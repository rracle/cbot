"""백테스트 실행 스크립트.

사용법: uv run run_backtest.py
(캔들은 data/ 에 캐시되므로 두 번째 실행부터 빠르다)
"""

from cbot.backtest import Backtester
from cbot.data import get_candles_cached
from cbot.exchanges.upbit import UpbitExchange
from cbot.indicators import candles_to_df
from cbot.metrics import evaluate
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

    print(f"기간          : {df.index[0]} ~ {df.index[-1]}  ({len(df)}봉)")
    print(f"초기 자본     : {result.initial_capital:>15,.0f} KRW")
    print(f"최종 자산     : {result.final_equity:>15,.0f} KRW")

    # Buy & Hold 와 비교해야 전략의 기여를 알 수 있다
    bh_return = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    print(f"Buy & Hold    : {bh_return*100:>+14.2f} %")
    print()
    print(evaluate(result).format())

    if result.trades:
        print("\n--- 트레이드 내역 ---")
        for t in result.trades:
            print(
                f"{t.entry_time:%Y-%m-%d %H:%M} → {t.exit_time:%Y-%m-%d %H:%M}  "
                f"진입 {t.entry_price:>13,.0f}  청산 {t.exit_price:>13,.0f}  "
                f"손익 {t.pnl:>+12,.0f} ({t.return_pct*100:>+6.2f}%)  [{t.reason}]"
            )


if __name__ == "__main__":
    main()
