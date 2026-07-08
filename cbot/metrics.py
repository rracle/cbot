"""성과 지표 (거래소 무관).

백테스트 결과(자산 곡선 + 트레이드 목록)를 '전략을 평가할 수 있는 숫자'로 요약한다.

왜 총수익률 하나로는 안 되나:
- 같은 +10%라도 MDD -50% 를 견디며 얻었는지 -5% 로 얻었는지는 다른 전략이다
- CAGR : 기간이 다른 전략/시장끼리 비교하려면 연율화가 필요하다
- Sharpe: 수익의 '질' — 변동성 한 단위당 수익. 운으로 번 돈과 꾸준히 번 돈을 구분
- 승률 x 손익비: 전략의 성격. 추세추종은 '낮은 승률 x 높은 손익비'로 먹는 구조라
  승률만 보면 나쁜 전략처럼 보인다 — 두 지표는 반드시 함께 봐야 한다

⚠️ 표본 크기: 트레이드가 30건 미만이면 어떤 지표든 통계적 의미가 약하다.
숫자를 결론이 아니라 '단서'로 읽을 것.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from cbot.backtest import BacktestResult, Trade


# ----------------------------------------------------------------------
# 개별 지표 (순수 함수 — 각각 단위 테스트 가능)
# ----------------------------------------------------------------------


def cagr(equity: pd.Series) -> float:
    """연평균 성장률. 실제 경과 시간 기준으로 연율화한다.

    (최종/초기)^(365/경과일수) - 1
    → '이 속도로 1년 굴리면 몇 %인가'. 기간이 다른 백테스트끼리 비교 가능해진다.
    """
    days = (equity.index[-1] - equity.index[0]).total_seconds() / 86400
    if days <= 0 or equity.iloc[0] <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (365 / days) - 1)


def max_drawdown(equity: pd.Series) -> float:
    """최대 낙폭(MDD) — 고점 대비 최대 하락률. 음수로 반환 (예: -0.25 = -25%).

    cummax(지금까지의 최고점) 대비 현재 자산의 비율에서 최악값.
    '이 전략을 들고 있으면 최악의 순간 몇 %를 잃은 채 버텨야 하나'.
    """
    return float((equity / equity.cummax() - 1).min())


def sharpe_ratio(equity: pd.Series) -> float:
    """샤프 비율 — 변동성 한 단위당 수익 (연율화, 무위험수익률 0 가정).

    봉 단위 수익률의 mean/std 에 sqrt(연간 봉 수)를 곱해 연율화한다.
    (변동성은 시간의 제곱근에 비례해 커진다는 통계 성질)
    봉 간격은 인덱스에서 자동 추론하므로 4시간봉이든 일봉이든 그대로 쓴다.
    대략: <1 약함 / 1~2 쓸만 / >2 우수 — 단, 표본이 충분할 때 이야기.
    """
    returns = equity.pct_change().dropna()
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    bar_interval = equity.index.to_series().diff().median()
    periods_per_year = pd.Timedelta(days=365) / bar_interval
    return float(returns.mean() / returns.std() * periods_per_year**0.5)


def profit_factor(trades: list[Trade]) -> float:
    """총이익 / 총손실. 1.0 이 손익분기, 1.5 이상이면 준수.

    승률과 달리 '금액'을 반영한다 — 몇 번 이겼냐가 아니라 얼마를 벌고 잃었냐.
    """
    gross_win = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = -sum(t.pnl for t in trades if t.pnl < 0)
    if gross_loss == 0:
        return float("inf") if gross_win > 0 else 0.0
    return gross_win / gross_loss


def payoff_ratio(trades: list[Trade]) -> float:
    """손익비 — 평균 이익 / 평균 손실.

    추세추종의 생명선. 승률 40%라도 손익비 2.5 면 기대값이 양수다:
    기대값 = 승률 x 평균이익 - (1-승률) x 평균손실
    """
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [-t.pnl for t in trades if t.pnl < 0]
    if not wins or not losses:
        return 0.0
    avg_win = sum(wins) / len(wins)
    avg_loss = sum(losses) / len(losses)
    return avg_win / avg_loss if avg_loss > 0 else 0.0


def exposure(trades: list[Trade], equity: pd.Series) -> float:
    """시장 노출 비율 — 전체 기간 중 포지션을 들고 있던 시간의 비율.

    낮은 노출로 같은 수익을 냈다면 그만큼 리스크에 덜 노출된 것이다.
    """
    span = (equity.index[-1] - equity.index[0]).total_seconds()
    if span <= 0:
        return 0.0
    held = sum((t.exit_time - t.entry_time).total_seconds() for t in trades)
    return held / span


# ----------------------------------------------------------------------
# 종합 리포트
# ----------------------------------------------------------------------


@dataclass
class PerformanceReport:
    total_return: float
    cagr: float
    mdd: float
    sharpe: float
    n_trades: int
    win_rate: float
    profit_factor: float
    payoff_ratio: float
    exposure: float
    total_fees: float

    def format(self) -> str:
        lines = [
            f"총 수익률     : {self.total_return*100:>+10.2f} %",
            f"CAGR(연율화)  : {self.cagr*100:>+10.2f} %",
            f"최대 낙폭(MDD): {self.mdd*100:>+10.2f} %",
            f"Sharpe        : {self.sharpe:>10.2f}",
            f"트레이드 수   : {self.n_trades:>10d}",
            f"승률          : {self.win_rate*100:>10.1f} %",
            f"Profit Factor : {self.profit_factor:>10.2f}",
            f"손익비        : {self.payoff_ratio:>10.2f}",
            f"시장 노출     : {self.exposure*100:>10.1f} %",
            f"총 수수료     : {self.total_fees:>10,.0f} KRW",
        ]
        if self.n_trades < 30:
            lines.append(
                f"⚠️  표본 {self.n_trades}건 — 통계적 의미가 약하다. 단서로만 읽을 것."
            )
        return "\n".join(lines)


def evaluate(result: BacktestResult) -> PerformanceReport:
    """BacktestResult 를 종합 성과 리포트로 변환한다."""
    return PerformanceReport(
        total_return=result.total_return,
        cagr=cagr(result.equity),
        mdd=max_drawdown(result.equity),
        sharpe=sharpe_ratio(result.equity),
        n_trades=len(result.trades),
        win_rate=result.win_rate,
        profit_factor=profit_factor(result.trades),
        payoff_ratio=payoff_ratio(result.trades),
        exposure=exposure(result.trades, result.equity),
        total_fees=sum(t.fees for t in result.trades),
    )
