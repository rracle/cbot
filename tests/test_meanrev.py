"""평균회귀 전략 + 백테스터 선택 훅(exit_signal/max_hold_bars) 테스트.

검증하는 성질:
  1) 상승 국면의 급락(과매도)에서 진입하고, RSI 회복 시 '신호 청산'한다
  2) 기한 내 회복이 없으면 '시간 청산'한다
  3) 폭락하면 하드 스탑이 잡는다
  4) 하락 추세(EMA 아래)에선 아무리 과매도여도 사지 않는다
  5) 선택 훅이 없는 추세추종의 동작은 변하지 않는다 (기존 스위트가 보증)
"""

import pandas as pd
import pytest

from cbot.backtest import Backtester
from cbot.strategy import MeanReversionStrategy


def make_df(closes, spread=1.0):
    closes = [float(c) for c in closes]
    return pd.DataFrame(
        {
            "open": closes,  # 단순화: 시가 = 종가
            "high": [c + spread for c in closes],
            "low": [c - spread for c in closes],
            "close": closes,
            "volume": [1.0] * len(closes),
        },
        index=pd.date_range("2026-01-01", periods=len(closes), freq="4h"),
    )


def small_strategy(**overrides):
    """테스트용 소형 파라미터 (워밍업 5봉)."""
    params = dict(
        rsi_period=2, rsi_entry=45.0, rsi_exit=60.0,
        ema_trend=5, atr_period=3, atr_stop_mult=3.0, max_hold_bars=3,
    )
    params.update(overrides)
    return MeanReversionStrategy(**params)


def make_bt(strategy):
    return Backtester(strategy, initial_capital=1_000_000, fee_rate=0.0, slippage_rate=0.0)


UPTREND = [100 + 10 * i for i in range(10)]  # 100..190 강한 상승 (EMA 위 유지)


def test_dip_entry_then_signal_exit():
    # 상승 → 2봉 급락(과매도) → 반등(RSI 회복) → 신호 청산으로 이익 실현
    closes = UPTREND + [185, 180, 190, 200, 210]
    result = make_bt(small_strategy()).run(make_df(closes))

    trade = result.trades[0]
    assert trade.reason == "signal"
    assert trade.pnl > 0            # 눌림목에 사서 반등에 팔았다
    assert result.final_equity > 1_000_000


def test_no_recovery_time_exit():
    # 급락 후 횡보(회복 없음) → max_hold_bars(3봉) 뒤 시간 청산.
    # 횡보가 계속되면 RSI 가 저점에 머물러 재진입할 수 있다(정당한 동작)
    # → '첫' 트레이드가 시간 청산인지 확인한다.
    closes = UPTREND + [185, 180] + [180] * 6
    result = make_bt(small_strategy()).run(make_df(closes))

    assert len(result.trades) >= 1
    assert result.trades[0].reason == "time"


def test_crash_hits_hard_stop():
    # 진입 체결(182) 후 '보유 중' 폭락(100) → 하드 스탑(3xATR)이 재난을 차단
    closes = UPTREND + [185, 180, 182, 100, 100]
    result = make_bt(small_strategy()).run(make_df(closes))

    (trade,) = result.trades
    assert trade.reason == "stop"
    assert trade.pnl < 0


def test_never_buys_in_downtrend():
    # 하락 추세(종가 < EMA)에선 과매도가 계속돼도 매수하지 않는다
    closes = [200 - 8 * i for i in range(20)]  # 일방적 하락
    result = make_bt(small_strategy()).run(make_df(closes))
    assert result.trades == []
