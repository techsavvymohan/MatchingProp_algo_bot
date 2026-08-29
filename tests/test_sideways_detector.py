import math
from datetime import datetime, timedelta
import pytest

from xauusd_bot.models import TimeframeData
from xauusd_bot.strategy.sideways_detector import SidewaysDetector


def _make_tf(tf: str, closes: list, highs: list = None, lows: list = None, opens: list = None):
    n = len(closes)
    base = datetime(2025, 1, 1)
    if highs is None:
        highs = [c + 0.5 for c in closes]
    if lows is None:
        lows = [c - 0.5 for c in closes]
    if opens is None:
        opens = [c for c in closes]
    times = [base + timedelta(minutes=i * 15) for i in range(n)]
    return TimeframeData(
        tf=tf,
        time=times,
        open=opens,
        high=highs,
        low=lows,
        close=closes,
        tick_volume=[100] * n,
        spread=[10] * n,
    )


def test_sideways_detector_trending_market():
    detector = SidewaysDetector()
    # Continuous directional trend (bullish)
    n = 60
    closes = [100.0 + i * 1.5 for i in range(n)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    opens = [c - 0.3 for c in closes]

    m15 = _make_tf("M15", closes, highs, lows, opens)
    is_sw, reason, conf = detector.is_timeframe_sideways(m15)
    assert not is_sw
    assert conf < 0.5

    data_all = {"M15": m15, "H1": m15}
    blocked, reason_all = detector.check_sideways(data_all)
    assert not blocked


def test_sideways_detector_choppy_ranging_market():
    detector = SidewaysDetector(chop_threshold=55.0, adx_threshold=25.0)
    # Tight sine wave / oscillating sideways chop with dojis
    n = 70
    closes = [100.0 + math.sin(i * 0.5) * 0.5 for i in range(n)]
    highs = [c + 1.2 for c in closes]
    lows = [c - 1.2 for c in closes]
    opens = [c + 0.05 if i % 2 == 0 else c - 0.05 for i, c in enumerate(closes)]

    m15 = _make_tf("M15", closes, highs, lows, opens)
    is_sw, reason, conf = detector.is_timeframe_sideways(m15)
    assert is_sw
    assert conf >= 0.6

    data_all = {"M15": m15, "H1": m15}
    blocked, reason_all = detector.check_sideways(data_all)
    assert blocked
    assert "Sideways" in reason_all


def test_sideways_detector_insufficient_data():
    detector = SidewaysDetector()
    m15 = _make_tf("M15", [100.0, 101.0, 102.0])
    is_sw, reason, conf = detector.is_timeframe_sideways(m15)
    assert not is_sw
    assert reason == "insufficient data"
