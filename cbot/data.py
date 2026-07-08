"""캔들 로컬 캐시.

백테스트를 돌릴 때마다 같은 과거 데이터를 API 로 다시 받는 건 느리고
rate limit 낭비다. 받은 캔들을 CSV 로 저장해 두고, 다음부터는
'캐시 마지막 이후의 새 캔들'만 추가로 받아 이어붙인다.

- 파일 위치: data/{거래소}_{마켓}_{unit}m.csv  (data/ 는 .gitignore 대상)
- 이 모듈은 Exchange '프로토콜'에만 의존한다 → 거래소가 뭐든 동작한다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from cbot.exchanges.base import Exchange
from cbot.models import Candle


def get_candles_cached(
    exchange: Exchange,
    market: str,
    unit: int = 240,
    count: int = 200,
    data_dir: str = "data",
) -> list[Candle]:
    """캐시를 활용해 캔들 count 개를 과거→현재 순으로 반환한다.

    동작:
      1) 캐시 파일이 있으면 읽는다.
      2) 캐시 마지막 캔들 이후 '빠진 개수'를 시간 간격으로 추산해 그만큼만 API 요청.
         (+1 개 여유: 마지막 캐시 봉이 저장 당시 '진행 중'이었을 수 있어 다시 받아 갱신)
      3) timestamp 기준으로 병합(새 데이터가 이김) 후 저장.
      4) 그래도 count 에 못 미치면(과거 방향 부족) count 만큼 통째로 받아 병합.
    """
    path = Path(data_dir) / f"{exchange.name}_{market}_{unit}m.csv"
    cached = _load(path)
    merged: dict[datetime, Candle] = {c.timestamp: c for c in cached}

    if cached:
        now = datetime.now(timezone.utc).replace(tzinfo=None)  # 캔들과 같은 naive UTC
        gap_seconds = (now - cached[-1].timestamp).total_seconds()
        missing = max(int(gap_seconds // (unit * 60)) + 1, 1)
        fresh = exchange.get_candles(market, unit=unit, count=missing)
        for c in fresh:
            merged[c.timestamp] = c  # 새 데이터가 캐시를 덮어씀(미완성 봉 갱신)
    else:
        for c in exchange.get_candles(market, unit=unit, count=count):
            merged[c.timestamp] = c

    candles = sorted(merged.values(), key=lambda c: c.timestamp)

    # 과거 방향으로 모자라면 필요한 만큼 통째로 받는다 (단순하고 확실한 방법)
    if len(candles) < count:
        for c in exchange.get_candles(market, unit=unit, count=count):
            merged.setdefault(c.timestamp, c)  # 이미 있는 최신 데이터는 유지
        candles = sorted(merged.values(), key=lambda c: c.timestamp)

    _save(path, candles)
    return candles[-count:]


# ----------------------------------------------------------------------
# 저장/로드 (CSV — 사람이 열어볼 수 있어 디버깅에 좋다)
# ----------------------------------------------------------------------


def _load(path: Path) -> list[Candle]:
    if not path.exists():
        return []
    df = pd.read_csv(path, parse_dates=["timestamp"])
    return [
        Candle(
            timestamp=row.timestamp.to_pydatetime(),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in df.itertuples()
    ]


def _save(path: Path, candles: list[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "timestamp": [c.timestamp for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
        }
    ).to_csv(path, index=False)
