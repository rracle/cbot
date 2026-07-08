"""페이퍼(모의) 거래소 어댑터.

시세는 '진짜' 거래소에 위임하고, 주문·잔고만 시뮬레이션한다.
Exchange 프로토콜을 그대로 구현하므로 실전 어댑터와 서로 교체 가능하다:

    PaperExchange(UpbitExchange())   # 페이퍼: 진짜 시세 + 가짜 체결
    UpbitExchange(access, secret)    # 실전 (미래) — 엔진 코드는 안 바뀐다

체결 모델 (백테스트와 동일한 비관적 가정):
  - 시장가 즉시 전량 체결. 매수는 x(1+슬리피지), 매도는 x(1-슬리피지)
  - 수수료 fee_rate 양방향 부과
  - 잔고 부족이면 REJECTED

한계(정직하게): 실제 호가창 깊이·부분 체결은 모른다.
페이퍼 성과는 실전보다 낙관적이라고 가정할 것.

잔고는 JSON 으로 영속화한다 → 프로세스를 재시작해도 이어진다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from cbot.exchanges.base import Exchange
from cbot.models import Balance, Candle, Order, OrderSide, OrderStatus


class PaperExchange:
    name = "paper"

    def __init__(
        self,
        data_source: Exchange,
        initial_krw: float = 10_000_000,
        fee_rate: float = 0.0005,
        slippage_rate: float = 0.0005,
        state_path: str = "data/paper_state.json",
    ) -> None:
        self._source = data_source
        self._fee = fee_rate
        self._slip = slippage_rate
        self._path = Path(state_path)

        if self._path.exists():  # 재시작: 이전 잔고 복원
            saved = json.loads(self._path.read_text())
            self._balances: dict[str, float] = saved["balances"]
            self._order_seq: int = saved["order_seq"]
        else:
            self._balances = {"KRW": float(initial_krw)}
            self._order_seq = 0

    # ------------------------------------------------------------------
    # 시세 — 진짜 거래소에 그대로 위임
    # ------------------------------------------------------------------

    def get_candles(self, market: str, unit: int = 240, count: int = 200) -> list[Candle]:
        return self._source.get_candles(market, unit=unit, count=count)

    def get_current_price(self, market: str) -> float:
        return self._source.get_current_price(market)

    # ------------------------------------------------------------------
    # 거래 — 시뮬레이션
    # ------------------------------------------------------------------

    def get_balance(self, currency: str) -> Balance:
        return Balance(currency=currency, available=float(self._balances.get(currency, 0.0)))

    def place_order(self, order: Order) -> Order:
        """시장가 주문을 현재가 기준으로 즉시 체결 처리한다."""
        price = self._source.get_current_price(order.market)
        quote, base = order.market.split("-")  # "KRW-BTC" → ("KRW", "BTC")

        if order.side == OrderSide.BUY:
            fill = price * (1 + self._slip)  # 매수는 불리하게
            cost = order.volume * fill
            fee = cost * self._fee
            if self._balances.get(quote, 0.0) < cost + fee:
                order.status = OrderStatus.REJECTED  # 잔고 부족 — 실전과 같은 결과
                return order
            self._balances[quote] = self._balances.get(quote, 0.0) - (cost + fee)
            self._balances[base] = self._balances.get(base, 0.0) + order.volume
        else:
            if self._balances.get(base, 0.0) < order.volume:
                order.status = OrderStatus.REJECTED
                return order
            fill = price * (1 - self._slip)  # 매도는 불리하게
            proceeds = order.volume * fill
            fee = proceeds * self._fee
            self._balances[base] = self._balances.get(base, 0.0) - order.volume
            self._balances[quote] = self._balances.get(quote, 0.0) + proceeds - fee

        self._order_seq += 1
        order.id = f"paper-{self._order_seq}"
        order.status = OrderStatus.FILLED
        order.filled_volume = order.volume
        order.filled_price = fill
        order.fee = fee
        order.created_at = datetime.now()
        self._save()
        return order

    def cancel_order(self, order_id: str) -> bool:
        return False  # 시장가 즉시 체결 모델 — 취소할 미체결 주문이 없다

    # ------------------------------------------------------------------

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"balances": self._balances, "order_seq": self._order_seq}, indent=2)
        )
