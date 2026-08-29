from typing import List, Optional


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
