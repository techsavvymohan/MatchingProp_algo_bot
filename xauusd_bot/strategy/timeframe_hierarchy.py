import logging
from typing import Dict, List, Optional, Tuple

from ..models import Bias, Regime, Session, TimeframeData
from .bias_detector import BiasDetector
from .zone_detector import ZoneDetector
from ..indicators.quant_indicators import (
    awesome_oscillator,
    check_ao_saucer,
    heikin_ashi,
    heikin_ashi_trend,
)

log = logging.getLogger("xauusd_bot.strategy.hierarchy")


class TimeframeHierarchy:
    def __init__(self, bias_detector: BiasDetector, zone_detector: ZoneDetector,
                 sideways_detector=None, session_agnostic: bool = False):
        self.bias = bias_detector
        self.zone = zone_detector
        self.sideways = sideways_detector
        self.session_agnostic = session_agnostic

    def evaluate(self, data_all: Dict[str, TimeframeData], session: Session) -> dict:
        result = {
            "h4_bias": Bias.NEUTRAL,
            "h1_bias": Bias.NEUTRAL,
            "m15_bias": Bias.NEUTRAL,
            "m5_bias": Bias.NEUTRAL,
            "m1_bias": Bias.NEUTRAL,
            "regime": Regime.RANGING,
            "m15_zone": None,
            "allowed_direction": None,
            "entry_tier": None,
            "alignment_score": 0,
            "alignment_count": 0,
            "is_sideways": False,
            "sideways_reason": "",
            "ao_saucer": False,
            "ha_trend": "",
        }

        # Check sideways avoidance first if detector is present
        if self.sideways is not None:
            is_sw, sw_reason = self.sideways.check_sideways(data_all)
            result["is_sideways"] = is_sw
            result["sideways_reason"] = sw_reason
            if is_sw:
                log.debug("Sideways regime active — blocking signal generation: %s", sw_reason)
                result["regime"] = Regime.RANGING
                result["entry_tier"] = self._select_entry_tier(session, result["regime"])
                return result

        h4_data = data_all.get("H4")
        h1_data = data_all.get("H1")
        m15_data = data_all.get("M15")
        m5_data = data_all.get("M5")
        m1_data = data_all.get("M1")

        if h4_data:
            result["h4_bias"] = self.bias.detect_bias(h4_data)
            result["regime"] = self.bias.detect_regime(h4_data)
        if h1_data:
            result["h1_bias"] = self.bias.detect_bias(h1_data)
        if m15_data:
            result["m15_bias"] = self.bias.detect_bias(m15_data)
        if m5_data:
            result["m5_bias"] = self.bias.detect_bias(m5_data)
        if m1_data:
            result["m1_bias"] = self.bias.detect_bias(m1_data)

        h4 = result["h4_bias"]
        h1 = result["h1_bias"]

        if h4 == Bias.NEUTRAL and h1 != Bias.NEUTRAL:
            result["allowed_direction"] = h1.value
        elif h4 != Bias.NEUTRAL and h1 == Bias.NEUTRAL:
            result["allowed_direction"] = h4.value
        elif h4 == h1 and h4 != Bias.NEUTRAL:
            result["allowed_direction"] = h4.value
        elif h4 != Bias.NEUTRAL and h1 != Bias.NEUTRAL and h4 != h1:
            result["allowed_direction"] = h4.value
        else:
            result["allowed_direction"] = None

        # Heikin-Ashi and AO Saucer verification from je-suis-tm/quant-trading
        if m15_data and len(m15_data.close) >= 10:
            ha = heikin_ashi(m15_data.open, m15_data.high, m15_data.low, m15_data.close)
            ha_tr, _ = heikin_ashi_trend(ha, lookback=3)
            result["ha_trend"] = ha_tr

        if m15_data and len(m15_data.close) >= 35 and result["allowed_direction"]:
            ao = awesome_oscillator(m15_data.high, m15_data.low)
            if check_ao_saucer(ao, result["allowed_direction"]):
                result["ao_saucer"] = True

        if m15_data and result["allowed_direction"]:
            allowed = Bias.BULLISH if result["allowed_direction"] == "bullish" else Bias.BEARISH
            zone = self.zone.detect_pullback_zone(m15_data, allowed)
            result["m15_zone"] = zone

        result["alignment_count"] = self._count_aligned(data_all, result)
        result["alignment_score"] = self._score_alignment(result)

        result["entry_tier"] = self._select_entry_tier(session, result["regime"])

        return result

    def _count_aligned(self, data_all: Dict[str, TimeframeData], result: dict) -> int:
        allowed = result.get("allowed_direction")
        if allowed is None:
            return 0
        count = 0
        for tf_name in ["h4_bias", "h1_bias", "m15_bias", "m5_bias", "m1_bias"]:
            tf_bias = result.get(tf_name, Bias.NEUTRAL)
            if tf_bias != Bias.NEUTRAL and tf_bias.value == allowed:
                count += 1
        return count

    def _score_alignment(self, result: dict) -> int:
        score = 0
        mapping = {"h4_bias": 3, "h1_bias": 2, "m15_bias": 2, "m5_bias": 1, "m1_bias": 1}
        allowed = result.get("allowed_direction")
        if allowed is None:
            return 0
        for tf_name, weight in mapping.items():
            tf_bias = result.get(tf_name, Bias.NEUTRAL)
            if tf_bias != Bias.NEUTRAL and tf_bias.value == allowed:
                score += weight
        if result.get("regime") in (Regime.TRENDING_BULL, Regime.TRENDING_BEAR):
            regime_dir = "bullish" if result["regime"] == Regime.TRENDING_BULL else "bearish"
            if regime_dir == allowed:
                score += 2
        # Bonus for quant confluence
        if result.get("ao_saucer"):
            score += 1
        if result.get("ha_trend") == allowed:
            score += 1
        return score

    def _select_entry_tier(self, session: Session, regime: Regime) -> str:
        if self.session_agnostic:
            if regime != Regime.RANGING:
                return "M1"
            return "M15"
        if session in (Session.LONDON, Session.NY, Session.LONDON_NY_OVERLAP) and regime != Regime.RANGING:
            return "M1"
        if session in (Session.LONDON, Session.NY, Session.LONDON_NY_OVERLAP):
            return "M5"
        return "M15"
