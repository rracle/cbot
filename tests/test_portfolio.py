"""포트폴리오 백테스터 테스트.

가장 중요한 성질은 '동등성': 마켓 1개짜리 포트폴리오는 단일 백테스터와
결과가 완전히 같아야 한다 — 새 엔진의 회계가 정확하다는 가장 강한 증거.
"""

import pandas as pd
import pytest

from cbot.backtest import Backtester
from cbot.portfolio import PortfolioBacktester
from cbot.strategy import TrendFollowingStrategy


def make_df(closes, spread=1.0):
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


def small_strategy(**overrides):
    params = dict(donchian_period=3, ema_fast=2, ema_slow=5, atr_period=3)
    params.update(overrides)
    return TrendFollowingStrategy(**params)


RISING = [100, 103, 106, 109, 112, 115, 118, 121, 124]
GAP_DOWN = RISING[:8] + [50]  # 진입 후 폭락 (단일 백테스터 테스트와 동일 시나리오)


def test_single_market_portfolio_equals_backtester():
    # 마켓 1개 포트폴리오 == 단일 백테스터 (진입/손절/수수료/강제청산 전부)
    for closes in (RISING, GAP_DOWN, [100] * 30):
        df = make_df(closes)
        single = Backtester(small_strategy(), initial_capital=1_000_000).run(df)
        port = PortfolioBacktester(small_strategy(), initial_capital=1_000_000).run(
            {"KRW-BTC": df}
        )
        assert port.final_equity == pytest.approx(single.final_equity)
        assert len(port.trades) == len(single.trades)
        assert port.equity.tolist() == pytest.approx(single.equity.tolist())


def test_only_signaling_market_trades():
    # A는 상승(신호), B는 횡보(무신호) → 트레이드는 전부 A에서만
    result = PortfolioBacktester(small_strategy(), initial_capital=1_000_000).run(
        {"A": make_df(GAP_DOWN), "B": make_df([100] * len(GAP_DOWN))}
    )
    assert len(result.trades) == 1
    assert result.trades[0].market == "A"


def test_cash_is_shared_first_come_first_served():
    # 리스크 5% → 포지션 하나가 자본의 ~74% 를 차지
    # → 같은 봉에 신호가 겹치면 두 번째 마켓은 남은 현금(~26%)만큼만 산다
    strat = small_strategy(risk_per_trade=0.05)
    result = PortfolioBacktester(strat, initial_capital=1_000_000).run(
        {"A": make_df(RISING), "B": make_df(RISING)}
    )

    entries = {t.market: t for t in result.trades}
    assert set(entries) == {"A", "B"}  # 둘 다 진입은 했고
    total_cost = sum(t.entry_price * t.quantity for t in result.trades)
    assert total_cost <= 1_000_000  # 현금 풀을 초과해 사지 않았다
    # 선착순: 먼저 처리된 A가 더 크게 (B는 남은 현금으로 축소됨)
    assert entries["A"].quantity * entries["A"].entry_price > \
           entries["B"].quantity * entries["B"].entry_price


def test_max_concurrent_positions_tracked():
    result = PortfolioBacktester(small_strategy(), initial_capital=1_000_000).run(
        {"A": make_df(RISING), "B": make_df(RISING)}
    )
    assert result.max_concurrent == 2  # 두 마켓 동시 보유 순간이 있었다


def test_different_history_lengths():
    # B가 늦게 상장(짧은 역사)해도 합집합 시간축에서 각자 봉만 처리된다
    long_df = make_df(GAP_DOWN + [50] * 5)
    short_closes = RISING[:6]
    short_df = make_df(short_closes)
    # B의 인덱스를 뒤쪽 시간대로 이동 (늦은 상장 시뮬레이션)
    short_df.index = long_df.index[-len(short_closes):]

    result = PortfolioBacktester(small_strategy(), initial_capital=1_000_000).run(
        {"A": long_df, "B": short_df}
    )
    assert len(result.equity) == len(long_df)  # 합집합 = 긴 쪽
