"""ATR 추세추종 전략 (거래소 무관).

이 모듈은 '매매 규칙'의 단일 진실 공급원이다. 실제 체결/현금 관리는 하지 않는다
(그건 backtest/실전 실행기의 몫). 여기서는 다음만 정의한다:
  - 진입 조건        entry_signal()
  - 손절선           initial_stop()
  - 트레일링 스탑    trailing_stop()
  - 포지션 크기      position_size()

역할 분리 이유: 진입은 벡터화 가능하지만, 청산(트레일링 스탑)은 '진입 후 최고가'에
의존하는 경로 의존적 로직이다. 규칙만 여기 모으고, 상태 추적은 실행기가 맡는다.

⚠️ 룩어헤드 방어
- 진입은 '직전 period 봉'의 돈치안 상단 돌파로 판단한다(현재 봉 제외 → shift(1)).
- 신호는 봉 '종가'에서 확정되고, 실제 체결은 다음 봉에서 일어난다고 가정한다
  (실행기가 그렇게 처리). 신호 생성에 쓴 가격으로 즉시 체결하면 룩어헤드가 된다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from cbot.indicators import atr, donchian, ema, rsi


@dataclass
class TrendFollowingStrategy:
    """돈치안 돌파 + EMA 추세필터 + ATR 손절/사이징.

    파라미터는 CLAUDE.md의 초기안과 일치한다. 과최적화 경계를 위해
    '견고한 기본값'을 쓰고, 데이터에 맞춰 미세조정하지 않는다.
    """

    donchian_period: int = 20    # 돌파 기준 봉 수
    ema_fast: int = 50           # 단기 추세
    ema_slow: int = 200          # 장기 추세 (fast>slow 일 때만 매수)
    atr_period: int = 14
    atr_stop_mult: float = 2.0   # 손절폭 = 2 x ATR
    atr_trail_mult: float = 3.0  # 트레일링 스탑폭 = 3 x ATR
    risk_per_trade: float = 0.01 # 트레이드당 총자본의 1% 리스크
    min_order_value: float = 5000.0  # 거래소 최소 주문 금액 (Upbit 원화 마켓 기준)

    @property
    def warmup_bars(self) -> int:
        """지표가 신뢰할 만해지기까지 필요한 봉 수.

        ewm(EMA)은 NaN 없이 첫 봉부터 값을 내지만, 초반 값은 '덜 데워진' 상태다.
        가장 긴 지표(ema_slow=200) 길이만큼은 신호를 내지 않는다.
        """
        return max(self.ema_slow, self.donchian_period + 1, self.atr_period)

    # ------------------------------------------------------------------
    # 지표 준비
    # ------------------------------------------------------------------

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """지표 컬럼을 붙인 새 DataFrame 을 반환한다(원본 불변).

        dc_upper_prev/dc_lower_prev 는 '현재 봉을 제외한' 채널이다(shift(1)).
        진입 판단에 바로 쓸 수 있게 여기서 미리 룩어헤드를 제거해 둔다.
        """
        out = df.copy()
        out["ema_fast"] = ema(out["close"], self.ema_fast)
        out["ema_slow"] = ema(out["close"], self.ema_slow)
        out["atr"] = atr(out, self.atr_period)

        dc = donchian(out, self.donchian_period)
        # shift(1): 현재 봉을 제외한 직전 period 봉의 채널
        out["dc_upper_prev"] = dc["upper"].shift(1)
        out["dc_lower_prev"] = dc["lower"].shift(1)
        return out

    # ------------------------------------------------------------------
    # 진입 (벡터화 가능)
    # ------------------------------------------------------------------

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        """매수 진입 신호(bool Series). prepare()를 거친 df 를 받는다.

        조건:
          1) 종가 > 직전 20봉 최고가   → 돌파(추세 시작)
          2) EMA50 > EMA200            → 큰 추세가 상승
        Donchian 은 앞 period 봉이 NaN 이라 자동으로 걸러지지만, EMA 는 NaN 없이
        '덜 데워진 값'을 내므로 warmup_bars 구간의 신호를 명시적으로 차단한다.
        """
        breakout = df["close"] > df["dc_upper_prev"]
        uptrend = df["ema_fast"] > df["ema_slow"]
        signal = (breakout & uptrend).fillna(False)
        signal.iloc[: self.warmup_bars] = False  # 워밍업 구간 신호 금지
        return signal

    # ------------------------------------------------------------------
    # 청산 규칙 (경로 의존적 — 실행기가 상태와 함께 호출)
    # ------------------------------------------------------------------

    def initial_stop(self, entry_price: float, atr_value: float) -> float:
        """최초 손절선 = 진입가 - 2 x ATR."""
        return entry_price - self.atr_stop_mult * atr_value

    def trailing_stop(self, peak_price: float, atr_value: float) -> float:
        """트레일링 스탑 = (진입 후 최고가) - 3 x ATR.

        가격이 오르면 peak_price 가 올라가며 스탑도 따라 올라간다(이익 보호).
        내려갈 때는 스탑이 내려가지 않는다 → 실행기가 max 로 관리한다.
        """
        return peak_price - self.atr_trail_mult * atr_value

    # ------------------------------------------------------------------
    # 포지션 사이징
    # ------------------------------------------------------------------

    def position_size(
        self, capital: float, entry_price: float, atr_value: float
    ) -> float:
        """리스크 기반 수량 산정 → 기초자산 단위 수량 반환.

        핵심 아이디어: '한 번 손절당 잃는 금액'을 총자본의 risk_per_trade 로 고정한다.
          리스크금액 = capital x risk_per_trade
          손절거리   = atr_stop_mult x ATR   (진입가와 손절선 사이 거리)
          수량       = 리스크금액 / 손절거리
        → 변동성(ATR)이 크면 수량이 작아진다(자동 리스크 조절).

        안전장치 두 가지:
        - 자본 캡: 변동성이 아주 낮으면 계산 수량의 매수금액이 자본을 초과할 수
          있다(현물은 레버리지 불가). 가진 돈 이상은 사지 않는다.
        - 최소 주문: 주문 가치가 거래소 최소 금액 미만이면 주문 불가 → 0 반환.
        """
        if atr_value <= 0 or entry_price <= 0 or capital <= 0:
            return 0.0
        risk_amount = capital * self.risk_per_trade
        stop_distance = self.atr_stop_mult * atr_value
        qty = risk_amount / stop_distance
        qty = min(qty, capital / entry_price)  # 현물: 가진 돈 이상 못 산다
        if qty * entry_price < self.min_order_value:
            return 0.0
        return qty


@dataclass
class MeanReversionStrategy:
    """평균회귀: 상승 국면의 단기 과매도 급락을 사서, 평균 복귀에 판다.

    ⚠️ 검증 실패 — 실전 사용 금지 (실험 기록용으로 보존)
    KRW-BTC 4h 5년(2021-07~2026-07) 백테스트: -12.8%, PF 0.72, Sharpe -0.77.
    승률 62.9%는 설계대로였으나 손익비 0.43 — 잦은 잔이익이 손절(42회)과
    수수료(자본의 ~5%)를 못 이겼다. 파라미터를 데이터에 맞춰 되살리려는
    시도는 과최적화다(CLAUDE.md 원칙). 재도전하려면 구조 자체를 바꿀 것.

    추세추종과 반대 성격 — 수익이 '자주, 잘게' 나온다(높은 승률 x 낮은 손익비).
    추세추종이 노는 횡보/상승장에서 일하고, 하락 추세는 EMA 필터로 회피한다.

    ⚠️ 평균회귀의 본질적 위험: '더 떨어지면 더 싸졌다'는 논리는 폭락장에선
    파산 논리가 된다. 그래서 청산을 세 겹으로 갖춘다:
      1) 신호 청산 : RSI 회복 → 평균 복귀 완료 (정상 시나리오)
      2) 시간 청산 : max_hold_bars 안에 회복 못 하면 포기 (가정 실패 인정)
      3) 하드 스탑 : 진입가 - 3xATR (재난 방지선)
    트레일링 스탑은 없다 — 이익을 태우는 전략이 아니라 빨리 회수하는 전략이다.
    (trailing_stop 미정의 → 백테스터가 알아서 생략한다. 덕 타이핑.)
    """

    rsi_period: int = 4          # 짧은 RSI 로 '단기 급락' 포착 (Connors 계열 표준)
    rsi_entry: float = 25.0      # 이보다 낮으면 과매도 → 진입
    rsi_exit: float = 55.0       # 이보다 높으면 평균 복귀 완료 → 청산
    ema_trend: int = 200         # 상승 국면 필터 (종가 > EMA200 일 때만 매수)
    atr_period: int = 14
    atr_stop_mult: float = 3.0   # 하드 스탑 — MR 은 넓게 (타이트하면 반등 직전에 털린다)
    risk_per_trade: float = 0.01
    max_hold_bars: int = 30      # 4h x 30 = 5일 안에 회복 없으면 시간 청산
    min_order_value: float = 5000.0

    @property
    def warmup_bars(self) -> int:
        return max(self.ema_trend, self.atr_period, self.rsi_period + 1)

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["ema_trend"] = ema(out["close"], self.ema_trend)
        out["atr"] = atr(out, self.atr_period)
        out["rsi"] = rsi(out["close"], self.rsi_period)
        return out

    def entry_signal(self, df: pd.DataFrame) -> pd.Series:
        """과매도(RSI) + 상승 국면(EMA 위) 일 때 매수."""
        oversold = df["rsi"] < self.rsi_entry
        uptrend = df["close"] > df["ema_trend"]
        signal = (oversold & uptrend).fillna(False)
        signal.iloc[: self.warmup_bars] = False
        return signal

    def exit_signal(self, df: pd.DataFrame) -> pd.Series:
        """RSI 가 회복되면 청산 — 평균 복귀 완료, 더 욕심내지 않는다."""
        return (df["rsi"] > self.rsi_exit).fillna(False)

    def initial_stop(self, entry_price: float, atr_value: float) -> float:
        return entry_price - self.atr_stop_mult * atr_value

    def position_size(
        self, capital: float, entry_price: float, atr_value: float
    ) -> float:
        """추세추종과 같은 리스크 기반 사이징 (손절거리만 3xATR)."""
        if atr_value <= 0 or entry_price <= 0 or capital <= 0:
            return 0.0
        risk_amount = capital * self.risk_per_trade
        stop_distance = self.atr_stop_mult * atr_value
        qty = risk_amount / stop_distance
        qty = min(qty, capital / entry_price)
        if qty * entry_price < self.min_order_value:
            return 0.0
        return qty
