from xauusd_bot.strategy.zone_detector import ZoneDetector
from xauusd_bot.models import Bias, TimeframeData


def _make_data(close_prices):
    return TimeframeData(
        tf="M5", time=[], open=[], high=[max(p, p + 1) for p in close_prices],
        low=[min(p, p - 1) for p in close_prices], close=list(close_prices),
        tick_volume=[100] * len(close_prices), spread=[1] * len(close_prices),
    )


def test_detect_pullback_zone_bullish():
    zd = ZoneDetector(vwap_period=5)
    prices = [100 + i for i in range(30)]
    data = _make_data(prices)
    zone = zd.detect_pullback_zone(data, Bias.BULLISH)
    assert zone is not None
    low, high = zone
    assert low <= high


def test_detect_pullback_zone_bearish():
    zd = ZoneDetector(vwap_period=5)
    prices = [130 - i for i in range(30)]
    data = _make_data(prices)
    zone = zd.detect_pullback_zone(data, Bias.BEARISH)
    assert zone is not None
    low, high = zone
    assert low <= high


def test_detect_pullback_zone_neutral():
    zd = ZoneDetector(vwap_period=5)
    data = _make_data(list(range(30)))
    zone = zd.detect_pullback_zone(data, Bias.NEUTRAL)
    assert zone is None


def test_detect_pullback_zone_insufficient_data():
    zd = ZoneDetector(vwap_period=20)
    data = _make_data(list(range(10)))
    zone = zd.detect_pullback_zone(data, Bias.BULLISH)
    assert zone is None


def test_price_in_zone():
    zd = ZoneDetector()
    assert zd.price_in_zone(105, (100, 110))
    assert zd.price_in_zone(100, (100, 110))
    assert zd.price_in_zone(110, (100, 110))
    assert not zd.price_in_zone(99, (100, 110))
    assert not zd.price_in_zone(111, (100, 110))


def test_price_in_zone_none():
    zd = ZoneDetector()
    assert not zd.price_in_zone(100, None)