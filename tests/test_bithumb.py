"""빗썸 어댑터 테스트 — 상속이 올바르게 동작하는지 확인한다.

로직 자체(페이지네이션·재시도·매핑)는 test_upbit.py 가 이미 검증하므로,
여기서는 '빗썸으로 요청이 가는가'와 '프로토콜 준수'만 확인한다.
"""

from cbot.exchanges.base import Exchange
from cbot.exchanges.bithumb import BithumbExchange

from tests.test_upbit import FakeResponse, FakeSession, candle_row


def make_exchange(responses):
    ex = BithumbExchange(request_delay=0)
    ex._session = FakeSession(responses)
    return ex


def test_satisfies_exchange_protocol():
    assert isinstance(BithumbExchange(), Exchange)
    assert BithumbExchange.name == "bithumb"


def test_requests_go_to_bithumb_host():
    ex = make_exchange([FakeResponse(200, [candle_row("2026-07-14T11:00:00")])])
    ex.get_candles("KRW-BTC", unit=240, count=1)
    url, params = ex._session.calls[0]
    assert url.startswith("https://api.bithumb.com/v1/")  # 업비트가 아니라 빗썸으로
    assert params["market"] == "KRW-BTC"


def test_candle_mapping_reused_from_upbit():
    # 응답 규격이 같으므로 매핑도 그대로 동작해야 한다
    ex = make_exchange([FakeResponse(200, [candle_row("2026-07-14T11:00:00", close=100.0)])])
    (c,) = ex.get_candles("KRW-BTC", unit=240, count=1)
    assert c.close == 100.0
    assert c.timestamp.isoformat() == "2026-07-14T11:00:00"


def test_cache_filename_differs_by_exchange_name():
    # data.py 캐시가 upbit_*.csv 와 bithumb_*.csv 로 분리되는 근거
    assert BithumbExchange().name != "upbit"
