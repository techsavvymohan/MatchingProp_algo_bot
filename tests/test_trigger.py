from xauusd_bot.strategy.trigger import TriggerDetector
from xauusd_bot.models import TimeframeData, TradeDirection


def _make_data(close_prices):
    return TimeframeData(
        tf="M5", time=[], open=[], high=[p + 1 for p in close_prices],
        low=[p - 1 for p in close_prices], close=list(close_prices),
        tick_volume=[100] * len(close_prices), spread=[1] * len(close_prices),
    )


def test_momentum_bullish():
    td = TriggerDetector(ema_fast=9, rsi_period=14, rsi_mid_upper=70, rsi_mid_lower=30)
    prices = [100.0]
    for i in range(100):
        if i % 3 == 0:
            prices.append(prices[-1] - 0.6)
        else:
            prices.append(prices[-1] + 0.5)
    data = _make_data(prices)
    ok, reason = td.check_momentum_continuation(data, TradeDirection.BUY)
    assert ok, reason


def test_momentum_bearish():
    td = TriggerDetector(ema_fast=9, rsi_period=14, rsi_mid_upper=70, rsi_mid_lower=30)
    prices = [110.0]
    for i in range(100):
        if i % 3 == 0:
            prices.append(prices[-1] + 0.6)
        else:
            prices.append(prices[-1] - 0.5)
    data = _make_data(prices)
    ok, reason = td.check_momentum_continuation(data, TradeDirection.SELL)
    assert ok, reason


def test_momentum_insufficient_data():
    td = TriggerDetector()
    data = _make_data([100, 101])
    ok, _ = td.check_momentum_continuation(data, TradeDirection.BUY)
    assert not ok


def test_micro_structure_break_buy():
    td = TriggerDetector()
    highs = [100, 101, 100, 99, 105]
    data = TimeframeData(tf="M5", time=[], open=[], high=highs, low=[h - 2 for h in highs],
                         close=[h - 1 for h in highs], tick_volume=[100]*5, spread=[1]*5)
    ok, price = td.check_micro_structure_break(data, TradeDirection.BUY)
    assert ok
    assert price == 105


def test_micro_structure_break_sell():
    td = TriggerDetector()
    lows = [100, 99, 100, 101, 95]
    data = TimeframeData(tf="M5", time=[], open=[], high=[l + 2 for l in lows], low=lows,
                         close=[l + 1 for l in lows], tick_volume=[100]*5, spread=[1]*5)
    ok, price = td.check_micro_structure_break(data, TradeDirection.SELL)
    assert ok
    assert price == 95


def test_micro_structure_no_break():
    td = TriggerDetector()
    data = _make_data([100, 101, 102])
    ok, _ = td.check_micro_structure_break(data, TradeDirection.BUY)
    assert not ok


def test_zone_entry_in_zone():
    td = TriggerDetector()
    ok, reason = td.check_zone_entry(105, (100, 110), TradeDirection.BUY)
    assert ok


def test_zone_entry_outside():
    td = TriggerDetector()
    ok, _ = td.check_zone_entry(99, (100, 110), TradeDirection.BUY)
    assert not ok


def test_zone_entry_none():
    td = TriggerDetector()
    ok, _ = td.check_zone_entry(100, None, TradeDirection.BUY)
    assert not ok


def test_ema_stack_bullish():
    td = TriggerDetector()
    prices = [100 + i * 0.3 for i in range(60)]
    data = _make_data(prices)
    ok, reason = td.check_ema_stack_alignment(data)
    assert ok, reason


def test_ema_stack_bearish():
    td = TriggerDetector()
    prices = [115 - i * 0.3 for i in range(60)]
    data = _make_data(prices)
    ok, reason = td.check_ema_stack_alignment(data)
    assert ok, reason


def test_ema_stack_insufficient():
    td = TriggerDetector()
    data = _make_data([100] * 30)
    ok, _ = td.check_ema_stack_alignment(data)
    assert not ok