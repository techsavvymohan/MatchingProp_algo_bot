import logging
from collections import deque
from datetime import datetime
from typing import Dict, Optional

from ..models import TimeframeData

_MIN_CHUNK_PCT = 0.9

log = logging.getLogger("xauusd_bot.data.ohlcv")

TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4"]
TF_BARS_LOOKBACK = {
    "M1": 100,
    "M5": 200,
    "M15": 200,
    "M30": 200,
    "H1": 200,
    "H4": 200,
}


class MultiTFData:
    def __init__(self, connector, symbol: str = "XAUUSD"):
        self.connector = connector
        self.symbol = symbol
        self._data: Dict[str, Optional[TimeframeData]] = {tf: None for tf in TIMEFRAMES}
        self._spread_history: Dict[str, deque] = {tf: deque(maxlen=50) for tf in TIMEFRAMES}

    def update_all(self) -> bool:
        ok = True
        for tf in TIMEFRAMES:
            try:
                self._update_tf(tf)
            except Exception as exc:
                log.error("Failed to update %s: %s", tf, exc)
                ok = False
        return ok

    def _update_tf(self, tf: str):
        if not self.connector.ensure_connected():
            return
        mt5_tf = self.connector.tf_to_mt5(tf)
        bars = self.connector.copy_rates_from_pos(self.symbol, mt5_tf, 0, TF_BARS_LOOKBACK[tf])
        if bars is None or len(bars) == 0:
            log.warning("No %s data returned", tf)
            return
        self._data[tf] = TimeframeData(
            tf=tf,
            time=[datetime.fromtimestamp(b[0]) for b in bars],
            open=[b[1] for b in bars],
            high=[b[2] for b in bars],
            low=[b[3] for b in bars],
            close=[b[4] for b in bars],
            tick_volume=[b[5] for b in bars],
            spread=[b[7] if len(b) > 7 else 0 for b in bars],
        )

    def get(self, tf: str) -> Optional[TimeframeData]:
        return self._data.get(tf)

    def latest_close(self, tf: str) -> float:
        d = self._data.get(tf)
        if d and d.close:
            return d.close[-1]
        return 0.0

    def latest_time(self, tf: str) -> Optional[datetime]:
        d = self._data.get(tf)
        if d and d.time:
            return d.time[-1]
        return None

    def aggregate_to_tf(self, target_tf: str, bars: int = 100) -> Optional[TimeframeData]:
        source_map = {
            "M5": "M1",
            "M15": "M5",
            "M30": "M15",
            "H1": "M15",
            "H4": "H1",
        }
        src_tf = source_map.get(target_tf)
        if not src_tf:
            return self.get(target_tf)
        src_data = self._data.get(src_tf)
        if src_data is None:
            return None
        agg_factor = {
            "M5": 5,
            "M15": 3,
            "M30": 2,
            "H1": 4,
            "H4": 4,
        }.get(target_tf, 1)
        times, opens, highs, lows, closes, volumes, spreads = [], [], [], [], [], [], []
        total_bars = len(src_data.close)
        for i in range(0, total_bars, agg_factor):
            end = min(i + agg_factor, total_bars)
            if end - i < agg_factor and (end - i) / agg_factor < _MIN_CHUNK_PCT:
                break
            chunk = slice(i, end)
            times.append(src_data.time[i])
            opens.append(src_data.open[i])
            highs.append(max(src_data.high[chunk]))
            lows.append(min(src_data.low[chunk]))
            closes.append(src_data.close[end - 1])
            volumes.append(sum(src_data.tick_volume[chunk]))
            spreads.append(sum(src_data.spread[chunk]) // max(len(src_data.spread[chunk]), 1))
        return TimeframeData(target_tf, times[-bars:], opens[-bars:], highs[-bars:],
                             lows[-bars:], closes[-bars:], volumes[-bars:], spreads[-bars:])

    def all_tfs(self) -> Dict[str, TimeframeData]:
        return {tf: d for tf, d in self._data.items() if d is not None}

    def current_spread(self) -> float:
        tick = self.connector.symbol_info_tick(self.symbol)
        if tick is None:
            return 0.0
        pt = None
        if hasattr(self.connector, "point_size"):
            try:
                res = self.connector.point_size(self.symbol)
                if isinstance(res, (int, float)):
                    pt = res
            except Exception:
                pass
        if pt is None:
            info = self.connector.symbol_info(self.symbol)
            if info and hasattr(info, "point") and isinstance(info.point, (int, float)):
                pt = info.point
        if not pt:
            pt = 0.0001 if "EUR" in self.symbol else 0.01
        return (tick.ask - tick.bid) / pt
