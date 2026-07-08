"""성과 지표 테스트 — 답을 손으로 계산할 수 있는 데이터로 각 지표를 고정한다."""

from datetime import datetime

import pandas as pd
import pytest

from cbot.backtest import Trade
from cbot.metrics import cagr, exposure, max_drawdown, payoff_ratio, profit_factor, sharpe_ratio


def make_trade(pnl, entry="2026-01-01", exit="2026-01-02"):
    """지표 계산에 필요한 필드만 의미 있는 가짜 Trade."""
    return Trade(
        entry_time=datetime.fromisoformat(entry),
        exit_time=datetime.fromisoformat(exit),
        entry_price=100.0,
        exit_price=100.0,
        quantity=1.0,
        fees=0.0,
        pnl=pnl,
        reason="stop",
    )


def test_cagr_double_in_exactly_one_year():
    # 정확히 365일 동안 2배 → CAGR = 100%
    idx = pd.to_datetime(["2025-01-01", "2026-01-01"])
    assert cagr(pd.Series([100.0, 200.0], index=idx)) == pytest.approx(1.0)


def test_cagr_double_in_half_year_annualizes_up():
    # 반년에 2배면 연율화 시 4배 속도 → CAGR = 300%
    idx = pd.to_datetime(["2025-01-01", "2025-07-02"])  # 182.5일
    assert cagr(pd.Series([100.0, 200.0], index=idx)) == pytest.approx(3.0, rel=0.01)


def test_max_drawdown_known_path():
    # 고점 120 → 저점 60 = -50% 가 최대 낙폭 (이후 회복해도 기록은 남는다)
    idx = pd.date_range("2026-01-01", periods=4, freq="D")
    equity = pd.Series([100.0, 120.0, 60.0, 90.0], index=idx)
    assert max_drawdown(equity) == pytest.approx(-0.5)


def test_sharpe_zero_when_flat():
    # 자산 변동이 없으면 std=0 → 0 반환 (0 나누기 방어)
    idx = pd.date_range("2026-01-01", periods=10, freq="4h")
    assert sharpe_ratio(pd.Series([100.0] * 10, index=idx)) == 0.0


def test_sharpe_positive_for_steady_growth():
    # 꾸준히 오르기만 하는 자산은 Sharpe 가 크게 양수여야 한다
    idx = pd.date_range("2026-01-01", periods=100, freq="4h")
    equity = pd.Series([100.0 * 1.01**i for i in range(100)], index=idx)
    assert sharpe_ratio(equity) > 2.0


def test_profit_factor_and_payoff():
    trades = [make_trade(100.0), make_trade(200.0), make_trade(-100.0)]
    assert profit_factor(trades) == pytest.approx(3.0)   # 총이익 300 / 총손실 100
    assert payoff_ratio(trades) == pytest.approx(1.5)    # 평균이익 150 / 평균손실 100


def test_profit_factor_no_losses_is_inf():
    assert profit_factor([make_trade(100.0)]) == float("inf")
    assert profit_factor([]) == 0.0


def test_exposure_half_of_span():
    # 전체 4일 중 2일 보유 → 노출 50%
    idx = pd.date_range("2026-01-01", periods=5, freq="D")  # 1/1~1/5 (4일 span)
    equity = pd.Series([100.0] * 5, index=idx)
    trades = [make_trade(0.0, entry="2026-01-01", exit="2026-01-03")]
    assert exposure(trades, equity) == pytest.approx(0.5)
