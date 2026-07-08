"""백테스트 실행 스크립트.

사용법: uv run run_backtest.py
(캔들은 data/ 에 캐시되므로 두 번째 실행부터 빠르다)
"""

from cbot.backtest import Backtester
from cbot.data import get_candles_cached
from cbot.exchanges.upbit import UpbitExchange
from cbot.indicators import candles_to_df
from cbot.strategy import TrendFollowingStrategy

MARKET = "KRW-BTC"
UNIT = 240      # 4시간봉
COUNT = 2000    # 약 333일
INITIAL_CAPITAL = 10_000_000


def main() -> None:
    exchange = UpbitExchange()
    candles = get_candles_cached(exchange, MARKET, unit=UNIT, count=COUNT)
    df = candles_to_df(candles)

    strategy = TrendFollowingStrategy()
    backtester = Backtester(strategy, initial_capital=INITIAL_CAPITAL)
    result = backtester.run(df)

    # --- 요약 (정식 성과 지표는 metrics.py 에서 확장 예정) ---
    equity = result.equity
    mdd = (equity / equity.cummax() - 1).min()  # 최대 낙폭

    print(f"기간          : {df.index[0]} ~ {df.index[-1]}  ({len(df)}봉)")
    print(f"초기 자본     : {result.initial_capital:>15,.0f} KRW")
    print(f"최종 자산     : {result.final_equity:>15,.0f} KRW")
    print(f"총 수익률     : {result.total_return*100:>+14.2f} %")
    print(f"최대 낙폭(MDD): {mdd*100:>+14.2f} %")
    print(f"트레이드 수   : {len(result.trades):>15d}")
    print(f"승률          : {result.win_rate*100:>14.1f} %")

    if result.trades:
        total_fees = sum(t.fees for t in result.trades)
        print(f"총 수수료     : {total_fees:>15,.0f} KRW")
        print("\n--- 트레이드 내역 ---")
        for t in result.trades:
            print(
                f"{t.entry_time:%Y-%m-%d %H:%M} → {t.exit_time:%Y-%m-%d %H:%M}  "
                f"진입 {t.entry_price:>13,.0f}  청산 {t.exit_price:>13,.0f}  "
                f"손익 {t.pnl:>+12,.0f} ({t.return_pct*100:>+6.2f}%)  [{t.reason}]"
            )


if __name__ == "__main__":
    main()
