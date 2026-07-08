"""백테스트 엔진 (거래소 무관).

봉을 하나씩 시간순으로 돌며 전략 규칙대로 모의 매매한다.
트레일링 스탑은 '진입 후 최고가'에 의존하는 경로 의존적 로직이라
벡터화가 안 되고, 이렇게 이벤트 루프로 상태를 추적해야 한다.

체결 타이밍 규칙 (룩어헤드 방지의 핵심)
  - 신호는 봉 t 의 '종가'에서 확정된다.
  - 체결은 봉 t+1 의 '시가'에서 일어난다.
  - 신호를 만든 봉의 가격으로 즉시 체결하면 미래를 본 것이다.

체결 비용 (CLAUDE.md 원칙: 반드시 포함)
  - 수수료 fee_rate: 매수/매도 양쪽에 부과 (Upbit 원화 마켓 0.05%)
  - 슬리피지 slippage_rate: 시장가는 호가를 먹으며 불리하게 체결된다
    → 매수는 비싸게 x(1+s), 매도는 싸게 x(1-s)

봉 하나를 처리하는 순서 (순서가 곧 정확성이다)
  1) 시가: 전 봉 종가에서 예약된 진입이 있으면 체결
  2) 손절 검사: '이전 봉까지의 정보로 만든' 스탑을 사용.
     - 시가가 이미 스탑 아래면(갭 하락) 스탑이 아니라 시가에 체결 — 현실적
     - 아니면 저가가 스탑을 건드렸으면 스탑 가격에 체결
  3) 종가: 최고가 갱신 → 트레일링 스탑을 위로만(ratchet) 갱신.
     현재 봉 고가로 올린 스탑을 같은 봉 청산에 쓰면 그것도 미래 참조라,
     스탑 갱신은 반드시 손절 검사 '뒤'에 한다.
  4) 종가: 무포지션이고 진입 신호면 다음 봉 시가 체결을 예약
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from cbot.strategy import TrendFollowingStrategy


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
    reason: str   # "stop"(손절/트레일링) | "end_of_data"(데이터 끝 강제 청산)

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
        strategy: TrendFollowingStrategy,
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

        cash = self.initial_capital
        qty = 0.0                 # 보유 수량 (0 = 무포지션)
        entry_price = 0.0
        entry_time: datetime | None = None
        entry_fee = 0.0
        stop = 0.0                # 현재 손절선
        peak = 0.0                # 진입 후 최고가 (트레일링 기준)
        pending_entry = False     # '다음 봉 시가에 진입' 예약

        trades: list[Trade] = []
        equity_curve: list[float] = []

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
                        stop = self.strategy.initial_stop(price, atr_signal)

            # --- 2) 손절 검사 (이전 봉까지의 정보로 만든 stop 사용) ---
            if qty > 0.0:
                if bar["open"] <= stop:
                    exit_price = bar["open"]   # 갭 하락: 스탑보다 나쁜 시가에 체결
                elif bar["low"] <= stop:
                    exit_price = stop          # 봉 안에서 스탑 터치
                else:
                    exit_price = None

                if exit_price is not None:
                    exit_price *= 1 - self.slippage_rate  # 매도는 불리하게
                    proceeds = qty * exit_price
                    fee = proceeds * self.fee_rate
                    cash += proceeds - fee
                    trades.append(
                        Trade(
                            entry_time=entry_time,
                            exit_time=df.index[i],
                            entry_price=entry_price,
                            exit_price=exit_price,
                            quantity=qty,
                            fees=entry_fee + fee,
                            pnl=(proceeds - fee) - (qty * entry_price + entry_fee),
                            reason="stop",
                        )
                    )
                    qty = 0.0

            # --- 3) 종가: 트레일링 스탑 갱신 (손절 검사 '뒤'에!) ---
            if qty > 0.0:
                peak = max(peak, bar["high"])
                atr_now = df["atr"].iloc[i]
                if not pd.isna(atr_now):
                    # max() = ratchet: 스탑은 올라가기만 하고 절대 내려오지 않는다
                    stop = max(stop, self.strategy.trailing_stop(peak, atr_now))

            # --- 4) 종가: 진입 신호면 다음 봉 체결 예약 ---
            if qty == 0.0 and entry_signal.iloc[i]:
                pending_entry = True

            # 봉마다 평가자산 기록 (현금 + 보유분 평가액)
            equity_curve.append(cash + qty * bar["close"])

        # --- 데이터 끝: 남은 포지션은 마지막 종가에 강제 청산 (통계 일관성) ---
        if qty > 0.0:
            exit_price = df["close"].iloc[-1] * (1 - self.slippage_rate)
            proceeds = qty * exit_price
            fee = proceeds * self.fee_rate
            cash += proceeds - fee
            trades.append(
                Trade(
                    entry_time=entry_time,
                    exit_time=df.index[-1],
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=qty,
                    fees=entry_fee + fee,
                    pnl=(proceeds - fee) - (qty * entry_price + entry_fee),
                    reason="end_of_data",
                )
            )
            qty = 0.0
            equity_curve[-1] = cash

        return BacktestResult(
            initial_capital=self.initial_capital,
            final_equity=cash,
            trades=trades,
            equity=pd.Series(equity_curve, index=df.index, name="equity"),
        )
