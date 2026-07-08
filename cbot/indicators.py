"""기술적 지표 (거래소 무관).

입력은 항상 '과거→현재'로 정렬된 캔들이라고 가정한다(어댑터가 보장).
pandas 로 벡터화해서 계산한다 — 반복문보다 빠르고 트레이딩에서 표준적이다.

⚠️ 룩어헤드(look-ahead) 편향 주의
지표는 '그 시점까지의 정보'만으로 계산돼야 한다. 미래 캔들 값이 과거 시점의
지표에 새어들면 백테스트 성과는 환상이 된다. pandas 의 rolling/ewm 은 기본적으로
'과거→현재' 방향으로만 계산하므로 안전하다. 다만 Donchian '돌파' 신호처럼
현재 봉을 채널에서 제외해야 하는 경우는 strategy 에서 shift(1) 로 처리한다
(아래 donchian docstring 참고).
"""

from __future__ import annotations

import pandas as pd

from cbot.models import Candle


def candles_to_df(candles: list[Candle]) -> pd.DataFrame:
    """Candle 리스트를 지표 계산용 DataFrame 으로 변환한다.

    - index: timestamp (시간 오름차순)
    - columns: open, high, low, close, volume
    지표 함수들은 모두 이 DataFrame(또는 그 컬럼)을 입력으로 받는다.
    """
    df = pd.DataFrame(
        {
            "timestamp": [c.timestamp for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
        }
    )
    df = df.set_index("timestamp")
    return df


def ema(series: pd.Series, period: int) -> pd.Series:
    """지수이동평균(EMA).

    최근 값에 더 큰 가중치를 준다(단순평균 SMA 와 달리 최신 추세에 민감).
    adjust=False → 트레이딩에서 통용되는 재귀식 EMA:
        EMA_t = α·price_t + (1-α)·EMA_{t-1},  α = 2/(period+1)
    """
    if period <= 0:
        raise ValueError(f"period 는 1 이상이어야 함: {period}")
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """평균실체범위(Average True Range) — 변동성 지표.

    True Range(TR) = 다음 셋 중 최댓값:
        1) 당일 고가 - 당일 저가
        2) |당일 고가 - 전일 종가|   (갭 상승 반영)
        3) |당일 저가 - 전일 종가|   (갭 하락 반영)
    ATR = TR 의 Wilder 평활(RMA). 손절폭·포지션 사이징을 변동성에 비례시키는 데 쓴다.

    period 개의 캔들이 쌓이기 전(초반부)은 NaN/불안정할 수 있으니 전략에서 걸러 쓴다.
    """
    if period <= 0:
        raise ValueError(f"period 는 1 이상이어야 함: {period}")

    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)  # 전일 종가 (shift 로 '과거'만 참조 → 안전)

    # 세 후보를 열로 놓고 행별 최댓값
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder 평활 = ewm(alpha=1/period). ATR 의 전통적 정의.
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def donchian(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """돈치안 채널 — 최근 period 봉의 고가/저가 범위.

    upper: 최근 period 봉의 최고가
    lower: 최근 period 봉의 최저가

    ⚠️ 돌파(breakout) 신호에 쓸 때 주의:
    여기서 upper 는 '현재 봉 포함' 최고가다. 그래서 close 는 정의상 항상
    upper 이하라 'close > upper' 는 절대 참이 되지 않는다. '이전 period 봉을
    돌파했는가'를 보려면 strategy 에서 donchian(...).shift(1) 로 현재 봉을
    제외한 채널과 비교해야 한다. 이게 룩어헤드를 피하는 올바른 방법이다.
    """
    if period <= 0:
        raise ValueError(f"period 는 1 이상이어야 함: {period}")

    upper = df["high"].rolling(window=period).max()
    lower = df["low"].rolling(window=period).min()
    return pd.DataFrame({"upper": upper, "lower": lower})
