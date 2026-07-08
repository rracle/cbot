"""지표 테스트 — 손으로 답을 계산할 수 있는 작은 데이터로 성질을 고정한다."""

from datetime import datetime

import pandas as pd
import pytest

from cbot.indicators import atr, candles_to_df, donchian, ema, rsi
from cbot.models import Candle


def test_candles_to_df_basic():
    candles = [
        Candle(datetime(2026, 1, 1, 0), 1.0, 2.0, 0.5, 1.5, 10.0),
        Candle(datetime(2026, 1, 1, 4), 1.5, 3.0, 1.0, 2.5, 20.0),
    ]
    df = candles_to_df(candles)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index[0] < df.index[1]  # 시간 오름차순
    assert df["close"].tolist() == [1.5, 2.5]


def test_ema_constant_series_stays_constant():
    # 값이 5로 일정하면 어떤 평균이든 5여야 한다
    s = pd.Series([5.0] * 10)
    assert (ema(s, 3) == 5.0).all()


def test_atr_constant_range():
    # 매 봉 고가-저가=10, 갭 없음 → TR 은 항상 10 → ATR 도 10
    df = pd.DataFrame(
        {"high": [105.0] * 10, "low": [95.0] * 10, "close": [100.0] * 10}
    )
    assert atr(df, period=3).iloc[-1] == pytest.approx(10.0)


def test_rsi_extremes():
    # 계속 오르기만 하면 100 에, 계속 내리기만 하면 0 에 접근해야 한다
    up = pd.Series(range(1, 30), dtype=float)
    assert rsi(up, 14).iloc[-1] > 90
    down = pd.Series(range(30, 1, -1), dtype=float)
    assert rsi(down, 14).iloc[-1] < 10


def test_rsi_bounded_0_100():
    s = pd.Series([100, 105, 95, 110, 90, 120, 80, 130], dtype=float)
    r = rsi(s, 3).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_donchian_rolling_extremes():
    df = pd.DataFrame(
        {"high": [1.0, 2.0, 3.0, 2.0, 1.0], "low": [0.5, 1.5, 2.5, 1.5, 0.5]}
    )
    dc = donchian(df, period=3)
    assert dc["upper"].iloc[2:].tolist() == [3.0, 3.0, 3.0]
    assert dc["lower"].iloc[2:].tolist() == [0.5, 1.5, 0.5]
    assert dc["upper"].iloc[:2].isna().all()  # 봉이 모자란 초반은 NaN
