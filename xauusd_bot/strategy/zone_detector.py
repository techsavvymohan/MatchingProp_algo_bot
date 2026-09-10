import logging
from typing import Dict, Optional, Tuple

from ..indicators.moving_averages import (
    calc_volume_profile,
    vwap as calc_vwap,
    vwap_bands as calc_vwap_bands,
)
from ..models import Bias, TimeframeData

log = logging.getLogger("xauusd_bot.strategy.zone")


class ZoneDetector:
    def __init__(self, vwap_period: int = 20, swing_bars_min: int = 5, swing_bars_max: int = 20):
        self.vwap_period = vwap_period
        self.swing_bars_min = swing_bars_min
        self.swing_bars_max = swing_bars_max

    def detect_pullback_zone(self, data: TimeframeData, bias: Bias) -> Optional[Tuple[float, float]]:
        if bias == Bias.NEUTRAL:
            return None
        c = data.close
        if len(c) < self.vwap_period + 5:
            return None
        v = calc_vwap(data.high, data.low, c, data.tick_volume, self.vwap_period)
        if v is None:
            return None
        recent_high = max(c[-20:])
        recent_low = min(c[-20:])
        if bias == Bias.BULLISH:
            zone_top = v
            zone_bottom = recent_low
            return (zone_bottom, zone_top) if zone_bottom < zone_top else (zone_top * 0.995, zone_top)
        else:
            zone_bottom = v
            zone_top = recent_high
            return (zone_bottom, zone_top) if zone_bottom < zone_top else (zone_bottom, zone_bottom * 1.005)

    def price_in_zone(self, price: float, zone: Optional[Tuple[float, float]]) -> bool:
        if zone is None:
            return False
        return zone[0] <= price <= zone[1]

    def detect_auction_value_area(
        self,
        data: TimeframeData,
        lookback: int = 50,
        bins: int = 50,
        value_area_pct: float = 0.70,
    ) -> Optional[Dict[str, float]]:
        """Calculate Auction Market Theory Value Area (POC, VAH, VAL) from TimeframeData."""
        if not data.close or len(data.close) < 5:
            return None
        return calc_volume_profile(
            high=data.high,
            low=data.low,
            close=data.close,
            volume=data.tick_volume,
            bins=bins,
            value_area_pct=value_area_pct,
            lookback=lookback,
        )

    def detect_vwap_extremes(
        self,
        data: TimeframeData,
        period: int = 20,
    ) -> Optional[Tuple[float, float, float, float, float]]:
        """Calculate Session/Rolling VWAP and ±1σ, ±2σ bands."""
        if not data.close or len(data.close) < period:
            return None
        return calc_vwap_bands(
            high=data.high,
            low=data.low,
            close=data.close,
            volume=data.tick_volume,
            period=period,
        )

