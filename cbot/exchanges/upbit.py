"""Upbit 거래소 어댑터.

역할: Upbit REST API 의 제각각인 응답을 우리 공통 타입(models.Candle 등)으로 '번역'한다.
코어(전략/백테스트)는 이 클래스가 Upbit 인지도 모른 채 Exchange 인터페이스로만 쓴다.

이 어댑터는 base.Exchange 를 '상속하지 않는다'. 메서드 시그니처만 맞추면 Protocol 이
구조적으로 Exchange 로 인정해준다(덕 타이핑). 그래도 의도를 드러내려 name 필드는 둔다.

시세 조회(get_candles/get_current_price)는 인증이 필요 없다.
거래(get_balance/place_order/cancel_order)는 JWT 인증이 필요해서, 실전 트레이딩
단계에서 구현한다(지금은 백테스트 우선이라 명시적으로 미구현 처리).
"""

from __future__ import annotations

import time
from datetime import datetime

import requests

from cbot.models import Balance, Candle, Order

# Upbit 공개 API 베이스 URL
BASE_URL = "https://api.upbit.com/v1"

# 한 요청당 최대 캔들 개수 (Upbit 제한)
MAX_COUNT_PER_REQUEST = 200

# Upbit 분봉이 지원하는 unit(분) 값. 그 외 값은 거부한다.
SUPPORTED_MINUTE_UNITS = {1, 3, 5, 10, 15, 30, 60, 240}


class UpbitExchange:
    """Upbit 어댑터. 시세 조회는 바로 쓸 수 있고, 거래는 인증 키가 필요하다."""

    name = "upbit"

    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
        request_delay: float = 0.1,
    ) -> None:
        """
        access_key/secret_key: 거래(주문/잔고)에만 필요. 시세만 쓸 거면 없어도 된다.
                               절대 코드에 하드코딩하지 말고 .env 등에서 주입한다.
        request_delay        : 연속 요청 사이 쉬는 시간(초). Upbit rate limit 완화용.
        """
        self._access_key = access_key
        self._secret_key = secret_key
        self._request_delay = request_delay
        # Session 을 재사용하면 TCP 연결이 유지돼 반복 요청이 빨라진다.
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # 1) 시세 조회 (인증 불필요)
    # ------------------------------------------------------------------

    def get_candles(
        self,
        market: str,
        unit: int = 240,
        count: int = 200,
    ) -> list[Candle]:
        """캔들을 과거→현재(시간 오름차순)로 count 개 반환한다.

        Upbit 는 한 번에 최대 200개, 최신순으로만 주므로:
          1) count 가 200 을 넘으면 여러 번 나눠 요청(페이지네이션)하고,
          2) 'to' 파라미터로 점점 과거로 거슬러 올라가며,
          3) 마지막에 시간순으로 뒤집어 반환한다.
        """
        if unit not in SUPPORTED_MINUTE_UNITS:
            raise ValueError(
                f"지원하지 않는 unit={unit}. 가능한 값: {sorted(SUPPORTED_MINUTE_UNITS)}"
            )
        if count <= 0:
            return []

        url = f"{BASE_URL}/candles/minutes/{unit}"
        collected: list[dict] = []
        to: str | None = None  # None 이면 '가장 최근'부터

        while len(collected) < count:
            remaining = count - len(collected)
            params = {
                "market": market,
                "count": min(remaining, MAX_COUNT_PER_REQUEST),
            }
            if to is not None:
                params["to"] = to

            batch = self._get(url, params)
            if not batch:
                break  # 더 이상 과거 데이터가 없음
            collected.extend(batch)

            # 이번 배치의 가장 오래된 캔들 시각을 다음 요청의 'to'(이 시각 이전)로 사용
            oldest = batch[-1]["candle_date_time_utc"]
            to = oldest

            # rate limit 예의상 잠깐 쉼
            if len(collected) < count:
                time.sleep(self._request_delay)

        # 최신순으로 쌓였으니 뒤집어 과거→현재로. 초과분은 잘라낸다.
        collected = collected[:count]
        collected.reverse()
        return [self._to_candle(row) for row in collected]

    def get_current_price(self, market: str) -> float:
        """현재가(최근 체결가)를 반환한다."""
        rows = self._get(f"{BASE_URL}/ticker", {"markets": market})
        if not rows:
            raise RuntimeError(f"현재가 조회 실패: {market}")
        return float(rows[0]["trade_price"])

    # ------------------------------------------------------------------
    # 2) 거래 (JWT 인증 필요) — 실전 트레이딩 단계에서 구현
    # ------------------------------------------------------------------

    def get_balance(self, currency: str) -> Balance:
        raise NotImplementedError(
            "거래 기능은 JWT 인증(PyJWT + .env 키)이 필요합니다. "
            "실전/페이퍼 트레이딩 단계에서 구현합니다."
        )

    def place_order(self, order: Order) -> Order:
        raise NotImplementedError(
            "거래 기능은 실전/페이퍼 트레이딩 단계에서 구현합니다."
        )

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError(
            "거래 기능은 실전/페이퍼 트레이딩 단계에서 구현합니다."
        )

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _get(self, url: str, params: dict) -> list[dict]:
        """공개 GET 요청. 에러면 예외를 던지고, 성공하면 JSON(list)을 반환."""
        resp = self._session.get(url, params=params, timeout=10)
        resp.raise_for_status()  # 4xx/5xx 면 예외
        return resp.json()

    @staticmethod
    def _to_candle(row: dict) -> Candle:
        """Upbit 캔들 응답 1건(dict)을 공통 Candle 로 변환한다.

        이 매핑이 어댑터의 심장이다. 거래소마다 필드명이 다르므로,
        '번역'은 여기 한 곳에 가둔다.
        """
        return Candle(
            timestamp=datetime.fromisoformat(row["candle_date_time_utc"]),
            open=float(row["opening_price"]),
            high=float(row["high_price"]),
            low=float(row["low_price"]),
            close=float(row["trade_price"]),          # trade_price = 종가
            volume=float(row["candle_acc_trade_volume"]),
        )
