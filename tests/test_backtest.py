"""백테스트 엔진 테스트 — 답을 손으로 계산할 수 있는 합성 시나리오로 검증한다.

검증하는 성질:
  1) 신호 봉이 아니라 '다음 봉 시가'에 체결된다 (룩어헤드 방지)
  2) 마지막 봉에서 난 신호는 체결될 수 없다
  3) 갭 하락 시 스탑 가격이 아니라 (더 나쁜) 시가에 청산된다
  4) 현금 정산이 수수료까지 포함해 1원 단위로 정확하다
"""

import pandas as pd
import pytest

from cbot.backtest import Backtester
from cbot.strategy import TrendFollowingStrategy


def make_df(closes, spread=1.0):
    closes = [float(c) for c in closes]
    return pd.DataFrame(
        {
            "open": closes,  # 단순화: 시가 = 종가 (검증 계산이 쉬워진다)
            "high": [c + spread for c in closes],
            "low": [c - spread for c in closes],
            "close": closes,
            "volume": [1.0] * len(closes),
        },
        index=pd.date_range("2026-01-01", periods=len(closes), freq="4h"),
    )


def small_strategy():
    return TrendFollowingStrategy(
        donchian_period=3, ema_fast=2, ema_slow=5, atr_period=3
    )


RISING = [100, 103, 106, 109, 112, 115, 118, 121, 124]  # 워밍업(5봉) 후 매 봉 돌파


def test_entry_fills_at_next_bar_open():
    # 첫 신호는 워밍업 직후인 5번 봉 종가 → 체결은 6번 봉 시가여야 한다
    bt = Backtester(small_strategy(), initial_capital=1_000_000,
                    fee_rate=0.0, slippage_rate=0.0)
    df = make_df(RISING)
    result = bt.run(df)

    assert len(result.trades) == 1  # 계속 상승 → 데이터 끝에서 강제 청산 1건
    t = result.trades[0]
    assert t.entry_time == df.index[6]   # 신호(5번 봉) 다음 봉
    assert t.entry_price == 118.0        # 6번 봉 시가 (슬리피지 0)
    assert t.reason == "end_of_data"


def test_signal_on_last_bar_never_fills():
    # 5번 봉(마지막)에서 신호가 떠도 체결할 '다음 봉'이 없다 → 트레이드 0건
    bt = Backtester(small_strategy(), initial_capital=1_000_000,
                    fee_rate=0.0, slippage_rate=0.0)
    result = bt.run(make_df(RISING[:6]))
    assert result.trades == []
    assert result.final_equity == 1_000_000  # 아무 일도 없었다


def test_gap_down_exits_at_open_not_stop():
    # 진입 후 급락(갭)이 스탑을 건너뛰면, 스탑 가격이 아니라 시가에 체결된다
    closes = RISING[:8] + [50]  # 8번 봉이 121 → 50 으로 폭락
    bt = Backtester(small_strategy(), initial_capital=1_000_000,
                    fee_rate=0.0, slippage_rate=0.0)
    df = make_df(closes)
    result = bt.run(df)

    (t,) = result.trades
    assert t.reason == "stop"
    assert t.exit_time == df.index[8]
    assert t.exit_price == 50.0  # 스탑(~110)이 아니라 갭 시가

    # 현금 정산 검증: 최종자산 = 초기자본 + 수량 x (청산가 - 진입가)
    prepared = small_strategy().prepare(df)
    atr_signal = prepared["atr"].iloc[5]  # 신호 봉의 ATR 로 사이징됐어야 함
    expected_qty = small_strategy().position_size(1_000_000, 118.0, atr_signal)
    assert t.quantity == pytest.approx(expected_qty)
    assert result.final_equity == pytest.approx(1_000_000 + expected_qty * (50.0 - 118.0))


def test_fees_and_slippage_accounting():
    # 수수료·슬리피지가 켜진 상태에서 현금 흐름이 공식대로 정확한지 1원 단위 검증
    fee, slip = 0.0005, 0.0005
    closes = RISING[:8] + [50]
    bt = Backtester(small_strategy(), initial_capital=1_000_000,
                    fee_rate=fee, slippage_rate=slip)
    result = bt.run(make_df(closes))

    (t,) = result.trades
    buy_price = 118.0 * (1 + slip)   # 매수는 비싸게
    sell_price = 50.0 * (1 - slip)   # 매도는 싸게
    assert t.entry_price == pytest.approx(buy_price)
    assert t.exit_price == pytest.approx(sell_price)

    cost = t.quantity * buy_price
    proceeds = t.quantity * sell_price
    expected_final = 1_000_000 - cost - cost * fee + proceeds - proceeds * fee
    assert result.final_equity == pytest.approx(expected_final)
    assert t.fees == pytest.approx(cost * fee + proceeds * fee)


def test_flat_market_no_trades():
    bt = Backtester(small_strategy(), initial_capital=1_000_000)
    result = bt.run(make_df([100] * 30))
    assert result.trades == []
    assert (result.equity == 1_000_000).all()  # 자산 변동 없음
