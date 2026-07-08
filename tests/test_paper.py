"""PaperExchange 테스트 — 체결·수수료·잔고 정산을 1원 단위로 검증한다."""

import pytest

from cbot.exchanges.base import Exchange
from cbot.exchanges.paper import PaperExchange
from cbot.models import Order, OrderSide, OrderStatus, OrderType


class FakeSource:
    """정해둔 현재가만 돌려주는 가짜 시세 소스."""

    name = "fake"

    def __init__(self, price: float):
        self.price = price

    def get_candles(self, market, unit=240, count=200):
        return []

    def get_current_price(self, market):
        return self.price


def make_paper(tmp_path, price=100.0, krw=10_000.0, fee=0.001, slip=0.01):
    return PaperExchange(
        FakeSource(price), initial_krw=krw, fee_rate=fee, slippage_rate=slip,
        state_path=str(tmp_path / "paper.json"),
    )


def buy(volume):
    return Order(market="KRW-BTC", side=OrderSide.BUY, type=OrderType.MARKET, volume=volume)


def sell(volume):
    return Order(market="KRW-BTC", side=OrderSide.SELL, type=OrderType.MARKET, volume=volume)


def test_satisfies_exchange_protocol(tmp_path):
    assert isinstance(make_paper(tmp_path), Exchange)


def test_buy_updates_balances_exactly(tmp_path):
    ex = make_paper(tmp_path)  # 가격 100, 수수료 0.1%, 슬리피지 1%
    order = ex.place_order(buy(50.0))

    assert order.status == OrderStatus.FILLED
    assert order.filled_price == pytest.approx(101.0)  # 100 x (1+슬리피지)
    cost = 50.0 * 101.0            # 5050
    fee = cost * 0.001             # 5.05
    assert ex.get_balance("KRW").available == pytest.approx(10_000 - cost - fee)
    assert ex.get_balance("BTC").available == pytest.approx(50.0)


def test_buy_insufficient_balance_rejected(tmp_path):
    ex = make_paper(tmp_path)
    order = ex.place_order(buy(200.0))  # 비용 20,200 > 잔고 10,000

    assert order.status == OrderStatus.REJECTED
    assert ex.get_balance("KRW").available == 10_000  # 잔고 변화 없음
    assert ex.get_balance("BTC").available == 0.0


def test_sell_roundtrip_costs_fees_and_slippage(tmp_path):
    ex = make_paper(tmp_path)
    ex.place_order(buy(50.0))
    order = ex.place_order(sell(50.0))

    assert order.status == OrderStatus.FILLED
    assert order.filled_price == pytest.approx(99.0)  # 100 x (1-슬리피지)
    assert ex.get_balance("BTC").available == 0.0
    # 가격 그대로여도 왕복하면 슬리피지+수수료만큼 반드시 잃는다
    assert ex.get_balance("KRW").available < 10_000


def test_sell_more_than_held_rejected(tmp_path):
    ex = make_paper(tmp_path)
    assert ex.place_order(sell(1.0)).status == OrderStatus.REJECTED


def test_balances_survive_restart(tmp_path):
    ex1 = make_paper(tmp_path)
    ex1.place_order(buy(50.0))
    krw_after = ex1.get_balance("KRW").available

    # 같은 state_path 로 다시 생성 = 프로세스 재시작 시뮬레이션
    ex2 = make_paper(tmp_path)
    assert ex2.get_balance("KRW").available == pytest.approx(krw_after)
    assert ex2.get_balance("BTC").available == pytest.approx(50.0)
