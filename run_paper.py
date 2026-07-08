"""페이퍼 트레이딩 실행.

사용법:
  uv run run_paper.py          # 폴링 루프 (Ctrl+C 로 종료)
  uv run run_paper.py --once   # 한 번만 점검하고 종료 (cron 실행·동작 확인용)

시세는 진짜 Upbit, 체결·잔고는 시뮬레이션(PaperExchange).
상태 파일: data/paper_state.json (잔고), data/live_KRW-BTC_240m.json (포지션)
"""

import sys

from cbot.exchanges.paper import PaperExchange
from cbot.exchanges.upbit import UpbitExchange
from cbot.live import LiveEngine
from cbot.strategy import TrendFollowingStrategy

MARKET = "KRW-BTC"
INITIAL_KRW = 10_000_000


def main() -> None:
    exchange = PaperExchange(UpbitExchange(), initial_krw=INITIAL_KRW)
    engine = LiveEngine(exchange, TrendFollowingStrategy(), MARKET)

    if "--once" in sys.argv:
        engine.step()
        krw = exchange.get_balance("KRW").available
        coin = exchange.get_balance(MARKET.split("-")[1]).available
        pos = "무포지션" if engine.state.qty == 0 else (
            f"보유 {engine.state.qty:.8f} @ {engine.state.entry_price:,.0f}"
            f" (손절선 {engine.state.stop:,.0f})"
        )
        print(f"KRW 잔고: {krw:,.0f} | 코인: {coin:.8f} | {pos}")
        print(f"누적 체결: {len(engine.state.trades)}건")
    else:
        engine.run_forever(poll_seconds=60)


if __name__ == "__main__":
    main()
