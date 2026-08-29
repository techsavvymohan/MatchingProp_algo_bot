"""Quantitative indicators adapted from je-suis-tm/quant-trading.

Includes:
- Heikin-Ashi Candlestick transformation & trend analysis
- Awesome Oscillator (Bill Williams AO) & Saucer patterns
- Parabolic SAR (PSAR) trailing stop & trend state
- Bollinger Bands & Bandwidth squeeze metrics
- Choppiness Index (CHOP) & ADX for sideways detection
- Dual Thrust intraday breakout boundaries
"""
import math
from typing import Dict, List, Optional, Tuple


def heikin_ashi(
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
) -> Dict[str, List[float]]:
    """Calculate Heikin-Ashi candlestick values from standard OHLC bars.

    Formula from je-suis-tm/quant-trading Heikin-Ashi backtest:
    - HA_Close = (Open + High + Low + Close) / 4
    - HA_Open[0] = Open[0], HA_Open[i] = (HA_Open[i-1] + HA_Close[i-1]) / 2
    - HA_High = max(High, HA_Open, HA_Close)
    - HA_Low = min(Low, HA_Open, HA_Close)
    """
    n = len(closes)
    if n == 0:
        return {"open": [], "high": [], "low": [], "close": []}

    ha_close = [(opens[i] + highs[i] + lows[i] + closes[i]) / 4.0 for i in range(n)]
    ha_open = [opens[0]]
    for i in range(1, n):
        ha_open.append((ha_open[i - 1] + ha_close[i - 1]) / 2.0)

    ha_high = [max(highs[i], ha_open[i], ha_close[i]) for i in range(n)]
    ha_low = [min(lows[i], ha_open[i], ha_close[i]) for i in range(n)]

    return {
        "open": ha_open,
        "high": ha_high,
        "low": ha_low,
        "close": ha_close,
    }


def heikin_ashi_trend(
    ha: Dict[str, List[float]],
    lookback: int = 3,
    tolerance: float = 1e-4,
) -> Tuple[str, float]:
    """Analyze Heikin-Ashi candles for trend clarity and momentum.

    Returns:
    - ('bullish', strength): consecutive green candles with little/no lower wick
    - ('bearish', strength): consecutive red candles with little/no upper wick
    - ('indecision', 0.0): candles with wicks on both sides and small bodies (sideways chop)
    """
    o, h, l, c = ha["open"], ha["high"], ha["low"], ha["close"]
    n = len(c)
    if n < lookback:
        return "indecision", 0.0

    recent_o = o[-lookback:]
    recent_h = h[-lookback:]
    recent_l = l[-lookback:]
    recent_c = c[-lookback:]

    bullish_count = 0
    bearish_count = 0
    shaved_bottoms = 0
    shaved_tops = 0

    for i in range(lookback):
        body = abs(recent_c[i] - recent_o[i])
        total_range = recent_h[i] - recent_l[i]
        if total_range <= 0:
            continue

        if recent_c[i] > recent_o[i]:
            bullish_count += 1
            lower_wick = recent_o[i] - recent_l[i]
            if lower_wick <= body * 0.15 or lower_wick <= tolerance:
                shaved_bottoms += 1
        elif recent_c[i] < recent_o[i]:
            bearish_count += 1
            upper_wick = recent_h[i] - recent_o[i]
            if upper_wick <= body * 0.15 or upper_wick <= tolerance:
                shaved_tops += 1

    if bullish_count == lookback:
        strength = (shaved_bottoms + bullish_count) / (2.0 * lookback)
        return "bullish", strength
    if bearish_count == lookback:
        strength = (shaved_tops + bearish_count) / (2.0 * lookback)
        return "bearish", strength

    return "indecision", 0.0


def awesome_oscillator(
    highs: List[float],
    lows: List[float],
    fast_period: int = 5,
    slow_period: int = 34,
) -> List[float]:
    """Calculate Bill Williams Awesome Oscillator (AO).

    From je-suis-tm/quant-trading Awesome Oscillator backtest:
    Median Price = (High + Low) / 2
    AO = SMA(Median Price, 5) - SMA(Median Price, 34)
    """
    n = len(highs)
    if n < slow_period:
        return []

    median_prices = [(highs[i] + lows[i]) / 2.0 for i in range(n)]

    # Calculate rolling SMA
    fast_sma = []
    running_fast = sum(median_prices[:fast_period])
    fast_sma.append(running_fast / fast_period)
    for i in range(fast_period, n):
        running_fast += median_prices[i] - median_prices[i - fast_period]
        fast_sma.append(running_fast / fast_period)

    slow_sma = []
    running_slow = sum(median_prices[:slow_period])
    slow_sma.append(running_slow / slow_period)
    for i in range(slow_period, n):
        running_slow += median_prices[i] - median_prices[i - slow_period]
        slow_sma.append(running_slow / slow_period)

    # Align to slow_period start
    offset = slow_period - fast_period
    ao = []
    for i in range(len(slow_sma)):
        ao.append(fast_sma[i + offset] - slow_sma[i])

    return ao


def check_ao_saucer(
    ao_series: List[float],
    direction: str,
) -> bool:
    """Detect Bullish or Bearish Saucer setup from Awesome Oscillator.

    From je-suis-tm/quant-trading:
    - Bullish Saucer: AO > 0 across all 3 bars. Bar[-2] < Bar[-3] and Bar[-1] > Bar[-2] (turns back up).
    - Bearish Saucer: AO < 0 across all 3 bars. Bar[-2] > Bar[-3] and Bar[-1] < Bar[-2] (turns back down).
    """
    if len(ao_series) < 3:
        return False

    b1, b2, b3 = ao_series[-3], ao_series[-2], ao_series[-1]

    if direction == "bullish":
        return b1 > 0 and b2 > 0 and b3 > 0 and b2 < b1 and b3 > b2
    elif direction == "bearish":
        return b1 < 0 and b2 < 0 and b3 < 0 and b2 > b1 and b3 < b2
    return False


def parabolic_sar(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    initial_af: float = 0.02,
    step_af: float = 0.02,
    max_af: float = 0.20,
) -> Dict[str, List[float]]:
    """Compute Parabolic SAR series.

    From je-suis-tm/quant-trading Parabolic SAR backtest.
    Returns dictionary with:
    - 'sar': SAR price series
    - 'trend': 1 (bullish) or -1 (bearish) series
    - 'af': acceleration factor series
    """
    n = len(closes)
    if n < 3:
        return {"sar": [], "trend": [], "af": []}

    sar = [0.0] * n
    trend = [0] * n
    ep = [0.0] * n
    af = [0.0] * n

    # Initialize at index 1
    if closes[1] >= closes[0]:
        trend[1] = 1
        sar[1] = lows[0]
        ep[1] = highs[1]
    else:
        trend[1] = -1
        sar[1] = highs[0]
        ep[1] = lows[1]
    af[1] = initial_af

    for i in range(2, n):
        prev_sar = sar[i - 1]
        prev_ep = ep[i - 1]
        prev_af = af[i - 1]
        prev_trend = trend[i - 1]

        tentative_sar = prev_sar + prev_af * (prev_ep - prev_sar)

        if prev_trend > 0:
            # Bullish trend
            current_sar = min(tentative_sar, lows[i - 1], lows[i - 2])
            if current_sar > lows[i]:
                # Trend reversal to bearish
                trend[i] = -1
                sar[i] = max(ep[i - 1], highs[i])
                ep[i] = lows[i]
                af[i] = initial_af
            else:
                trend[i] = 1
                sar[i] = current_sar
                if highs[i] > prev_ep:
                    ep[i] = highs[i]
                    af[i] = min(prev_af + step_af, max_af)
                else:
                    ep[i] = prev_ep
                    af[i] = prev_af
        else:
            # Bearish trend
            current_sar = max(tentative_sar, highs[i - 1], highs[i - 2])
            if current_sar < highs[i]:
                # Trend reversal to bullish
                trend[i] = 1
                sar[i] = min(ep[i - 1], lows[i])
                ep[i] = highs[i]
                af[i] = initial_af
            else:
                trend[i] = -1
                sar[i] = current_sar
                if lows[i] < prev_ep:
                    ep[i] = lows[i]
                    af[i] = min(prev_af + step_af, max_af)
                else:
                    ep[i] = prev_ep
                    af[i] = prev_af

    return {"sar": sar, "trend": trend, "af": af}


def bollinger_bands(
    closes: List[float],
    period: int = 20,
    multiplier: float = 2.0,
) -> Dict[str, List[float]]:
    """Compute Bollinger Bands (Middle, Upper, Lower) and Bandwidth.

    From je-suis-tm/quant-trading Bollinger Bands backtest.
    Bandwidth = (Upper - Lower) / Middle
    """
    n = len(closes)
    if n < period:
        return {"middle": [], "upper": [], "lower": [], "bandwidth": []}

    middle, upper, lower, bandwidth = [], [], [], []
    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(variance)

        up = mean + multiplier * std
        dn = mean - multiplier * std
        bw = (up - dn) / mean if mean != 0 else 0.0

        middle.append(mean)
        upper.append(up)
        lower.append(dn)
        bandwidth.append(bw)

    return {"middle": middle, "upper": upper, "lower": lower, "bandwidth": bandwidth}


def choppiness_index(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14,
) -> Optional[float]:
    """Calculate Choppiness Index (CHOP).

    Values > 61.8 indicate a sideways/consolidating/choppy market.
    Values < 38.2 indicate a strong directional trend.
    Formula: 100 * LOG10(Sum(TR, period) / (Max(High, period) - Min(Low, period))) / LOG10(period)
    """
    n = len(closes)
    if n < period + 1:
        return None

    # Calculate True Range for the last period bars
    tr_sum = 0.0
    for i in range(n - period, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr_sum += max(hl, hc, lc)

    high_max = max(highs[n - period : n])
    low_min = min(lows[n - period : n])
    price_range = high_max - low_min

    if price_range <= 0 or tr_sum <= 0:
        return 100.0

    ratio = tr_sum / price_range
    if ratio <= 0:
        return 100.0

    chop = 100.0 * (math.log10(ratio) / math.log10(period))
    return min(max(chop, 0.0), 100.0)


def adx(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14,
) -> Optional[float]:
    """Calculate Average Directional Index (ADX) to measure trend strength.

    Values < 20-25 indicate weak trend or sideways market.
    Values >= 25 indicate strong directional movement.
    """
    n = len(closes)
    if n < period * 2 + 1:
        return None

    tr_list = []
    plus_dm_list = []
    minus_dm_list = []

    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr = max(hl, hc, lc)
        tr_list.append(tr)

        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        if up_move > down_move and up_move > 0:
            plus_dm_list.append(up_move)
        else:
            plus_dm_list.append(0.0)

        if down_move > up_move and down_move > 0:
            minus_dm_list.append(down_move)
        else:
            minus_dm_list.append(0.0)

    if len(tr_list) < period * 2:
        return None

    # Smoothed initial averages
    smooth_tr = sum(tr_list[:period])
    smooth_plus_dm = sum(plus_dm_list[:period])
    smooth_minus_dm = sum(minus_dm_list[:period])

    dx_list = []
    for i in range(period, len(tr_list)):
        smooth_tr = smooth_tr - (smooth_tr / period) + tr_list[i]
        smooth_plus_dm = smooth_plus_dm - (smooth_plus_dm / period) + plus_dm_list[i]
        smooth_minus_dm = smooth_minus_dm - (smooth_minus_dm / period) + minus_dm_list[i]

        plus_di = 100.0 * (smooth_plus_dm / smooth_tr) if smooth_tr > 0 else 0.0
        minus_di = 100.0 * (smooth_minus_dm / smooth_tr) if smooth_tr > 0 else 0.0

        di_sum = plus_di + minus_di
        di_diff = abs(plus_di - minus_di)
        dx = 100.0 * (di_diff / di_sum) if di_sum > 0 else 0.0
        dx_list.append(dx)

    if len(dx_list) < period:
        return None

    # Average the DX values to get ADX
    adx_val = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx_val = (adx_val * (period - 1) + dx_list[i]) / period

    return adx_val


def dual_thrust_range(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    opens: List[float],
    lookback_days: int = 4,
    k1: float = 0.5,
    k2: float = 0.5,
) -> Tuple[float, float]:
    """Compute Dual Thrust breakout range boundaries.

    From je-suis-tm/quant-trading Dual Thrust backtest:
    Range = max(HH - LC, HC - LL)
    Upper Threshold = Current_Open + K1 * Range
    Lower Threshold = Current_Open - K2 * Range
    """
    n = len(closes)
    if n < lookback_days + 1:
        cur_open = opens[-1] if opens else 0.0
        return cur_open, cur_open

    hh = max(highs[-(lookback_days + 1) : -1])
    hc = max(closes[-(lookback_days + 1) : -1])
    ll = min(lows[-(lookback_days + 1) : -1])
    lc = min(closes[-(lookback_days + 1) : -1])

    range_val = max(hh - lc, hc - ll)
    cur_open = opens[-1]

    upper_trigger = cur_open + k1 * range_val
    lower_trigger = cur_open - k2 * range_val

    return upper_trigger, lower_trigger
