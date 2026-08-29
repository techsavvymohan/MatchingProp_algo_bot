import logging
from typing import Optional

log = logging.getLogger("xauusd_bot.risk.max_dd")


class MaxDDTracker:
    def __init__(self, max_dd_pct: float = 10.0, buffer_pct: float = 2.0):
        self.max_dd_pct = max_dd_pct
        self.buffer_pct = buffer_pct
        self._peak_equity: float = 0.0
        self._current_dd_pct: float = 0.0
        self._breached: bool = False

    def update(self, equity: float):
        if equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity > 0:
            self._current_dd_pct = max(0.0, (self._peak_equity - equity) / self._peak_equity * 100)

    @property
    def current_dd_pct(self) -> float:
        return self._current_dd_pct

    def is_near_limit(self) -> bool:
        effective_limit = self.max_dd_pct - self.buffer_pct
        return self._current_dd_pct >= effective_limit

    def is_breached(self) -> bool:
        return self._current_dd_pct >= self.max_dd_pct

    def kill_switch_engaged(self) -> bool:
        if self._breached:
            return True
        if self.is_breached():
            self._breached = True
            log.critical("MAX DD BREACHED: %.2f%% (limit=%.2f%%)", self._current_dd_pct, self.max_dd_pct)
            return True
        if self.is_near_limit():
            log.warning("Near max DD limit: %.2f%% (buffer limit=%.2f%%)",
                        self._current_dd_pct, self.max_dd_pct - self.buffer_pct)
        return self._breached

    def reset(self, peak_equity: float = 0.0):
        self._peak_equity = peak_equity
        self._current_dd_pct = 0.0
        self._breached = False
