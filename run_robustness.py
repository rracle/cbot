"""견고성 검증 스크립트.

사용법: uv run run_robustness.py

⚠️ 목적은 '최고 파라미터 찾기'가 아니다 — 그건 과최적화다 (CLAUDE.md 원칙).
확인하려는 것:
  A. 마켓 견고성   — 같은 규칙이 다른 코인에서도 무너지지 않는가
  B. 기간 견고성   — 긴 기간(상승장+하락장 포함)에서도 살아남는가
  C. 파라미터 민감성 — 파라미터를 조금 바꿔도 결과가 급변하지 않는가
     좋은 전략 = 파라미터 공간에서 '평평한 고원', 과최적화 = '뾰족한 봉우리'
"""

from dataclasses import replace

from cbot.backtest import Backtester
from cbot.data import get_candles_cached
from cbot.exchanges.upbit import UpbitExchange
from cbot.indicators import candles_to_df
from cbot.metrics import evaluate
from cbot.strategy import TrendFollowingStrategy

UNIT = 240
BASE = TrendFollowingStrategy()  # 기본 파라미터 — 여기서 '조금씩' 흔들어본다


def run_one(exchange, market: str, count: int, strategy: TrendFollowingStrategy, label: str) -> dict:
    """마켓 하나를 백테스트하고 표 한 줄 분량의 결과를 반환한다."""
    candles = get_candles_cached(exchange, market, unit=UNIT, count=count)
    df = candles_to_df(candles)
    result = Backtester(strategy).run(df)
    report = evaluate(result)
    days = (df.index[-1] - df.index[0]).days
    bh = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    return {
        "label": label,
        "days": days,
        "ret": report.total_return,
        "bh": bh,
        "mdd": report.mdd,
        "trades": report.n_trades,
        "win": report.win_rate,
        "pf": report.profit_factor,
        "payoff": report.payoff_ratio,
    }


def print_yearly(exchange, market: str, count: int) -> None:
    """연도별 전략 vs B&H 분해 — '최악의 해'가 어디까지 나빠지는지 보는 용도."""
    candles = get_candles_cached(exchange, market, unit=UNIT, count=count)
    df = candles_to_df(candles)
    result = Backtester(BASE).run(df)
    eq = result.equity
    print(f"\n--- {market} 연도별 수익률 (전략 vs B&H) ---")
    for year, grp in eq.groupby(eq.index.year):
        px = df["close"][df.index.year == year]
        strat_ret = grp.iloc[-1] / grp.iloc[0] - 1
        bh_ret = px.iloc[-1] / px.iloc[0] - 1
        print(f"  {year}: 전략 {strat_ret*100:>+7.1f}%   B&H {bh_ret*100:>+7.1f}%")


def print_table(title: str, rows: list[dict]) -> None:
    print(f"\n{'='*100}\n{title}\n{'='*100}")
    print(
        f"{'구분':<16}{'기간(일)':>8}{'전략수익':>10}{'B&H':>10}{'MDD':>9}"
        f"{'거래수':>7}{'승률':>8}{'PF':>7}{'손익비':>8}"
    )
    print("-" * 100)
    for r in rows:
        pf = f"{r['pf']:.2f}" if r["pf"] != float("inf") else "inf"
        print(
            f"{r['label']:<16}{r['days']:>8}{r['ret']*100:>+9.1f}%{r['bh']*100:>+9.1f}%"
            f"{r['mdd']*100:>+8.1f}%{r['trades']:>7}{r['win']*100:>7.0f}%{pf:>7}{r['payoff']:>8.2f}"
        )


def main() -> None:
    exchange = UpbitExchange()

    # --- A. 마켓 견고성: 같은 규칙, 다른 코인 (약 333일) ---
    markets = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
    rows = [run_one(exchange, m, 2000, BASE, m) for m in markets]
    print_table("A. 마켓 견고성 — 같은 규칙, 다른 코인 (4h x 2000봉 ≈ 333일)", rows)

    # --- B. 기간 견고성: 5년 풀사이클 (2021 불장 → 2022 폭락 → 2023-25 상승 → 하락) ---
    rows = [
        run_one(exchange, m, 11000, BASE, m)
        for m in ["KRW-BTC", "KRW-ETH"]
    ]
    print_table("B. 기간 견고성 — 4h x 11000봉 ≈ 5년 풀사이클", rows)
    print_yearly(exchange, "KRW-BTC", 11000)

    # --- C. 파라미터 민감성: 하나씩만 흔든다 (BTC, 5년) ---
    # 결과가 '완만하게' 변하면 견고, 특정 값에서만 좋으면 위험 신호다.
    variations: list[tuple[str, TrendFollowingStrategy]] = []
    for dc in [10, 15, 20, 30, 40]:
        variations.append((f"donchian={dc}", replace(BASE, donchian_period=dc)))
    for stop in [1.5, 2.0, 2.5, 3.0]:
        variations.append((f"stop={stop}xATR", replace(BASE, atr_stop_mult=stop)))
    for trail in [2.0, 3.0, 4.0]:
        variations.append((f"trail={trail}xATR", replace(BASE, atr_trail_mult=trail)))

    rows = [run_one(exchange, "KRW-BTC", 11000, s, label) for label, s in variations]
    print_table("C. 파라미터 민감성 — KRW-BTC 5년, 하나씩만 변경 (기본: dc=20, stop=2, trail=3)", rows)


if __name__ == "__main__":
    main()
