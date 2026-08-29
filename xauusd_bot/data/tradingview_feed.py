"""TradingView Technical Analysis Feed.

Uses the official tradingview-ta library to query live TradingView
recommendations, oscillators, moving averages, and indicator metrics
for XAUUSD, EURUSD, and other symbols.
"""
import logging
import time
from typing import Any, Dict, Optional, Tuple

try:
    from tradingview_ta import Interval, TA_Handler
    TV_AVAILABLE = True
except ImportError:
    TV_AVAILABLE = False
    Interval = None
    TA_Handler = None

from ..models import Bias

log = logging.getLogger("xauusd_bot.data.tradingview")

# Symbol routing to TradingView screener and exchange
DEFAULT_EXCHANGE_MAP = {
    "XAUUSD": ("OANDA", "cfd"),
    "GOLD": ("OANDA", "cfd"),
    "EURUSD": ("FX_IDC", "forex"),
    "GBPUSD": ("FX_IDC", "forex"),
    "USDJPY": ("FX_IDC", "forex"),
}


class TradingViewFeed:
    def __init__(self, cache_ttl_seconds: float = 45.0, enabled: bool = True):
        self.cache_ttl = cache_ttl_seconds
        self.enabled = enabled and TV_AVAILABLE
        self._cache: Dict[str, Tuple[float, Any]] = {}

        if not TV_AVAILABLE:
            log.warning("tradingview-ta library not installed. TradingView feed disabled.")

    @staticmethod
    def map_tf_to_interval(tf: str) -> str:
        if not TV_AVAILABLE:
            return "15m"
        mapping = {
            "M1": Interval.INTERVAL_1_MINUTE,
            "M5": Interval.INTERVAL_5_MINUTES,
            "M15": Interval.INTERVAL_15_MINUTES,
            "M30": Interval.INTERVAL_30_MINUTES,
            "H1": Interval.INTERVAL_1_HOUR,
            "H4": Interval.INTERVAL_4_HOURS,
            "D1": Interval.INTERVAL_1_DAY,
        }
        return mapping.get(tf.upper(), Interval.INTERVAL_15_MINUTES)

    def _get_exchange_screener(self, symbol: str) -> Tuple[str, str]:
        sym = symbol.upper()
        return DEFAULT_EXCHANGE_MAP.get(sym, ("FX_IDC", "forex"))

    def get_analysis(self, symbol: str = "XAUUSD", tf: str = "M15") -> Optional[Any]:
        """Fetch analysis object from TradingView with caching."""
        if not self.enabled:
            return None

        cache_key = f"{symbol.upper()}_{tf.upper()}"
        now = time.time()
        if cache_key in self._cache:
            ts, cached_res = self._cache[cache_key]
            if now - ts < self.cache_ttl:
                return cached_res

        exchange, screener = self._get_exchange_screener(symbol)
        interval = self.map_tf_to_interval(tf)

        try:
            handler = TA_Handler(
                symbol=symbol.upper(),
                exchange=exchange,
                screener=screener,
                interval=interval,
                timeout=5.0,
            )
            analysis = handler.get_analysis()
            self._cache[cache_key] = (now, analysis)
            return analysis
        except Exception as exc:
            log.warning("TradingView fetch failed for %s (%s): %s", symbol, tf, exc)
            return None

    def get_recommendation(self, symbol: str = "XAUUSD", tf: str = "M15") -> str:
        """Get summary recommendation: STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL."""
        analysis = self.get_analysis(symbol, tf)
        if not analysis or not hasattr(analysis, "summary"):
            return ""
        return analysis.summary.get("RECOMMENDATION", "")

    def get_bias(self, symbol: str = "XAUUSD", tf: str = "M15") -> Bias:
        """Convert TradingView consensus into Bias enum."""
        rec = self.get_recommendation(symbol, tf)
        if "BUY" in rec:
            return Bias.BULLISH
        elif "SELL" in rec:
            return Bias.BEARISH
        return Bias.NEUTRAL

    def is_sideways(self, symbol: str = "XAUUSD", tf: str = "M15") -> Tuple[bool, str]:
        """Check if TradingView signals sideways / consolidation.

        Returns (True, reason) if TradingView rates market as NEUTRAL or
        neutral indicators dominate.
        """
        analysis = self.get_analysis(symbol, tf)
        if not analysis or not hasattr(analysis, "summary"):
            return False, "no tradingview data"

        summary = analysis.summary
        rec = summary.get("RECOMMENDATION", "")
        neutral_votes = summary.get("NEUTRAL", 0)
        buy_votes = summary.get("BUY", 0)
        sell_votes = summary.get("SELL", 0)
        total_votes = neutral_votes + buy_votes + sell_votes

        if rec == "NEUTRAL" and neutral_votes >= max(buy_votes, sell_votes):
            return True, f"TradingView {tf} Neutral ({neutral_votes}/{total_votes} indicators neutral)"

        return False, f"TradingView trend {rec}"

    def get_indicators(self, symbol: str = "XAUUSD", tf: str = "M15") -> Dict[str, Any]:
        """Retrieve full dictionary of TradingView technical indicators."""
        analysis = self.get_analysis(symbol, tf)
        if not analysis or not hasattr(analysis, "indicators"):
            return {}
        return analysis.indicators or {}
