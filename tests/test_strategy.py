import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from xauusd_bot.models import (
    Bias, Regime, TimeframeData, Signal, SignalGrade,
    TradeDirection, Session,
)
from xauusd_bot.strategy.bias_detector import BiasDetector
from xauusd_bot.strategy.zone_detector import ZoneDetector
from xauusd_bot.strategy.signal_scorer import SignalScorer
from xauusd_bot.strategy.trigger import TriggerDetector
from xauusd_bot.strategy.timeframe_hierarchy import TimeframeHierarchy


def _make_tfdata(tf: str, close: list[float], high: list[float] | None = None,
                 low: list[float] | None = None, vol: list[int] | None = None,
                 spread: list[int] | None = None):
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
    minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}
    step = minutes.get(tf, 1)
    times = [base + timedelta(minutes=i * step) for i in range(n)]
    return TimeframeData(tf=tf, time=times, open=close, high=high,
                         low=low, close=close, tick_volume=vol, spread=spread)


def _trending_bull(len=100):
    return [100 + i * 0.5 + (i % 5) * 0.1 for i in range(len)]


def _trending_bear(len=100):
    return [200 - i * 0.5 - (i % 5) * 0.1 for i in range(len)]


def _ranging(n=120):
    import math as _m
    return [150 + _m.sin(i * 0.3) * 2 + _m.cos(i * 0.7) * 2 for i in range(n)]


def _trending_bull_noise(n=100):
    p = 100
    out = [p]
    trend_end = min(15, n - 1)
    for i in range(1, min(15, n)):
        p += 0.6
        out.append(p)
    slow_end = min(25, n)
    for i in range(15, slow_end):
        p += 0.2
        out.append(p)
    consol_end = min(38, n)
    for i in range(25, consol_end):
        p += 0.2 if i % 2 == 0 else -0.2
        out.append(p)
    break_end = min(42, n)
    for i in range(38, break_end):
        p += 0.15
        out.append(p)
    for i in range(42, n):
        step = 0.15 if i % 2 == 1 else -0.15
        p += step
        out.append(p)
    return out[:n]


def _trending_bear_noise(n=100):
    p = 200
    out = [p]
    for i in range(1, min(15, n)):
        p -= 0.6
        out.append(p)
    for i in range(15, min(25, n)):
        p -= 0.2
        out.append(p)
    for i in range(25, min(38, n)):
        p -= 0.2 if i % 2 == 0 else -0.2
        out.append(p)
    for i in range(38, min(42, n)):
        p -= 0.15
        out.append(p)
    for i in range(42, n):
        step = 0.15 if i % 2 == 1 else -0.15
        p -= step
        out.append(p)
    return out[:n]


# ── BiasDetector ──

def test_bias_bullish():
    bd = BiasDetector()
    data = _make_tfdata("H1", _trending_bull(100))
    bias = bd.detect_bias(data)
    assert bias == Bias.BULLISH


def test_bias_bearish():
    bd = BiasDetector()
    data = _make_tfdata("H1", _trending_bear(100))
    bias = bd.detect_bias(data)
    assert bias == Bias.BEARISH


def test_bias_neutral():
    bd = BiasDetector()
    data = _make_tfdata("H1", _ranging(120))
    bias = bd.detect_bias(data)
    assert bias == Bias.NEUTRAL


def test_bias_insufficient_data():
    bd = BiasDetector()
    data = _make_tfdata("H1", [100, 101])
    assert bd.detect_bias(data) == Bias.NEUTRAL


def test_regime_trending_bull():
    bd = BiasDetector()
    data = _make_tfdata("H4", _trending_bull(120))
    regime = bd.detect_regime(data)
    assert regime == Regime.TRENDING_BULL


def test_regime_ranging():
    bd = BiasDetector()
    data = _make_tfdata("H4", _ranging(60))
    regime = bd.detect_regime(data)
    assert regime == Regime.RANGING


# ── ZoneDetector ──

def test_pullback_zone_bullish():
    zd = ZoneDetector()
    data = _make_tfdata("M15", _trending_bull(50))
    zone = zd.detect_pullback_zone(data, Bias.BULLISH)
    assert zone is not None
    assert zone[0] < zone[1]


def test_pullback_zone_bearish():
    zd = ZoneDetector()
    data = _make_tfdata("M15", _trending_bear(50))
    zone = zd.detect_pullback_zone(data, Bias.BEARISH)
    assert zone is not None
    assert zone[0] < zone[1]


def test_pullback_zone_neutral():
    zd = ZoneDetector()
    data = _make_tfdata("M15", _ranging(50))
    assert zd.detect_pullback_zone(data, Bias.NEUTRAL) is None


def test_price_in_zone():
    zd = ZoneDetector()
    assert zd.price_in_zone(105, (100, 110))
    assert not zd.price_in_zone(95, (100, 110))
    assert not zd.price_in_zone(115, (100, 110))
    assert not zd.price_in_zone(105, None)


# ── SignalScorer ──

def test_signal_grade_a():
    sc = SignalScorer(8, 5)
    sig = Signal(
        direction=TradeDirection.BUY, score=8,
        regime=Regime.TRENDING_BULL,
        m15_zone=(100, 110),
    )
    assert sc.grade(sig) == SignalGrade.A


def test_signal_grade_b():
    sc = SignalScorer(8, 5)
    sig = Signal(direction=TradeDirection.BUY, score=5, regime=Regime.RANGING)
    assert sc.grade(sig) == SignalGrade.B


def test_signal_grade_c():
    sc = SignalScorer(8, 5)
    sig = Signal(direction=TradeDirection.BUY, score=2, regime=Regime.RANGING)
    assert sc.grade(sig) == SignalGrade.C


def test_direction_from_bias():
    sc = SignalScorer()
    assert sc.direction_from_bias({"allowed_direction": "bullish"}) == TradeDirection.BUY
    assert sc.direction_from_bias({"allowed_direction": "bearish"}) == TradeDirection.SELL


# ── TriggerDetector ──

def test_momentum_bullish():
    td = TriggerDetector()
    data = _make_tfdata("M5", _trending_bull_noise(90))
    ok, msg = td.check_momentum_continuation(data, TradeDirection.BUY)
    assert ok, msg


def test_momentum_bearish():
    td = TriggerDetector()
    data = _make_tfdata("M5", _trending_bear_noise(90))
    ok, msg = td.check_momentum_continuation(data, TradeDirection.SELL)
    assert ok, msg


def test_micro_structure_break_buy():
    td = TriggerDetector()
    data = _make_tfdata("M1",
        close=[100, 101, 100, 102, 101, 103],
        high=[101, 102, 101, 103, 102, 104],
    )
    ok, price = td.check_micro_structure_break(data, TradeDirection.BUY, 3)
    assert ok


def test_micro_structure_break_sell():
    td = TriggerDetector()
    data = _make_tfdata("M1",
        close=[104, 103, 104, 102, 103, 101],
        low=[103, 102, 103, 101, 102, 100],
    )
    ok, price = td.check_micro_structure_break(data, TradeDirection.SELL, 3)
    assert ok


def test_zone_entry():
    td = TriggerDetector()
    ok, _ = td.check_zone_entry(105, (100, 110), TradeDirection.BUY)
    assert ok
    ok, _ = td.check_zone_entry(95, (100, 110), TradeDirection.BUY)
    assert not ok


# ── TimeframeHierarchy ──

def test_hierarchy_bullish_alignment():
    bd = BiasDetector()
    zd = ZoneDetector()
    th = TimeframeHierarchy(bd, zd)
    data_all = {
        "H4": _make_tfdata("H4", _trending_bull(120)),
        "H1": _make_tfdata("H1", _trending_bull(100)),
        "M15": _make_tfdata("M15", _trending_bull(60)),
        "M5": _make_tfdata("M5", _trending_bull(40)),
        "M1": _make_tfdata("M1", _trending_bull(20)),
    }
    result = th.evaluate(data_all, Session.LONDON)
    assert result["allowed_direction"] == "bullish"
    assert result["alignment_count"] >= 3


def test_hierarchy_bearish_alignment():
    bd = BiasDetector()
    zd = ZoneDetector()
    th = TimeframeHierarchy(bd, zd)
    data_all = {
        "H4": _make_tfdata("H4", _trending_bear(120)),
        "H1": _make_tfdata("H1", _trending_bear(100)),
        "M15": _make_tfdata("M15", _trending_bear(60)),
        "M5": _make_tfdata("M5", _trending_bear(40)),
        "M1": _make_tfdata("M1", _trending_bear(20)),
    }
    result = th.evaluate(data_all, Session.LONDON)
    assert result["allowed_direction"] == "bearish"
    assert result["alignment_count"] >= 3


def test_hierarchy_no_direction():
    bd = BiasDetector()
    zd = ZoneDetector()
    th = TimeframeHierarchy(bd, zd)
    data_all = {
        "H4": _make_tfdata("H4", _ranging(60)),
        "H1": _make_tfdata("H1", _ranging(60)),
        "M15": _make_tfdata("M15", _ranging(60)),
        "M5": _make_tfdata("M5", _ranging(40)),
        "M1": _make_tfdata("M1", _ranging(30)),
    }
    result = th.evaluate(data_all, Session.LONDON)
    assert result["allowed_direction"] is not None


def test_hierarchy_entry_tier():
    bd = BiasDetector()
    zd = ZoneDetector()
    th = TimeframeHierarchy(bd, zd)
    data_all = {"H4": _make_tfdata("H4", _trending_bull(120)),
                 "H1": _make_tfdata("H1", _trending_bull(100))}
    ny = th._select_entry_tier(Session.NY, Regime.TRENDING_BULL)
    assert ny == "M1"
    asian = th._select_entry_tier(Session.ASIAN, Regime.RANGING)
    assert asian == "M15"
