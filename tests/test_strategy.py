"""전략 테스트 — 이번에 고친 두 버그(자본 캡, 워밍업)를 테스트로 고정한다."""

import pandas as pd
import pytest

from cbot.strategy import TrendFollowingStrategy


def make_df(closes, spread=1.0):
    """종가 리스트로 합성 OHLCV 를 만든다 (고가=종가+1, 저가=종가-1)."""
    closes = [float(c) for c in closes]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + spread for c in closes],
            "low": [c - spread for c in closes],
            "close": closes,
            "volume": [1.0] * len(closes),
        },
        index=pd.date_range("2026-01-01", periods=len(closes), freq="4h"),
    )


def small_strategy():
    """테스트용 소형 파라미터 — 워밍업(=5봉)이 짧아 작은 데이터로 검증 가능."""
    return TrendFollowingStrategy(
        donchian_period=3, ema_fast=2, ema_slow=5, atr_period=3
    )


# ---- 진입 신호 ----


def test_rising_market_signals_only_after_warmup():
    strat = small_strategy()
    df = strat.prepare(make_df(range(100, 190, 3)))  # 꾸준한 상승장
    sig = strat.entry_signal(df)
    assert sig.iloc[: strat.warmup_bars].sum() == 0  # 워밍업 구간 신호 금지 (버그 수정 검증)
    assert sig.iloc[strat.warmup_bars :].sum() > 0  # 이후엔 돌파 신호 발생


def test_flat_market_no_signal():
    strat = small_strategy()
    df = strat.prepare(make_df([100] * 30))  # 횡보장 — 돌파가 없다
    assert strat.entry_signal(df).sum() == 0


# ---- 포지션 사이징 ----


def test_position_size_risks_exactly_one_percent():
    strat = TrendFollowingStrategy()
    capital, entry, atr_v = 10_000_000, 95_000_000, 1_200_000
    qty = strat.position_size(capital, entry, atr_v)
    loss_at_stop = qty * strat.atr_stop_mult * atr_v  # 손절까지 밀렸을 때 손실
    assert loss_at_stop == pytest.approx(capital * strat.risk_per_trade)


def test_position_size_capped_by_capital():
    # 저변동성(ATR 40만)이면 공식 수량의 매수금액이 자본을 넘는다 → 캡 발동 (버그 수정 검증)
    strat = TrendFollowingStrategy()
    capital, entry = 10_000_000, 95_000_000
    qty = strat.position_size(capital, entry, atr_value=400_000)
    assert qty * entry <= capital
    assert qty == pytest.approx(capital / entry)


def test_position_size_zero_below_min_order():
    # 자본 1만원 → 주문가치가 최소 주문 금액(5,000원) 미만 → 주문 불가
    strat = TrendFollowingStrategy()
    assert strat.position_size(10_000, 95_000_000, 1_200_000) == 0.0


# ---- 손절/트레일링 ----


def test_stop_levels():
    strat = TrendFollowingStrategy()
    assert strat.initial_stop(95_000_000, 1_200_000) == 92_600_000  # -2xATR
    assert strat.trailing_stop(100_000_000, 1_200_000) == 96_400_000  # 최고가-3xATR
