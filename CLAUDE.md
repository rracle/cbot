# cbot — 코인 자동매매 봇

## 프로젝트 목적
Upbit 대상 알고리즘 트레이딩 봇. ATR 기반 추세추종 전략을 백테스트로 검증한 뒤,
페이퍼 트레이딩 → 소액 실전 순서로 단계적으로 배포한다.

## 핵심 원칙 (반드시 준수)
- **검증 순서**: 백테스트 → 페이퍼 트레이딩(최소 2~4주) → 소액 실전. 단계를 건너뛰지 않는다.
- 모든 백테스트는 **수수료(원화 마켓 0.05%)와 슬리피지를 포함**해 계산한다.
- **손절·포지션 사이징이 없는 전략은 실전에 배포하지 않는다.**
- **API 키·시드는 `.env`로만 관리하고 절대 커밋하지 않는다.** (`.gitignore`에 포함됨)
- 과최적화 경계: 파라미터를 과거 데이터에 맞춰 미세조정하지 않는다. 견고성 > 최고 수익률.
- **거래소 추상화**: 전략·백테스트·지표 코드는 특정 거래소를 몰라야 한다. 거래소 연동은
  `exchanges/`의 얇은 어댑터에만 둔다. 공통 인터페이스(`Exchange` 프로토콜)에만 의존하고,
  Upbit→Binance 등 교체 시 어댑터만 갈아끼운다.

## 기술 스택
- **Python 3.12** (uv가 격리 관리 — 시스템 파이썬과 무관)
- **uv** — 패키지·가상환경·락파일 관리
- **pandas / numpy** — 시계열 처리·백테스트
- **requests** — Upbit 공개 REST API (캔들 조회는 인증 불필요)

의존성은 `pyproject.toml`에 선언하고 `uv.lock`으로 고정한다. `uv.lock`은 커밋한다(재현성).

## 자주 쓰는 명령
```bash
uv sync                 # pyproject.toml/uv.lock 기준으로 환경 구성
uv add <패키지>          # 의존성 추가
uv run <스크립트.py>     # venv 안에서 실행
uv run pytest             # 테스트 실행
uv run run_backtest.py    # 백테스트 실행
uv run run_robustness.py  # 견고성 검증 (마켓/기간/파라미터 민감성)
```

## 디렉토리 구조 (구축 예정)

거래소에 독립적인 **코어**와 거래소별 **어댑터**를 분리한다.

```
cbot/
├── models.py            공통 데이터 타입 (Candle, Order, Position 등)
├── exchanges/
│   ├── base.py          Exchange 인터페이스(프로토콜): 캔들 조회·주문·잔고
│   ├── upbit.py         Upbit 어댑터 (공개 캔들 + JWT 인증 주문, 429/5xx 재시도)
│   └── (binance.py)     추후 추가 시 여기에
├── data.py              캔들 로컬 캐시 (data/*.csv — API 재호출 최소화)
├── indicators.py        ATR, EMA, Donchian 등 지표 (거래소 무관)
├── strategy.py          진입/청산 신호 생성 (거래소 무관)
├── backtest.py          백테스트 엔진 (수수료·슬리피지 포함, 거래소 무관)
└── metrics.py           성과 지표 (CAGR, MDD, Sharpe, 승률, 손익비)
tests/                   pytest 단위 테스트 (지표·전략·어댑터)
run_backtest.py          실행 스크립트
```

**의존성 방향**: `strategy`/`backtest`/`indicators` → `models`(공통 타입)에만 의존.
거래소 어댑터(`exchanges/*`)도 `models`를 구현/반환할 뿐, 코어가 어댑터를 직접 import 하지 않는다.
데이터는 어댑터가 `Candle` 리스트/DataFrame으로 넘겨주고, 코어는 그것만 소비한다.

## 전략 요약 (ATR 추세추종, 초기안)
- **타임프레임**: 4시간봉(`/v1/candles/minutes/240`) — 노이즈·수수료 회피
- **진입(Long)**: Donchian 상단(최근 20봉 고가) 돌파 AND EMA(50) > EMA(200)
- **손절**: 진입가 − 2×ATR
- **트레일링 스탑**: 최고가 − 3×ATR
- **포지션 사이징**: 트레이드당 총자본의 1~2% 리스크 → 수량 = (자본×리스크%) / (2×ATR)

## 거래소 특이사항 (Upbit)
- 최소 주문 금액: **5,000원**
- 캔들 API: `/v1/candles/minutes/{unit}` (unit: 1,3,5,10,15,30,60,240), `/v1/candles/days`
- 한 번에 최대 200개 캔들, 요청 rate limit 있음(초당 제한) — 수집 시 페이지네이션·딜레이 필요
- 수수료: 원화 마켓 0.05%
- 주문/잔고 조회 등은 JWT 인증 필요 (Access/Secret 키 → `.env`)

## 코딩 규약
- 함수·변수: snake_case, 클래스: PascalCase
- 타입힌트 사용 권장
- 금액·수량은 부동소수 오차에 주의 (필요 시 반올림/정밀도 명시)
