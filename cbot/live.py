"""실시간 트레이딩 엔진 (페이퍼/실전 공용).

백테스트와 '같은 규칙'을 실시간으로 실행한다. 거래소는 Exchange 프로토콜로만
다루므로 PaperExchange 를 꽂으면 페이퍼 트레이딩, 인증된 실전 어댑터를 꽂으면
실전이 된다 — 엔진 코드는 한 글자도 바뀌지 않는다.

백테스트와의 대응 (동작 일치가 페이퍼 트레이딩의 핵심 목적):
  백테스트                          실시간(여기)
  ---------------------------------------------------------------
  신호 봉 '종가'에서 확정            봉 마감 직후, 마감 봉들로 신호 계산
  다음 봉 '시가'에 체결              마감 직후 시장가 주문 (같은 순간)
  봉 low 로 인트라바 손절            폴링마다 현재가로 손절 감시 (더 촘촘)
  종가 후 트레일링 ratchet           마감 봉 처리 시 ratchet

⚠️ 진행 중인 봉은 절대 신호에 쓰지 않는다. 마감 전 봉의 값은 계속 변하므로,
그걸로 신호를 내면 실시간판 룩어헤드가 된다. (timestamp + unit <= now 인
봉만 '마감'으로 취급)

상태(포지션·스탑·마지막 처리 봉)는 JSON 으로 영속화한다
→ 프로세스가 죽었다 살아나도 포지션을 기억한다.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cbot.exchanges.base import Exchange
from cbot.indicators import candles_to_df
from cbot.models import Candle, Order, OrderSide, OrderStatus, OrderType
from cbot.strategy import TrendFollowingStrategy


def _utcnow() -> datetime:
    """캔들 타임스탬프와 같은 naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class EngineState:
    """재시작 후에도 기억해야 하는 모든 것."""

    last_processed: str | None = None  # 마지막으로 처리한 마감 봉 시각 (ISO)
    qty: float = 0.0                   # 보유 수량 (0 = 무포지션)
    entry_price: float = 0.0
    stop: float = 0.0
    peak: float = 0.0                  # 진입 후 최고가 (트레일링 기준)
    trades: list[dict] = field(default_factory=list)  # 체결 로그


class LiveEngine:
    def __init__(
        self,
        exchange: Exchange,
        strategy: TrendFollowingStrategy,
        market: str,
        unit: int = 240,
        state_path: str | None = None,
    ) -> None:
        self.exchange = exchange
        self.strategy = strategy
        self.market = market
        self.unit = unit
        self._state_path = Path(state_path or f"data/live_{market}_{unit}m.json")

        if self._state_path.exists():  # 재시작: 포지션·스탑 복원
            self.state = EngineState(**json.loads(self._state_path.read_text()))
            self._log(f"상태 복원: qty={self.state.qty}, stop={self.state.stop:,.0f}")
        else:
            self.state = EngineState()

    # ------------------------------------------------------------------
    # 한 번의 점검 — run_forever 가 주기적으로 호출
    # ------------------------------------------------------------------

    def step(self) -> None:
        now = _utcnow()

        # --- 1) 손절 감시: 포지션이 있으면 '매 폴마다' 현재가로 확인 ---
        # (백테스트의 인트라바 low 검사에 대응 — 실시간이 오히려 더 촘촘하다)
        if self.state.qty > 0:
            price = self.exchange.get_current_price(self.market)
            if price <= self.state.stop:
                self._sell(reason="stop")

        # --- 2) 봉 마감 처리: 새로 마감된 봉이 있을 때 '한 번만' ---
        # ⚠️ EMA 워밍업: 재귀식 EMA(adjust=False)는 span 의 ~5배 데이터가 있어야
        # 수렴한다. warmup_bars(=ema_slow) 만큼만 로드하면 EMA200 이 덜 데워져
        # 과소평가되고, 백테스트가 내지 않을 진입 신호를 실시간에서 오발하게 된다
        # (실측: 260개 로드 시 EMA200 이 ~1% 과소 → 조기 진입 버그).
        # ema_slow 의 5배를 로드해 백테스트와 EMA 값을 일치시킨다.
        candles = self.exchange.get_candles(
            self.market, unit=self.unit,
            count=max(self.strategy.warmup_bars * 5, 260),
        )
        closed = self._closed_only(candles, now)
        if closed:
            last_ts = closed[-1].timestamp.isoformat()
            if self.state.last_processed is None or last_ts > self.state.last_processed:
                self._on_bar_close(closed)
                self.state.last_processed = last_ts

        self._save()

    def run_forever(self, poll_seconds: int = 60) -> None:
        """폴링 루프. 예외가 나도 죽지 않고 다음 폴에서 재시도한다 (장애 내성)."""
        self._log(f"엔진 시작: {self.market} {self.unit}분봉, {poll_seconds}초 폴링")
        while True:
            try:
                self.step()
            except Exception as e:  # noqa: BLE001 — 무슨 일이 있어도 루프는 산다
                self._log(f"오류(다음 폴에서 재시도): {type(e).__name__}: {e}")
            time.sleep(poll_seconds)

    # ------------------------------------------------------------------
    # 내부 로직
    # ------------------------------------------------------------------

    def _closed_only(self, candles: list[Candle], now: datetime) -> list[Candle]:
        """마감된 봉만 남긴다. 진행 중인 봉으로 신호를 내면 룩어헤드다."""
        span = timedelta(minutes=self.unit)
        return [c for c in candles if c.timestamp + span <= now]

    def _on_bar_close(self, closed: list[Candle]) -> None:
        """봉 마감 직후 한 번 실행 — 백테스트의 3)트레일링 갱신, 4)신호 판단에 대응."""
        df = self.strategy.prepare(candles_to_df(closed))
        bar = df.iloc[-1]

        if self.state.qty > 0:
            # 백테스트 3단계와 동일: peak 갱신 → 트레일링 스탑은 위로만(ratchet)
            self.state.peak = max(self.state.peak, float(bar["high"]))
            atr_now = float(bar["atr"])
            if atr_now > 0:
                new_stop = max(self.state.stop, self.strategy.trailing_stop(self.state.peak, atr_now))
                if new_stop != self.state.stop:
                    self._log(f"트레일링 스탑 {self.state.stop:,.0f} → {new_stop:,.0f}")
                self.state.stop = new_stop
        elif bool(self.strategy.entry_signal(df).iloc[-1]):
            self._buy(atr_signal=float(bar["atr"]))

        # 하트비트: 봉 처리 사실을 항상 기록한다 — 이벤트 없는 '건강한 침묵'과
        # '조용한 정지'를 로그만으로 구분할 수 있게 (관측성)
        if self.state.qty > 0:
            status = f"보유 {self.state.qty:.8f} (스탑 {self.state.stop:,.0f})"
        else:
            status = "무포지션 (신호 없음)"
        self._log(
            f"봉 처리: {closed[-1].timestamp:%m-%d %H:%M} 종가 {float(bar['close']):,.0f} — {status}"
        )

    def _buy(self, atr_signal: float) -> None:
        quote = self.market.split("-")[0]
        capital = self.exchange.get_balance(quote).available
        price = self.exchange.get_current_price(self.market)
        # 수수료+슬리피지(왕복 ~0.2%) 낼 몫을 남기려고 자본의 99.7%만 사이징에 사용
        size = self.strategy.position_size(capital * 0.997, price, atr_signal)
        if size <= 0:
            return

        order = self.exchange.place_order(
            Order(market=self.market, side=OrderSide.BUY, type=OrderType.MARKET, volume=size)
        )
        if order.status != OrderStatus.FILLED:
            self._log(f"매수 거부됨: {order.status}")
            return

        # 진실은 거래소 응답 — 요청값이 아니라 체결값으로 상태를 기록한다
        self.state.qty = order.filled_volume
        self.state.entry_price = order.filled_price
        self.state.peak = order.filled_price
        self.state.stop = self.strategy.initial_stop(order.filled_price, atr_signal)
        self._record("buy", order, reason="entry")
        self._log(
            f"매수 체결: {order.filled_volume:.8f} @ {order.filled_price:,.0f}"
            f" (손절선 {self.state.stop:,.0f})"
        )

    def _sell(self, reason: str) -> None:
        order = self.exchange.place_order(
            Order(market=self.market, side=OrderSide.SELL, type=OrderType.MARKET, volume=self.state.qty)
        )
        if order.status != OrderStatus.FILLED:
            self._log(f"매도 거부됨: {order.status} — 다음 폴에서 재시도")
            return

        pnl = (order.filled_price - self.state.entry_price) * order.filled_volume
        self._record("sell", order, reason=reason, pnl=pnl)
        self._log(f"매도 체결({reason}): @ {order.filled_price:,.0f}, 손익 {pnl:+,.0f}")
        self.state.qty = 0.0
        self.state.entry_price = 0.0
        self.state.stop = 0.0
        self.state.peak = 0.0

    # ------------------------------------------------------------------
    # 기록
    # ------------------------------------------------------------------

    def _record(self, side: str, order: Order, reason: str, pnl: float | None = None) -> None:
        self.state.trades.append(
            {
                "time": _utcnow().isoformat(timespec="seconds"),
                "side": side,
                "price": order.filled_price,
                "qty": order.filled_volume,
                "fee": order.fee,
                "reason": reason,
                "pnl": pnl,
            }
        )

    def _save(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(asdict(self.state), indent=2))

    def _log(self, msg: str) -> None:
        print(f"[{_utcnow():%Y-%m-%d %H:%M:%S} UTC] {self.market} {msg}", flush=True)
