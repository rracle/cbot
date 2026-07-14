"""Bithumb 거래소 어댑터.

빗썸 API 2.0(2024년 개편)은 업비트 API 규격과 '의도적으로 호환'된다 —
엔드포인트 경로(/v1/candles/minutes/{unit}, /v1/ticker), 캔들 필드명
(candle_date_time_utc, opening_price, trade_price...)까지 동일하다.
(2026-07-14 실측 확인: 응답 형식 완전 일치)

그래서 시세 조회는 URL 만 바꾼 상속으로 충분하다 — 어댑터 패턴의 보상.
페이지네이션·재시도·필드 매핑 로직 전부 UpbitExchange 것을 재사용한다.

⚠️ 업비트와 다른 점 (실측으로 발견):
1. 4시간봉 정렬 기준 — 업비트는 UTC 자정(00,04,08,12,16,20시 UTC),
   빗썸은 KST 자정(03,07,11,15,19,23시 UTC). 봉 마감 '시각'이 다르므로
   라이브 엔진의 봉 처리 타이밍도 달라진다. 전략 규칙은 봉 상대적이라
   무관하지만, 두 거래소의 백테스트 수치가 완전히 같을 수는 없다.
2. 수수료 — 원화 마켓 0.04% (30일 쿠폰 등록 필수! 만료 시 0.25%로 복귀).
   백테스트 시 fee_rate=0.0004 를 쓰되, 쿠폰 만료 리스크를 기억할 것.
3. 거래(주문/잔고) 인증 API 는 별도 검증 필요 → 실전 단계에서 구현.
"""

from __future__ import annotations

from cbot.exchanges.upbit import UpbitExchange


class BithumbExchange(UpbitExchange):
    """빗썸 어댑터 — 시세 조회는 업비트 로직 재사용, 거래는 실전 단계에서 구현."""

    name = "bithumb"
    BASE_URL = "https://api.bithumb.com/v1"
