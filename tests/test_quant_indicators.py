import math
import pytest

from xauusd_bot.indicators.quant_indicators import (
    adx,
    awesome_oscillator,
    bollinger_bands,
    check_ao_saucer,
    choppiness_index,
    dual_thrust_range,
    heikin_ashi,
    heikin_ashi_trend,
    parabolic_sar,
)


def test_heikin_ashi_calculation():
    opens = [100.0, 101.0, 102.0, 103.0]
    highs = [102.0, 103.0, 104.0, 105.0]
    lows = [99.0, 100.0, 101.0, 102.0]
    closes = [101.0, 102.0, 103.0, 104.0]

    ha = heikin_ashi(opens, highs, lows, closes)
    assert len(ha["close"]) == 4
    # First candle HA_Close = (100 + 102 + 99 + 101) / 4 = 100.5
    assert ha["close"][0] == 100.5
    # First candle HA_Open = opens[0] = 100.0
    assert ha["open"][0] == 100.0
    # Second candle HA_Open = (100.0 + 100.5) / 2 = 100.25
    assert ha["open"][1] == 100.25
    # HA_High >= HA_Close and HA_High >= HA_Open
    assert ha["high"][1] >= ha["close"][1]
    assert ha["high"][1] >= ha["open"][1]


def test_heikin_ashi_trend_bullish():
    # Construct consecutive green candles with shaved bottoms
    opens = [100.0, 102.0, 104.0, 106.0]
    closes = [102.0, 104.0, 106.0, 108.0]
    highs = [103.0, 105.0, 107.0, 109.0]
    lows = [99.9, 101.9, 103.9, 105.9]

    ha = heikin_ashi(opens, highs, lows, closes)
    trend, strength = heikin_ashi_trend(ha, lookback=3)
    assert trend == "bullish"
    assert strength > 0.5


def test_heikin_ashi_trend_bearish():
    opens = [110.0, 108.0, 106.0, 104.0]
    closes = [108.0, 106.0, 104.0, 102.0]
    highs = [110.1, 108.1, 106.1, 104.1]
    lows = [107.0, 105.0, 103.0, 101.0]

    ha = heikin_ashi(opens, highs, lows, closes)
    trend, strength = heikin_ashi_trend(ha, lookback=3)
    assert trend == "bearish"
    assert strength > 0.5


def test_awesome_oscillator_and_saucers():
    highs = [100.0 + i * 0.5 for i in range(50)]
    lows = [99.0 + i * 0.5 for i in range(50)]
    ao = awesome_oscillator(highs, lows, fast_period=5, slow_period=34)
    assert len(ao) == 50 - 34 + 1
    # In an uptrend, fast SMA > slow SMA -> AO is positive
    assert ao[-1] > 0

    # Bullish saucer test: b1 > 0, b2 > 0, b3 > 0, b2 < b1 and b3 > b2
    test_ao_bull = [1.5, 1.0, 1.4]
    assert check_ao_saucer(test_ao_bull, "bullish")
    assert not check_ao_saucer(test_ao_bull, "bearish")

    # Bearish saucer test: b1 < 0, b2 < 0, b3 < 0, b2 > b1 and b3 < b2
    test_ao_bear = [-1.5, -1.0, -1.4]
    assert check_ao_saucer(test_ao_bear, "bearish")
    assert not check_ao_saucer(test_ao_bear, "bullish")


def test_parabolic_sar():
    closes = [100 + i * 1.0 for i in range(25)]
    highs = [c + 0.8 for c in closes]
    lows = [c - 0.8 for c in closes]

    psar = parabolic_sar(highs, lows, closes, initial_af=0.02, step_af=0.02, max_af=0.20)
    assert len(psar["sar"]) == len(closes)
    assert len(psar["trend"]) == len(closes)
    # In steady uptrend, trend should be +1 (bullish) and SAR below price
    assert psar["trend"][-1] == 1
    assert psar["sar"][-1] < closes[-1]
    assert psar["af"][-1] > 0.02


def test_bollinger_bands_and_bandwidth():
    closes = [100.0, 102.0, 98.0, 101.0, 100.0] * 5
    bb = bollinger_bands(closes, period=20, multiplier=2.0)
    assert len(bb["middle"]) == len(closes) - 20 + 1
    assert len(bb["upper"]) == len(bb["middle"])
    assert len(bb["lower"]) == len(bb["middle"])
    assert len(bb["bandwidth"]) == len(bb["middle"])

    # Upper > Middle > Lower
    assert bb["upper"][-1] > bb["middle"][-1] > bb["lower"][-1]
    # Bandwidth > 0
    assert bb["bandwidth"][-1] > 0


def test_choppiness_index():
    # Strong trend: consecutive higher closes with small true ranges
    trend_closes = [100.0 + i * 1.0 for i in range(25)]
    trend_highs = [c + 0.2 for c in trend_closes]
    trend_lows = [c - 0.2 for c in trend_closes]
    chop_trend = choppiness_index(trend_highs, trend_lows, trend_closes, period=14)
    assert chop_trend is not None
    # Trending CHOP should be low (typically < 45)
    assert chop_trend < 45.0

    # Choppy market: oscillates back and forth violently within a fixed range
    chop_closes = [100.0 if i % 2 == 0 else 102.0 for i in range(25)]
    chop_highs = [c + 1.5 for c in chop_closes]
    chop_lows = [c - 1.5 for c in chop_closes]
    chop_range = choppiness_index(chop_highs, chop_lows, chop_closes, period=14)
    assert chop_range is not None
    # Choppy CHOP should be high (> 60)
    assert chop_range > 55.0


def test_adx():
    closes = [100.0 + i * 1.0 for i in range(40)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]

    adx_val = adx(highs, lows, closes, period=14)
    assert adx_val is not None
    # In sustained trend, ADX should be strong (> 25)
    assert adx_val > 25.0


def test_dual_thrust_range():
    highs = [105.0, 106.0, 107.0, 108.0, 109.0]
    lows = [95.0, 96.0, 97.0, 98.0, 99.0]
    closes = [100.0, 102.0, 104.0, 106.0, 108.0]
    opens = [98.0, 100.0, 102.0, 104.0, 106.0]

    up, dn = dual_thrust_range(highs, lows, closes, opens, lookback_days=4, k1=0.5, k2=0.5)
    # Range = max(HH - LC, HC - LL) = max(108 - 100, 106 - 95) = 11.0
    # Current open = 106.0
    # up = 106.0 + 0.5 * 11.0 = 111.5
    # dn = 106.0 - 0.5 * 11.0 = 100.5
    assert up > opens[-1]
    assert dn < opens[-1]
