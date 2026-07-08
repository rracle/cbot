"""공통 데이터 타입.

이 모듈은 프로그램 전체가 주고받는 '언어' 역할을 한다.
거래소 어댑터는 거래소의 제각각인 응답을 여기 정의된 타입으로 변환해서 넘기고,
전략·백테스트 코어는 이 타입들만 소비한다. 그래서 코어는 거래소를 몰라도 된다.

- 값이 바뀌면 안 되는 '기록'(체결된 캔들 등)은 frozen dataclass 로 불변 처리한다.
- 상태가 변하는 것(주문, 포지션)은 일반 dataclass 로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class OrderSide(str, Enum):
    """매수/매도 방향.

    str 을 함께 상속하면 값이 문자열처럼 동작해서 JSON 직렬화·로그 출력이 편하다.
    (OrderSide.BUY == "buy" 가 True 가 된다)
    """

    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    """주문 종류.

    MARKET: 현재가로 즉시 체결 (가격 미지정)
    LIMIT:  지정가 — price 에 도달해야 체결
    """

    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    """주문 상태 (거래소 응답을 표준화한 값)."""

    PENDING = "pending"    # 접수됐지만 아직 체결 전
    FILLED = "filled"      # 전량 체결
    CANCELED = "canceled"  # 취소됨
    REJECTED = "rejected"  # 거래소가 거부 (잔고 부족 등)


@dataclass(frozen=True)
class Candle:
    """캔들(봉) 하나. OHLCV.

    frozen=True → 한번 만들면 값을 바꿀 수 없다(불변).
    과거 시세는 '사실'이라 바뀌면 안 되고, 실수로 덮어쓰는 버그를 막아준다.

    timestamp 는 이 캔들이 '시작된' 시각(UTC 권장)으로 통일한다.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Balance:
    """특정 화폐의 잔고.

    currency: "KRW", "BTC" 등
    available: 주문에 쓸 수 있는 금액/수량
    locked:    미체결 주문에 묶여 있는 금액/수량
    """

    currency: str
    available: float = 0.0
    locked: float = 0.0

    @property
    def total(self) -> float:
        """가용 + 묶인 것 = 총 보유량."""
        return self.available + self.locked


@dataclass
class Order:
    """주문 — '요청'과 '결과'를 함께 표현한다.

    주문을 낼 때: market/side/type/volume(+지정가면 price)을 채워 거래소로 보낸다.
    거래소가 응답하면: id/status/filled_* 필드가 채워져 돌아온다.

    volume  : 주문 수량 (BTC 등 기초자산 단위)
    price   : 지정가 주문의 가격 (시장가면 None)
    """

    market: str                       # 예: "KRW-BTC"
    side: OrderSide
    type: OrderType
    volume: float
    price: float | None = None

    # --- 아래는 거래소 응답으로 채워지는 결과 필드 ---
    id: str | None = None
    status: OrderStatus = OrderStatus.PENDING
    filled_volume: float = 0.0        # 실제 체결된 수량
    filled_price: float = 0.0         # 평균 체결가
    fee: float = 0.0                  # 지불한 수수료
    created_at: datetime | None = None


@dataclass
class Position:
    """현재 보유 포지션.

    quantity  : 보유 수량 (0 이면 무포지션)
    avg_price : 평균 매수가
    """

    market: str
    quantity: float = 0.0
    avg_price: float = 0.0

    @property
    def is_open(self) -> bool:
        """포지션을 들고 있는가?"""
        return self.quantity > 0

    def unrealized_pnl(self, current_price: float) -> float:
        """미실현 손익 = (현재가 - 평균가) × 수량. 무포지션이면 0."""
        if not self.is_open:
            return 0.0
        return (current_price - self.avg_price) * self.quantity
