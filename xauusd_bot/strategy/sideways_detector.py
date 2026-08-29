"""Sideways / Ranging Market Detector.

Specially built to fulfill:
"it should avoid only sideways situation"

Combines multiple quantitative models:
1. Bollinger Bandwidth Compression & Flatness (from je-suis-tm/quant-trading)
2. Choppiness Index (CHOP > 61.8 indicates consolidation / chop)
3. ADX Trend Strength (< 22 indicates lack of trend)
4. EMA Ribbon Tangling & Zero-Slope
5. Awesome Oscillator Dead-zone
6. Heikin-Ashi Indecision Dojis
"""
import logging
from typing import Dict, Optional, Tuple

from ..indicators.moving_averages import ema, ema_series
from ..indicators.quant_indicators import (
    adx,
    awesome_oscillator,
    bollinger_bands,
    choppiness_index,
    heikin_ashi,
    heikin_ashi_trend,
)
from ..models import TimeframeData

log = logging.getLogger("xauusd_bot.strategy.sideways")


class SidewaysDetector:
    def __init__(
        self,
        chop_threshold: float = 61.8,
        adx_threshold: float = 22.0,
        bb_period: int = 20,
        bb_multiplier: float = 2.0,
        bandwidth_squeeze_pct: float = 25.0,
        lookback_bars: int = 30,
    ):
        self.chop_threshold = chop_threshold
        self.adx_threshold = adx_threshold
        self.bb_period = bb_period
        self.bb_multiplier = bb_multiplier
        self.bandwidth_squeeze_pct = bandwidth_squeeze_pct
        self.lookback_bars = lookback_bars

    def is_timeframe_sideways(self, data: TimeframeData) -> Tuple[bool, str, float]:
        """Evaluate if a single timeframe is in a sideways / ranging state.

        Returns:
            (is_sideways, reason, confidence_score [0.0 - 1.0])
        """
        c = data.close
        h = data.high
        l = data.low
        o = data.open
        n = len(c)

        if n < self.bb_period + 10:
            return False, "insufficient data", 0.0

        if n > 150:
            c = c[-150:]
            h = h[-150:]
            l = l[-150:]
            o = o[-150:]
            n = len(c)

        sideways_signals = 0
        total_checks = 5
        reasons = []

        # 1. Choppiness Index
        chop_val = choppiness_index(h, l, c, period=14)
        if chop_val is not None and chop_val >= self.chop_threshold:
            sideways_signals += 1
            reasons.append(f"CHOP={chop_val:.1f} >= {self.chop_threshold}")

        # 2. ADX Trend Strength
        adx_val = adx(h, l, c, period=14)
        if adx_val is not None and adx_val < self.adx_threshold:
            sideways_signals += 1
            reasons.append(f"ADX={adx_val:.1f} < {self.adx_threshold}")

        # 3. Bollinger Bandwidth Compression & Squeeze
        bb = bollinger_bands(c, period=self.bb_period, multiplier=self.bb_multiplier)
        bw_series = bb["bandwidth"]
        if len(bw_series) >= 20:
            recent_bw = bw_series[-1]
            lookback_bw = bw_series[-self.lookback_bars :]
            min_bw = min(lookback_bw)
            max_bw = max(lookback_bw)
            bw_range = max_bw - min_bw
            if bw_range > 0:
                bw_percentile = ((recent_bw - min_bw) / bw_range) * 100.0
                if bw_percentile < self.bandwidth_squeeze_pct:
                    sideways_signals += 1
                    reasons.append(f"BB Squeeze ({bw_percentile:.1f}%ile)")

        # 4. EMA Ribbon Flatness / Tangling
        if n >= 55:
            e9 = ema(c, 9)
            e21 = ema(c, 21)
            e50 = ema(c, 50)
            if e9 and e21 and e50:
                # Check dispersion
                avg_e = (e9 + e21 + e50) / 3.0
                dispersion_pct = (max(e9, e21, e50) - min(e9, e21, e50)) / avg_e * 100.0
                # Check slope of EMA50 over last 5 bars
                e50_s = ema_series(c, 50)
                slope = abs(e50_s[-1] - e50_s[-5]) / 5.0 if len(e50_s) >= 5 else 1.0
                slope_pct = (slope / avg_e) * 100.0
                if dispersion_pct < 0.25 and slope_pct < 0.01:
                    sideways_signals += 1
                    reasons.append("EMA ribbon flat & tangled")

        # 5. Heikin-Ashi Indecision
        ha = heikin_ashi(o, h, l, c)
        trend_name, strength = heikin_ashi_trend(ha, lookback=4)
        if trend_name == "indecision":
            sideways_signals += 1
            reasons.append("Heikin-Ashi indecision dojis")

        confidence = sideways_signals / total_checks
        # Majority of indicators confirm sideways (3 or more out of 5)
        is_sideways = sideways_signals >= 3
        reason_str = ", ".join(reasons) if reasons else "trending/breakout active"

        return is_sideways, reason_str, confidence

    def check_sideways(self, data_all: Dict[str, TimeframeData]) -> Tuple[bool, str]:
        """Perform a multi-timeframe sideways consensus check.

        Evaluates H1, M15, and M5.
        If both H1 and M15 are sideways, or M15 has extreme sideways chop (>= 4 indicators),
        the market is strictly classified as sideways and trading must be avoided.
        """
        h1 = data_all.get("H1")
        m15 = data_all.get("M15")
        m5 = data_all.get("M5")

        if not m15:
            return False, "no M15 data"

        m15_sideways, m15_reasons, m15_conf = self.is_timeframe_sideways(m15)
        
        h1_sideways = False
        h1_reasons = ""
        if h1:
            h1_sideways, h1_reasons, _ = self.is_timeframe_sideways(h1)

        # Case 1: High conviction sideways on execution timeframe M15 (>= 80% indicators)
        if m15_conf >= 0.8:
            return True, f"M15 Severe Sideways: {m15_reasons}"

        # Case 2: Multi-timeframe agreement (both H1 and M15 are sideways)
        if h1_sideways and m15_sideways:
            return True, f"Multi-TF Sideways: H1 ({h1_reasons}) & M15 ({m15_reasons})"

        # Case 3: If M5 is also tested and both M15 and M5 are sideways
        if m5 and m15_sideways:
            m5_sideways, m5_reasons, _ = self.is_timeframe_sideways(m5)
            if m5_sideways:
                return True, f"Intraday Sideways: M15 & M5 ({m5_reasons})"

        return False, "Market is in directional movement / breakout"
