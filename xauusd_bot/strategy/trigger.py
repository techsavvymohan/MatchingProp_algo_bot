import logging
from typing import List, Optional, Tuple

from ..indicators.moving_averages import ema, ema_series
from ..indicators.rsi import rsi
from ..models import Bias, TimeframeData, TradeDirection

log = logging.getLogger("xauusd_bot.strategy.trigger")


class TriggerDetector:
    def __init__(self, ema_fast: int = 9, rsi_period: int = 14,
                 rsi_mid_upper: float = 60.0, rsi_mid_lower: float = 40.0):
        self.ema_fast = ema_fast
        self.rsi_period = rsi_period
        self.rsi_mid_upper = rsi_mid_upper
        self.rsi_mid_lower = rsi_mid_lower

    def check_momentum_continuation(self, data: TimeframeData, direction: TradeDirection) -> Tuple[bool, str]:
        c = data.close
        if len(c) < self.ema_fast + 5:
            return False, "insufficient data"
        e = ema(c, self.ema_fast)
        r = rsi(c, self.rsi_period)
        if e is None or r is None:
            return False, "indicator calc failed"
        if direction == TradeDirection.BUY:
            if c[-1] > e and self.rsi_mid_lower <= r <= self.rsi_mid_upper:
                return True, "bullish momentum + mid-range RSI"
            return False, f"buy fail: price={c[-1]:.2f} ema={e:.2f} rsi={r:.1f}"
        else:
            if c[-1] < e and self.rsi_mid_lower <= r <= self.rsi_mid_upper:
                return True, "bearish momentum + mid-range RSI"
            return False, f"sell fail: price={c[-1]:.2f} ema={e:.2f} rsi={r:.1f}"

    def check_micro_structure_break(self, data: TimeframeData, direction: TradeDirection,
                                    lookback: int = 3) -> Tuple[bool, float]:
        c = data.high if direction == TradeDirection.BUY else data.low
        if len(c) < lookback + 2:
            return False, 0.0
        recent = c[-(lookback + 1):-1]
        current = c[-1]
        if direction == TradeDirection.BUY and current > max(recent):
            return True, current
        if direction == TradeDirection.SELL and current < min(recent):
            return True, current
        return False, current

    def check_zone_entry(self, price: float, zone: Optional[Tuple[float, float]],
                         direction: TradeDirection) -> Tuple[bool, str]:
        if zone is None:
            return False, "no zone defined"
        zone_low, zone_high = zone
        if direction == TradeDirection.BUY:
            if zone_low <= price <= zone_high:
                return True, f"price {price:.2f} in buy zone [{zone_low:.2f}, {zone_high:.2f}]"
            return False, f"price {price:.2f} outside buy zone"
        else:
            if zone_low <= price <= zone_high:
                return True, f"price {price:.2f} in sell zone [{zone_low:.2f}, {zone_high:.2f}]"
            return False, f"price {price:.2f} outside sell zone"

    def check_ema_stack_alignment(self, data: TimeframeData,
                                  fast: int = 9, medium: int = 21, slow: int = 50) -> Tuple[bool, str]:
        c = data.close
        if len(c) < slow + 5:
            return False, "insufficient data"
        e_fast = ema(c, fast)
        e_med = ema(c, medium)
        e_slow = ema(c, slow)
        if any(x is None for x in [e_fast, e_med, e_slow]):
            return False, "ema calc failed"
        if e_fast > e_med > e_slow and c[-1] > e_fast:
            return True, "bullish stack"
        if e_fast < e_med < e_slow and c[-1] < e_fast:
            return True, "bearish stack"
        return False, "no stack alignment"
