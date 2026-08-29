import logging
from typing import List, Optional

from ..indicators.moving_averages import ema, ema_series, sma
from ..indicators.rsi import rsi
from ..models import Bias, Regime, TimeframeData

log = logging.getLogger("xauusd_bot.strategy.bias")


class BiasDetector:
    def __init__(self, ema_fast: int = 9, ema_medium: int = 21, ema_slow: int = 50,
                 rsi_period: int = 14, rsi_mid_upper: float = 60.0, rsi_mid_lower: float = 40.0):
        self.ema_fast = ema_fast
        self.ema_medium = ema_medium
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.rsi_mid_upper = rsi_mid_upper
        self.rsi_mid_lower = rsi_mid_lower

    def detect_bias(self, data: TimeframeData) -> Bias:
        c = data.close
        if len(c) < self.ema_slow + 10:
            return Bias.NEUTRAL
        if len(c) > 150:
            c = c[-150:]
        e9 = ema(c, self.ema_fast)
        e21 = ema(c, self.ema_medium)
        e50 = ema(c, self.ema_slow)
        r = rsi(c, self.rsi_period)
        if e9 is None or e21 is None or e50 is None or r is None:
            return Bias.NEUTRAL
        current_price = c[-1]
        if current_price > e9 > e21 > e50 and r > self.rsi_mid_lower:
            return Bias.BULLISH
        if current_price < e9 < e21 < e50 and r < self.rsi_mid_upper:
            return Bias.BEARISH
        return Bias.NEUTRAL

    def detect_regime(self, data: TimeframeData) -> Regime:
        c = data.close
        if len(c) < self.ema_slow + 10:
            return Regime.RANGING
        if len(c) > 150:
            c = c[-150:]
        e50 = ema(c, self.ema_slow)
        if e50 is None:
            return Regime.RANGING
        distance_pct = abs(c[-1] - e50) / e50 * 100
        if len(c) > self.ema_slow * 2:
            e50_vals = ema_series(c, self.ema_slow)
            slope = (e50_vals[-1] - e50_vals[-5]) / 5.0
        else:
            slope = 0

        # Support forex pricing (< 5.0, e.g. EURUSD at 1.08) as well as Gold / high nominals
        is_forex = c[-1] < 5.0
        min_dist = 0.15 if is_forex else 1.5
        min_slope = 0.0001 if is_forex else 0.01

        if distance_pct > min_dist and abs(slope) > min_slope:
            return Regime.TRENDING_BULL if slope > 0 else Regime.TRENDING_BEAR
        return Regime.RANGING

    def hysteresis_bias(self, data: TimeframeData, lookback: int = 3) -> Bias:
        biases = []
        for offset in range(min(lookback, len(data.close) // 20)):
            idx = -(offset * 20 + 1)
            if abs(idx) >= len(data.close):
                break
            segment = TimeframeData(
                tf=data.tf,
                time=data.time[:idx] if idx < 0 else [],
                open=data.open[:idx] if idx < 0 else [],
                high=data.high[:idx] if idx < 0 else [],
                low=data.low[:idx] if idx < 0 else [],
                close=data.close[:idx] if idx < 0 else [],
                tick_volume=data.tick_volume[:idx] if idx < 0 else [],
                spread=data.spread[:idx] if idx < 0 else [],
            )
            biases.append(self.detect_bias(segment))
        bullish_count = sum(1 for b in biases if b == Bias.BULLISH)
        bearish_count = sum(1 for b in biases if b == Bias.BEARISH)
        if bullish_count > bearish_count and bullish_count >= 2:
            return Bias.BULLISH
        if bearish_count > bullish_count and bearish_count >= 2:
            return Bias.BEARISH
        return Bias.NEUTRAL
