"""거래소 인터페이스(계약).

모든 거래소 어댑터(upbit.py, 추후 binance.py ...)가 '이런 메서드들을 제공하겠다'고
약속하는 계약서다. 코어 코드는 이 Exchange 타입에만 의존하고, 구체적인 거래소가
무엇인지는 알 필요가 없다 → 거래소 교체 = 어댑터 교체.

왜 abc(추상클래스)가 아니라 Protocol 인가?
- Protocol 은 '구조적 타이핑(덕 타이핑)'이다. 어댑터가 base.Exchange 를 상속하지
  않아도, 시그니처가 맞는 메서드만 갖추면 자동으로 Exchange 로 인정된다.
- 즉 어댑터가 이 파일을 import 할 필요조차 없다 → 결합도가 더 낮다.
- @runtime_checkable 을 붙이면 isinstance(obj, Exchange) 로 런타임 확인도 가능.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cbot.models import Balance, Candle, Order


@runtime_checkable
class Exchange(Protocol):
    """거래소 어댑터가 제공해야 하는 기능들.

    크게 두 갈래:
    1) 시세 조회 (읽기 전용, 인증 불필요) — 백테스트/전략에 필요
    2) 거래 (인증 필요) — 실전/페이퍼 트레이딩에 필요
    """

    name: str  # 거래소 식별자, 예: "upbit"

    # ---- 1) 시세 조회 ----

    def get_candles(
        self,
        market: str,
        unit: int = 240,
        count: int = 200,
    ) -> list[Candle]:
        """캔들(봉) 목록을 시간 오름차순으로 반환한다.

        market : "KRW-BTC" 같은 심볼
        unit   : 분 단위 봉 크기 (240 = 4시간봉). 일봉 등은 어댑터가 매핑.
        count  : 가져올 캔들 개수

        반환값은 항상 과거→현재 순서로 정렬돼 있어야 한다(코어가 그렇게 가정).
        """
        ...

    def get_current_price(self, market: str) -> float:
        """현재가(최근 체결가) 하나를 반환한다."""
        ...

    # ---- 2) 거래 (인증 필요) ----

    def get_balance(self, currency: str) -> Balance:
        """특정 화폐의 잔고를 반환한다. 예: get_balance("KRW")."""
        ...

    def place_order(self, order: Order) -> Order:
        """주문을 제출하고, 결과 필드(id/status/체결내역)가 채워진 Order 를 반환한다."""
        ...

    def cancel_order(self, order_id: str) -> bool:
        """주문을 취소한다. 성공하면 True."""
        ...
