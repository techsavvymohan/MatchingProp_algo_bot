import math
from typing import Dict, List, Optional, Tuple


def ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    result = sum(values[:period]) / period
    for i in range(period, len(values)):
        result = values[i] * k + result * (1 - k)
    return result


def ema_series(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for i in range(period, len(values)):
        result.append(values[i] * k + result[-1] * (1 - k))
    return result


def sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def vwap(high: List[float], low: List[float], close: List[float], volume: List[int], period: int = 20) -> Optional[float]:
    if len(close) < period:
        return None
    typical = [(h + l + c) / 3 for h, l, c in zip(high[-period:], low[-period:], close[-period:])]
    vol = volume[-period:]
    tp_v = sum(t * v for t, v in zip(typical, vol))
    total_v = sum(vol)
    return tp_v / total_v if total_v else 0.0


def vwap_bands(
    high: List[float],
    low: List[float],
    close: List[float],
    volume: List[int],
    period: int = 20,
    mult1: float = 1.0,
    mult2: float = 2.0,
) -> Optional[Tuple[float, float, float, float, float]]:
    """Calculate Volume-Weighted Average Price (VWAP) with Standard Deviation Bands.

    Returns:
        (vwap_val, upper_band_1, lower_band_1, upper_band_2, lower_band_2)
        representing the mean, ±1 standard deviation (~68% value area),
        and ±2 standard deviations (~95% statistical extreme/exhaustion boundary).
    """
    if len(close) < period or len(high) < period or len(low) < period or len(volume) < period:
        return None

    typical = [(h + l + c) / 3.0 for h, l, c in zip(high[-period:], low[-period:], close[-period:])]
    vol = volume[-period:]
    total_v = sum(vol)
    if total_v <= 0:
        return None

    vwap_val = sum(t * v for t, v in zip(typical, vol)) / total_v

    # Weighted standard deviation: sqrt( sum( v * (tp - vwap)^2 ) / sum(v) )
    variance = sum(v * ((t - vwap_val) ** 2) for t, v in zip(typical, vol)) / total_v
    stdev = math.sqrt(variance)

    upper_1 = vwap_val + mult1 * stdev
    lower_1 = vwap_val - mult1 * stdev
    upper_2 = vwap_val + mult2 * stdev
    lower_2 = vwap_val - mult2 * stdev

    return vwap_val, upper_1, lower_1, upper_2, lower_2


def calc_volume_profile(
    high: List[float],
    low: List[float],
    close: List[float],
    volume: List[int],
    bins: int = 50,
    value_area_pct: float = 0.70,
    lookback: Optional[int] = None,
) -> Optional[Dict[str, float]]:
    """Calculate Auction Market Theory Volume Profile: POC, VAH, and VAL.

    - Point of Control (POC): Price bin with the highest accumulated volume.
    - Value Area (VA): Price range containing `value_area_pct` (typically 70%) of total volume.
    - Value Area High (VAH): Upper boundary of the Value Area.
    - Value Area Low (VAL): Lower boundary of the Value Area.
    """
    n = len(close)
    if n == 0 or len(high) != n or len(low) != n or len(volume) != n:
        return None

    if lookback is not None and lookback > 0:
        h_slice = high[-lookback:]
        l_slice = low[-lookback:]
        c_slice = close[-lookback:]
        v_slice = volume[-lookback:]
    else:
        h_slice = high
        l_slice = low
        c_slice = close
        v_slice = volume

    min_p = min(l_slice)
    max_p = max(h_slice)
    total_v = sum(v_slice)

    if min_p >= max_p or total_v <= 0 or bins <= 0:
        if min_p == max_p and total_v > 0:
            return {"poc": min_p, "vah": min_p, "val": min_p, "total_volume": float(total_v)}
        return None

    bin_step = (max_p - min_p) / bins
    bin_volumes = [0.0] * bins

    for h, l, c, v in zip(h_slice, l_slice, c_slice, v_slice):
        if v <= 0:
            continue
        idx_low = int((l - min_p) / bin_step)
        idx_high = int((h - min_p) / bin_step)
        idx_low = max(0, min(bins - 1, idx_low))
        idx_high = max(0, min(bins - 1, idx_high))

        span = idx_high - idx_low + 1
        vol_per_bin = v / span
        for b in range(idx_low, idx_high + 1):
            bin_volumes[b] += vol_per_bin

    # 1. Point of Control (POC) = bin with max volume
    max_bin_idx = 0
    max_bin_vol = bin_volumes[0]
    for b in range(1, bins):
        if bin_volumes[b] > max_bin_vol:
            max_bin_vol = bin_volumes[b]
            max_bin_idx = b

    poc_price = min_p + (max_bin_idx + 0.5) * bin_step

    # 2. Value Area (70% standard) expanding from POC
    target_volume = total_v * value_area_pct
    current_volume = bin_volumes[max_bin_idx]
    lower_idx = max_bin_idx
    upper_idx = max_bin_idx

    while current_volume < target_volume and (lower_idx > 0 or upper_idx < bins - 1):
        next_above_vol = bin_volumes[upper_idx + 1] if upper_idx < bins - 1 else -1.0
        next_below_vol = bin_volumes[lower_idx - 1] if lower_idx > 0 else -1.0

        if next_above_vol >= next_below_vol and next_above_vol >= 0:
            upper_idx += 1
            current_volume += next_above_vol
        elif next_below_vol > next_above_vol and next_below_vol >= 0:
            lower_idx -= 1
            current_volume += next_below_vol
        elif next_above_vol >= 0:
            upper_idx += 1
            current_volume += next_above_vol
        elif next_below_vol >= 0:
            lower_idx -= 1
            current_volume += next_below_vol
        else:
            break

    val_price = min_p + lower_idx * bin_step
    vah_price = min_p + (upper_idx + 1) * bin_step

    return {
        "poc": round(poc_price, 4),
        "vah": round(vah_price, 4),
        "val": round(val_price, 4),
        "total_volume": float(total_v),
    }

