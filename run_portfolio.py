"""다종목 분산 추세추종 검증.

사용법: uv run run_portfolio.py

같은 추세추종 규칙을 6개 마켓에 동시에 돌리고(현금 풀 공유),
단일 BTC 전략과 비교한다. 확인할 것:
  - 분산이 월별 성과를 실제로 고르게 만드는가 (베팅 횟수 증가 효과)
  - 최대 동시 포지션 수 — 상관 높은 코인들의 집중 리스크

📋 검증 결론 (2026-07, 5년):
  겉보기: +181% (CAGR 22.9%) vs 단일 BTC +78% — 그러나 이는 동시 5포지션
  = 총 리스크 ~4배의 결과일 뿐이다 (MDD -26.7%, Sharpe 1.14 < 단일 1.53).
  리스크를 0.33%로 낮춰 변동성을 맞추면: CAGR 10.6% / MDD -11.5% / Sharpe 1.22
  → 동일 리스크에서 단일 BTC(12.2% / -8.1% / 1.53)보다 전부 열세.
  이유: 알트코인은 BTC와 상관 ~0.8 — '분산'이 아니라 '같은 베팅의 반복'이며,
  개별 알트의 추세 품질(Sharpe)이 BTC보다 낮아 평균을 끌어내린다.
  → 결론: 현 유니버스에선 단일 BTC 유지. 분산은 상관 낮은 자산군이 생길 때 재검토.
"""

from cbot.backtest import Backtester
from cbot.data import get_candles_cached
from cbot.exchanges.upbit import UpbitExchange
from cbot.indicators import candles_to_df
from cbot.metrics import evaluate
from cbot.portfolio import PortfolioBacktester
from cbot.strategy import TrendFollowingStrategy

# dict 순서 = 현금 경합 시 우선순위 (유동성 순)
UNIVERSE = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-ADA", "KRW-DOGE"]
COUNT = 11000  # 4h x 11000 ≈ 5년
INITIAL = 10_000_000


def monthly(series):
    m = series.resample("ME").last()
    r = m / m.shift(1) - 1
    r.iloc[0] = m.iloc[0] / series.iloc[0] - 1
    return r


def main() -> None:
    exchange = UpbitExchange()
    dfs = {}
    for market in UNIVERSE:
        candles = get_candles_cached(exchange, market, unit=240, count=COUNT)
        dfs[market] = candles_to_df(candles)
        print(f"{market}: {len(candles)}봉 ({candles[0].timestamp.date()} ~)")

    strategy = TrendFollowingStrategy()
    port = PortfolioBacktester(strategy, initial_capital=INITIAL).run(dfs)
    single = Backtester(strategy, initial_capital=INITIAL).run(dfs["KRW-BTC"])

    print("\n=== 포트폴리오 (6마켓 분산) ===")
    print(evaluate(port).format())
    print(f"최대 동시 포지션: {port.max_concurrent} / {len(UNIVERSE)}")

    by_market = {}
    for t in port.trades:
        d = by_market.setdefault(t.market, {"n": 0, "pnl": 0.0})
        d["n"] += 1
        d["pnl"] += t.pnl
    print("\n마켓별 기여:")
    for m in UNIVERSE:
        d = by_market.get(m, {"n": 0, "pnl": 0.0})
        print(f"  {m:<10} {d['n']:>4}건  손익 {d['pnl']:>+12,.0f} KRW")

    print("\n=== 단일 BTC (비교 기준) ===")
    print(evaluate(single).format())

    pm, sm = monthly(port.equity), monthly(single.equity)
    print("\n=== 월별 프로필 (목표: 매달 꾸준함) ===")
    print(f"{'':>12}{'플러스달':>8}{'평균월':>8}{'최악월':>8}{'월변동성':>9}")
    for name, r in [("단일 BTC", sm), ("6마켓 분산", pm)]:
        print(
            f"{name:>10}: {(r > 0).mean() * 100:>6.0f}% {r.mean() * 100:>+7.2f}%"
            f" {r.min() * 100:>+7.1f}% {r.std() * 100:>8.1f}%"
        )


if __name__ == "__main__":
    main()
