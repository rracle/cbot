"""LiveEngine 테스트 — 실시간 루프의 핵심 성질을 가짜 시세로 검증한다.

검증하는 성질:
  1) 마감 봉에서 신호가 뜨면 매수한다 (진행 중 봉은 절대 쓰지 않음)
  2) 같은 봉을 두 번 처리하지 않는다 (중복 매수 방지)
  3) 현재가가 스탑 이하로 내려오면 판다
  4) 재시작해도 포지션·스탑을 기억한다
"""

from datetime import datetime, timedelta, timezone

import pytest

from cbot.exchanges.paper import PaperExchange
from cbot.live import LiveEngine
from cbot.models import Candle
from cbot.strategy import TrendFollowingStrategy

UNIT = 240


class FakeSource:
    """정해둔 캔들·현재가를 돌려주는 가짜 시세 소스. price 를 바꿔가며 시나리오를 만든다."""

    name = "fake"

    def __init__(self, candles, price):
        self.candles = candles
        self.price = price

    def get_candles(self, market, unit=240, count=200):
        return self.candles[-count:]

    def get_current_price(self, market):
        return self.price


def make_candles(closes):
    """마지막 봉이 '방금 마감'된 상태가 되도록 타임스탬프를 역산해 만든다."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start = now - timedelta(minutes=UNIT * len(closes))
    return [
        Candle(
            timestamp=start + timedelta(minutes=UNIT * i),
            open=float(c), high=float(c) + 1, low=float(c) - 1, close=float(c),
            volume=1.0,
        )
        for i, c in enumerate(closes)
    ]


RISING = [100 + 3 * i for i in range(12)]  # 워밍업(5봉) 이후 매 봉 돌파하는 상승장


def build_engine(tmp_path, closes=RISING):
    src = FakeSource(make_candles(closes), price=float(closes[-1]))
    exchange = PaperExchange(
        src, initial_krw=1_000_000, state_path=str(tmp_path / "paper.json")
    )
    strategy = TrendFollowingStrategy(
        donchian_period=3, ema_fast=2, ema_slow=5, atr_period=3
    )
    engine = LiveEngine(
        exchange, strategy, "KRW-BTC", unit=UNIT, state_path=str(tmp_path / "live.json")
    )
    return src, exchange, engine


def test_buys_on_closed_bar_signal(tmp_path):
    src, exchange, engine = build_engine(tmp_path)
    engine.step()

    assert engine.state.qty > 0                       # 포지션 진입
    assert exchange.get_balance("BTC").available == pytest.approx(engine.state.qty)
    assert 0 < engine.state.stop < engine.state.entry_price  # 손절선이 아래에 세팅됨
    assert engine.state.trades[-1]["reason"] == "entry"


def test_same_bar_never_processed_twice(tmp_path):
    src, exchange, engine = build_engine(tmp_path)
    engine.step()
    qty_after_first = engine.state.qty

    engine.step()  # 새 봉 없음 — 아무 일도 일어나지 않아야 한다
    assert engine.state.qty == qty_after_first
    assert len(engine.state.trades) == 1


def test_stop_hit_sells_position(tmp_path):
    src, exchange, engine = build_engine(tmp_path)
    engine.step()  # 진입
    assert engine.state.qty > 0

    src.price = engine.state.stop - 1  # 현재가가 손절선 아래로
    engine.step()

    assert engine.state.qty == 0.0
    assert exchange.get_balance("BTC").available == 0.0
    assert engine.state.trades[-1]["side"] == "sell"
    assert engine.state.trades[-1]["reason"] == "stop"
    assert engine.state.trades[-1]["pnl"] < 0  # 손절이니 손실


def test_state_survives_restart(tmp_path):
    src, exchange, engine = build_engine(tmp_path)
    engine.step()  # 진입 후 상태 저장됨
    saved_qty, saved_stop = engine.state.qty, engine.state.stop

    # 같은 state_path 로 새 엔진 = 프로세스 재시작
    src2, exchange2, engine2 = build_engine(tmp_path)
    assert engine2.state.qty == pytest.approx(saved_qty)
    assert engine2.state.stop == pytest.approx(saved_stop)
    assert engine2.state.last_processed == engine.state.last_processed


def test_flat_market_never_trades(tmp_path):
    src, exchange, engine = build_engine(tmp_path, closes=[100] * 12)
    engine.step()
    assert engine.state.qty == 0.0
    assert engine.state.trades == []


def test_loads_enough_history_for_ema_warmup(tmp_path):
    """EMA 워밍업 버그 회귀 방지: 엔진은 ema_slow 의 5배 이상 캔들을 요청해야 한다.

    (실거래 첫 진입에서 발견: warmup_bars+60=260개만 로드하면 EMA200 이 덜 데워져
    과소평가되고, 백테스트가 내지 않을 진입을 실시간에서 오발했다.)
    """

    class CountRecordingSource(FakeSource):
        def __init__(self):
            super().__init__(make_candles(RISING), price=100.0)
            self.requested = []

        def get_candles(self, market, unit=240, count=200):
            self.requested.append(count)
            return self.candles[-count:]

    src = CountRecordingSource()
    exchange = PaperExchange(src, initial_krw=1_000_000, state_path=str(tmp_path / "p.json"))
    strat = TrendFollowingStrategy()  # 실전 파라미터 (ema_slow=200)
    engine = LiveEngine(exchange, strat, "KRW-BTC", unit=UNIT, state_path=str(tmp_path / "l.json"))
    engine.step()

    assert max(src.requested) >= strat.ema_slow * 5  # 최소 1000개
