import logging
from typing import Tuple

from ..data.spread import SpreadTracker

log = logging.getLogger("xauusd_bot.filters.spread")


class SpreadFilter:
    def __init__(self, tracker: SpreadTracker, max_multiplier: float = 1.5):
        self.tracker = tracker
        self.max_multiplier = max_multiplier

    def check(self) -> Tuple[bool, str]:
        if self.tracker.average <= 0:
            return True, "no spread data yet"
        if self.tracker.current > self.tracker.average * self.max_multiplier:
            return False, (
                f"spread {self.tracker.current:.1f} > "
                f"avg {self.tracker.average:.1f} × {self.max_multiplier}"
            )
        return True, f"spread {self.tracker.current:.1f} ok"

    def spike_blocked(self) -> bool:
        return self.tracker.is_spike(self.max_multiplier)
