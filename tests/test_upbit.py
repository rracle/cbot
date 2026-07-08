"""Upbit 어댑터 테스트 — 실제 API 를 부르지 않고 '가짜 Session'으로 검증한다.

네트워크에 의존하는 테스트는 느리고 불안정하다(장 상황 따라 결과가 바뀜).
requests.Session 자리에 미리 짜둔 응답을 돌려주는 가짜를 꽂아서,
재시도·페이지네이션·필드 매핑 같은 '우리 로직'만 격리해 검증한다.
"""

import pytest
import requests

import cbot.exchanges.upbit as upbit_mod
from cbot.exchanges.upbit import UpbitExchange


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """정해둔 응답을 순서대로 돌려주고, 요청 내역을 기록한다."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []  # (url, params) 기록

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self._responses.pop(0)


def candle_row(iso, close=100.0):
    """Upbit 캔들 API 응답 1건 모양의 dict."""
    return {
        "candle_date_time_utc": iso,
        "opening_price": close - 1,
        "high_price": close + 2,
        "low_price": close - 2,
        "trade_price": close,
        "candle_acc_trade_volume": 7.5,
    }


def make_exchange(responses):
    up = UpbitExchange(request_delay=0)
    up._session = FakeSession(responses)
    return up


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """재시도 백오프의 sleep 을 없애 테스트를 즉시 끝낸다."""
    monkeypatch.setattr("time.sleep", lambda s: None)


# ---- 재시도 ----


def test_retry_on_429_then_success():
    up = make_exchange(
        [FakeResponse(429), FakeResponse(429), FakeResponse(200, [{"trade_price": 12345.0}])]
    )
    assert up.get_current_price("KRW-BTC") == 12345.0
    assert len(up._session.calls) == 3  # 2번 실패 후 3번째 성공


def test_retry_gives_up_after_max_retries():
    up = make_exchange([FakeResponse(429)] * 4)  # max_retries=3 → 총 4회 시도 후 포기
    with pytest.raises(requests.HTTPError):
        up.get_current_price("KRW-BTC")


def test_no_retry_on_404():
    up = make_exchange([FakeResponse(404)])
    with pytest.raises(requests.HTTPError):
        up.get_current_price("KRW-BTC")
    assert len(up._session.calls) == 1  # 재시도 없이 즉시 실패


# ---- 필드 매핑 / 페이지네이션 ----


def test_candle_field_mapping():
    up = make_exchange([FakeResponse(200, [candle_row("2026-07-08T08:00:00")])])
    (c,) = up.get_candles("KRW-BTC", unit=240, count=1)
    assert (c.open, c.high, c.low, c.close, c.volume) == (99.0, 102.0, 98.0, 100.0, 7.5)
    assert c.timestamp.isoformat() == "2026-07-08T08:00:00"


def test_pagination_merges_ascending(monkeypatch):
    # 한 요청당 3개로 제한하면 5개 요청 시 3+2 두 페이지가 된다
    monkeypatch.setattr(upbit_mod, "MAX_COUNT_PER_REQUEST", 3)
    page1 = [candle_row(f"2026-07-08T{h:02d}:00:00") for h in (20, 16, 12)]  # 최신→과거
    page2 = [candle_row(f"2026-07-08T{h:02d}:00:00") for h in (8, 4)]
    up = make_exchange([FakeResponse(200, page1), FakeResponse(200, page2)])

    candles = up.get_candles("KRW-BTC", unit=240, count=5)

    assert [c.timestamp.hour for c in candles] == [4, 8, 12, 16, 20]  # 과거→현재 정렬
    # 두 번째 요청의 'to'는 첫 페이지의 가장 오래된 캔들 시각이어야 한다
    assert up._session.calls[1][1]["to"] == "2026-07-08T12:00:00"
