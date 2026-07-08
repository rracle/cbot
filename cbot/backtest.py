"""백테스트 엔진 (거래소 무관, 전략 무관).

봉을 하나씩 시간순으로 돌며 전략 규칙대로 모의 매매한다.
트레일링 스탑은 '진입 후 최고가'에 의존하는 경로 의존적 로직이라
벡터화가 안 되고, 이렇게 이벤트 루프로 상태를 추적해야 한다.

전략 인터페이스 (덕 타이핑 — 필수 + 선택 훅):
  필수: prepare(df), entry_signal(df), initial_stop(price, atr),
        position_size(capital, price, atr), warmup_bars
  선택: trailing_stop(peak, atr)   → 있으면 트레일링 (추세추종)
        exit_signal(df)            → 있으면 신호 청산 (평균회귀)
        max_hold_bars              → 있으면 시간 청산 (평균회귀)
  선택 훅은 정의한 전략에서만 동작한다 — 없는 전략은 그 로직을 건너뛴다.

체결 타이밍 규칙 (룩어헤드 방지의 핵심)
  - 신호(진입/청산)는 봉 t 의 '종가'에서 확정된다.
  - 체결은 봉 t+1 의 '시가'에서 일어난다.
  - 신호를 만든 봉의 가격으로 즉시 체결하면 미래를 본 것이다.

체결 비용 (CLAUDE.md 원칙: 반드시 포함)
  - 수수료 fee_rate: 매수/매도 양쪽에 부과 (Upbit 원화 마켓 0.05%)
  - 슬리피지 slippage_rate: 매수는 비싸게 x(1+s), 매도는 싸게 x(1-s)

봉 하나를 처리하는 순서 (순서가 곧 정확성이다)
  1)   시가: 전 봉 종가에서 예약된 진입이 있으면 체결
  1.5) 시가: 예약된 청산(신호/시간)이 있으면 체결 — 진입과 대칭
  2)   손절 검사: '이전 봉까지의 정보로 만든' 스탑을 사용.
       갭 하락이면 스탑이 아니라 (더 나쁜) 시가에 체결
  3)   종가: 트레일링 스탑을 위로만(ratchet) 갱신 — 반드시 2) 뒤에
  4)   종가: 진입/청산 신호 확인 → 다음 봉 시가 체결 예약
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass
class Trade:
    """청산까지 끝난 트레이드 한 건의 기록."""

    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    fees: float   # 매수+매도 수수료 합
    pnl: float    # 수수료 차감 후 순손익 (KRW)
    reason: str   # "stop" | "signal" | "time" | "end_of_data"

    @property
    def return_pct(self) -> float:
        """투입 금액 대비 수익률."""
        return self.pnl / (self.entry_price * self.quantity)


@dataclass
class BacktestResult:
    initial_capital: float
    final_equity: float
    trades: list[Trade]
    equity: pd.Series  # 봉마다의 평가자산 (현금 + 보유수량 x 종가)

    @property
    def total_return(self) -> float:
        return self.final_equity / self.initial_capital - 1

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t.pnl > 0) / len(self.trades)


class Backtester:
    """전략을 과거 데이터 위에서 실행한다. 전략 규칙은 strategy 가, 상태는 여기가 맡는다."""

    def __init__(
        self,
        strategy,
        initial_capital: float = 10_000_000,
        fee_rate: float = 0.0005,       # Upbit 원화 마켓 0.05%
        slippage_rate: float = 0.0005,  # 시장가 체결 미끄러짐 가정
    ) -> None:
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate

    def run(self, df: pd.DataFrame) -> BacktestResult:
        """candles_to_df() 결과(OHLCV DataFrame)를 받아 백테스트를 실행한다."""
        df = self.strategy.prepare(df)
        entry_signal = self.strategy.entry_signal(df)

        # 선택 훅: 전략이 정의했을 때만 켠다 (덕 타이핑)
        exit_signal = (
            self.strategy.exit_signal(df)
            if hasattr(self.strategy, "exit_signal")
            else None
        )
        has_trailing = hasattr(self.strategy, "trailing_stop")
        max_hold = getattr(self.strategy, "max_hold_bars", None)

        cash = self.initial_capital
        qty = 0.0                     # 보유 수량 (0 = 무포지션)
        entry_price = 0.0
        entry_time: datetime | None = None
        entry_fee = 0.0
        stop = 0.0                    # 현재 손절선
        peak = 0.0                    # 진입 후 최고가 (트레일링 기준)
        bars_held = 0                 # 진입 후 경과 봉 수 (시간 청산용)
        pending_entry = False         # '다음 봉 시가에 진입' 예약
        pending_exit: str | None = None  # None 아니면 '다음 봉 시가에 청산' (값=사유)

        trades: list[Trade] = []
        equity_curve: list[float] = []

        def close_position(raw_price: float, exit_time, reason: str) -> None:
            """청산 공통 처리 — 슬리피지·수수료 반영해 현금 정산, 트레이드 기록."""
            nonlocal cash, qty
            exit_price = raw_price * (1 - self.slippage_rate)  # 매도는 불리하게
            proceeds = qty * exit_price
            fee = proceeds * self.fee_rate
            cash += proceeds - fee
            trades.append(
                Trade(
                    entry_time=entry_time,
                    exit_time=exit_time,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=qty,
                    fees=entry_fee + fee,
                    pnl=(proceeds - fee) - (qty * entry_price + entry_fee),
                    reason=reason,
                )
            )
            qty = 0.0

        for i in range(len(df)):
            bar = df.iloc[i]

            # --- 1) 시가: 예약된 진입 체결 ---
            enter_now = pending_entry
            pending_entry = False
            if enter_now and qty == 0.0:
                # 체결 시점에 알 수 있는 정보 = 신호 봉(i-1)까지의 ATR
                atr_signal = df["atr"].iloc[i - 1]
                price = bar["open"] * (1 + self.slippage_rate)  # 매수는 불리하게
                if not pd.isna(atr_signal):
                    size = self.strategy.position_size(cash, price, atr_signal)
                    # 수수료까지 내고도 살 수 있는 수량으로 한 번 더 캡
                    size = min(size, cash / (price * (1 + self.fee_rate)))
                    if size > 0:
                        cost = size * price
                        fee = cost * self.fee_rate
                        cash -= cost + fee
                        qty = size
                        entry_price = price
                        entry_time = df.index[i]
                        entry_fee = fee
                        peak = price
                        bars_held = 0
                        stop = self.strategy.initial_stop(price, atr_signal)

            # --- 1.5) 시가: 예약된 청산 체결 (신호/시간 — 진입과 대칭) ---
            if qty > 0.0 and pending_exit is not None:
                close_position(bar["open"], df.index[i], pending_exit)
            pending_exit = None

            # --- 2) 손절 검사 (이전 봉까지의 정보로 만든 stop 사용) ---
            if qty > 0.0:
                if bar["open"] <= stop:
                    close_position(bar["open"], df.index[i], "stop")  # 갭 하락: 시가 체결
                elif bar["low"] <= stop:
                    close_position(stop, df.index[i], "stop")  # 봉 안에서 스탑 터치

            # --- 3) 종가: 트레일링 스탑 갱신 — 전략이 지원할 때만, 손절 검사 '뒤'에 ---
            if qty > 0.0 and has_trailing:
                peak = max(peak, bar["high"])
                atr_now = df["atr"].iloc[i]
                if not pd.isna(atr_now):
                    # max() = ratchet: 스탑은 올라가기만 하고 절대 내려오지 않는다
                    stop = max(stop, self.strategy.trailing_stop(peak, atr_now))

            # --- 4) 종가: 다음 봉 체결 예약 (진입 또는 청산) ---
            if qty == 0.0 and entry_signal.iloc[i]:
                pending_entry = True
            elif qty > 0.0:
                bars_held += 1
                if exit_signal is not None and bool(exit_signal.iloc[i]):
                    pending_exit = "signal"   # 목표 도달 (예: RSI 회복)
                elif max_hold is not None and bars_held >= max_hold:
                    pending_exit = "time"     # 기한 내 회복 실패 → 가정 철회

            # 봉마다 평가자산 기록 (현금 + 보유분 평가액)
            equity_curve.append(cash + qty * bar["close"])

        # --- 데이터 끝: 남은 포지션은 마지막 종가에 강제 청산 (통계 일관성) ---
        if qty > 0.0:
            close_position(df["close"].iloc[-1], df.index[-1], "end_of_data")
            equity_curve[-1] = cash

        return BacktestResult(
            initial_capital=self.initial_capital,
            final_equity=cash,
            trades=trades,
            equity=pd.Series(equity_curve, index=df.index, name="equity"),
        )
