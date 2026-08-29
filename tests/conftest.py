import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from xauusd_bot.models import (
    AccountInfo, TimeframeData, Signal, TradeDirection, SignalGrade,
    Bias, Regime, TradeLeg, TradeStatus, PyraCluster,
)


def make_tfdata(tf: str, close: list[float], high: list[float] | None = None,
                low: list[float] | None = None, vol: list[int] | None = None,
                spread: list[int] | None = None) -> TimeframeData:
    n = len(close)
    base = datetime(2025, 1, 1)
    if high is None:
        high = [c + 0.5 for c in close]
    if low is None:
        low = [c - 0.5 for c in close]
    if vol is None:
        vol = [100] * n
    if spread is None:
        spread = [10] * n
    minutes_map = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}
    step = minutes_map.get(tf, 1)
    times = [base + timedelta(minutes=i * step) for i in range(n)]
    return TimeframeData(tf=tf, time=times, open=close, high=high,
                         low=low, close=close, tick_volume=vol, spread=spread)


def trending_bull(n: int = 100) -> list[float]:
    return [100 + i * 0.5 + (i % 5) * 0.1 for i in range(n)]


def ranging(n: int = 60) -> list[float]:
    import math
    return [150 + math.sin(i * 0.3) * 2 + math.cos(i * 0.7) * 2 for i in range(n)]


def make_cluster(direction: TradeDirection = TradeDirection.BUY, legs: int = 1) -> PyraCluster:
    c = PyraCluster(direction=direction)
    for i in range(legs):
        entry = 2000 + i * 10
        leg = TradeLeg(
            direction=direction, entry_price=entry, lot_size=0.1,
            sl_price=entry - 20 if direction == TradeDirection.BUY else entry + 20,
            tp_price=entry + 40 if direction == TradeDirection.BUY else entry - 40,
            open_time=datetime(2025, 1, 1), status=TradeStatus.OPEN,
        )
        c.legs.append(leg)
    c.collective_sl = c.legs[0].sl_price
    c.open_time = datetime(2025, 1, 1)
    return c


def make_signal(direction: TradeDirection = TradeDirection.BUY) -> Signal:
    return Signal(
        direction=direction,
        grade=SignalGrade.A,
        entry_tf="M15",
        h4_bias=Bias.BULLISH if direction == TradeDirection.BUY else Bias.BEARISH,
        h1_bias=Bias.BULLISH if direction == TradeDirection.BUY else Bias.BEARISH,
        m15_bias=Bias.BULLISH if direction == TradeDirection.BUY else Bias.BEARISH,
        regime=Regime.TRENDING_BULL if direction == TradeDirection.BUY else Regime.TRENDING_BEAR,
        entry_price=2000,
        sl_price=1980 if direction == TradeDirection.BUY else 2020,
        tp_price=2040 if direction == TradeDirection.BUY else 1960,
        score=8,
    )


def make_account(equity: float = 100000.0) -> AccountInfo:
    return AccountInfo(balance=equity, equity=equity, leverage=100, currency="USD")