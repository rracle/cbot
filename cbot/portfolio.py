"""다종목 포트폴리오 백테스터 — 분산 추세추종.

여러 마켓에 같은 전략을 동시에 돌리며 '하나의 현금 풀'을 공유한다.
터틀 트레이더 방식: 같은 규칙, 여러 시장 → 베팅 횟수를 늘려 성과를 고르게.

단일 백테스터와의 차이:
  - 시간축: 전 마켓 봉의 합집합을 시간순으로 돈다
  - 리스크: 트레이드당 '포트폴리오 전체 평가자산'의 risk_per_trade
  - 현금 경합: 같은 봉에 신호가 겹치면 dfs 에 넣은 순서대로 선착순.
    현금이 부족하면 늦은 신호는 축소되거나 못 산다 (현물의 자연스러운 한도)

⚠️ 크립토 상관관계 경고: 코인들은 같이 움직인다. 동시 포지션 N개는
독립 베팅 N개가 아니라 '한 방향 베팅 ~N배'에 가깝다. 결과의
max_concurrent(최대 동시 포지션)를 반드시 확인하고 해석할 것.

지원 전략: 필수 인터페이스 + trailing_stop (추세추종류).
exit_signal/max_hold_bars 훅은 아직 미지원 — 필요해지면 확장.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from cbot.backtest import BacktestResult, Trade


@dataclass
class PortfolioResult(BacktestResult):
    """BacktestResult + 포트폴리오 고유 정보 (metrics.evaluate 그대로 사용 가능)."""

    max_concurrent: int = 0  # 동시에 들고 있던 최대 포지션 수 (집중 리스크 지표)


@dataclass
class _MarketState:
    """마켓 하나의 포지션 상태 (단일 백테스터의 지역변수들을 마켓별로 묶은 것)."""

    qty: float = 0.0
    entry_price: float = 0.0
    entry_time: datetime | None = None
    entry_fee: float = 0.0
    stop: float = 0.0
    peak: float = 0.0
    pending_entry: bool = False
    prev_atr: float = float("nan")  # 직전 봉의 ATR (진입 사이징용 — 신호 봉 값)
    last_close: float = 0.0         # 평가자산 계산용 최근 종가


class PortfolioBacktester:
    """단일 백테스터와 같은 봉 처리 규칙(1→2→3→4)을 마켓별 상태로 확장한다."""

    def __init__(
        self,
        strategy,
        initial_capital: float = 10_000_000,
        fee_rate: float = 0.0005,
        slippage_rate: float = 0.0005,
    ) -> None:
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate

    def run(self, dfs: dict[str, pd.DataFrame]) -> PortfolioResult:
        """dfs: {마켓: OHLCV DataFrame}. dict 순서가 현금 경합의 우선순위다."""
        markets = list(dfs)

        # 마켓별 지표·신호를 미리 계산하고, 속도를 위해 numpy 배열로 꺼내둔다
        cols = ("open", "high", "low", "close", "atr")
        arr: dict[str, dict[str, np.ndarray]] = {}
        sig: dict[str, np.ndarray] = {}
        index: dict[str, pd.DatetimeIndex] = {}
        pos_of: dict[str, dict] = {}  # 마켓별 timestamp → 행 번호
        for m in markets:
            p = self.strategy.prepare(dfs[m])
            arr[m] = {c: p[c].to_numpy() for c in cols}
            sig[m] = self.strategy.entry_signal(p).to_numpy()
            index[m] = p.index
            pos_of[m] = {ts: i for i, ts in enumerate(p.index)}

        # 합집합 시간축 (상장 시기가 달라도 각자 있는 봉만 처리)
        union = sorted(set().union(*(set(ix) for ix in index.values())))

        cash = self.initial_capital
        st = {m: _MarketState() for m in markets}
        trades: list[Trade] = []
        equity_curve: list[float] = []
        max_concurrent = 0

        def invested() -> float:
            return sum(s.qty * s.last_close for s in st.values())

        def close(m: str, raw_price: float, ts, reason: str) -> None:
            nonlocal cash
            s = st[m]
            exit_price = raw_price * (1 - self.slippage_rate)
            proceeds = s.qty * exit_price
            fee = proceeds * self.fee_rate
            cash += proceeds - fee
            trades.append(
                Trade(
                    entry_time=s.entry_time,
                    exit_time=ts,
                    entry_price=s.entry_price,
                    exit_price=exit_price,
                    quantity=s.qty,
                    fees=s.entry_fee + fee,
                    pnl=(proceeds - fee) - (s.qty * s.entry_price + s.entry_fee),
                    reason=reason,
                    market=m,
                )
            )
            s.qty = 0.0

        for ts in union:
            for m in markets:
                i = pos_of[m].get(ts)
                if i is None:
                    continue  # 이 마켓엔 이 시각의 봉이 없다 (상장 전 등)
                a = st[m]
                o, h, l, c = arr[m]["open"][i], arr[m]["high"][i], arr[m]["low"][i], arr[m]["close"][i]

                # --- 1) 시가: 예약된 진입 체결 (리스크 기준 = 포트폴리오 전체 자산) ---
                enter_now = a.pending_entry
                a.pending_entry = False
                if enter_now and a.qty == 0.0 and not np.isnan(a.prev_atr):
                    price = o * (1 + self.slippage_rate)
                    equity = cash + invested()
                    size = self.strategy.position_size(equity, price, a.prev_atr)
                    # 실제 지불 능력(현금)으로 최종 캡 — 여기서 선착순 경합이 일어난다
                    size = min(size, cash / (price * (1 + self.fee_rate)))
                    if size > 0:
                        cost = size * price
                        fee = cost * self.fee_rate
                        cash -= cost + fee
                        a.qty = size
                        a.entry_price = price
                        a.entry_time = ts
                        a.entry_fee = fee
                        a.peak = price
                        a.stop = self.strategy.initial_stop(price, a.prev_atr)

                # --- 2) 손절 검사 (이전 봉까지의 정보로 만든 stop) ---
                if a.qty > 0.0:
                    if o <= a.stop:
                        close(m, o, ts, "stop")  # 갭 하락: 시가 체결
                    elif l <= a.stop:
                        close(m, a.stop, ts, "stop")

                # --- 3) 종가: 트레일링 스탑 ratchet ---
                if a.qty > 0.0:
                    a.peak = max(a.peak, h)
                    atr_now = arr[m]["atr"][i]
                    if not np.isnan(atr_now):
                        a.stop = max(a.stop, self.strategy.trailing_stop(a.peak, atr_now))

                # --- 4) 종가: 진입 신호 예약 ---
                if a.qty == 0.0 and sig[m][i]:
                    a.pending_entry = True

                a.prev_atr = arr[m]["atr"][i]
                a.last_close = c

            equity_curve.append(cash + invested())
            max_concurrent = max(max_concurrent, sum(1 for s in st.values() if s.qty > 0))

        # --- 데이터 끝: 남은 포지션 강제 청산 ---
        for m in markets:
            if st[m].qty > 0.0:
                close(m, arr[m]["close"][-1], index[m][-1], "end_of_data")
        equity_curve[-1] = cash

        return PortfolioResult(
            initial_capital=self.initial_capital,
            final_equity=cash,
            trades=trades,
            equity=pd.Series(equity_curve, index=pd.DatetimeIndex(union), name="equity"),
            max_concurrent=max_concurrent,
        )
