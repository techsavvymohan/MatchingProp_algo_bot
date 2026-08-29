import logging
from collections import deque
from typing import Optional

log = logging.getLogger("xauusd_bot.data.spread")


class SpreadTracker:
    def __init__(self, lookback: int = 50):
        self.lookback = lookback
        self._history: deque = deque(maxlen=lookback)
        self._current: float = 0.0

    def update(self, spread: float):
        self._current = spread
        self._history.append(spread)

    @property
    def current(self) -> float:
        return self._current

    @property
    def average(self) -> float:
        if not self._history:
            return self._current
        return sum(self._history) / len(self._history)

    @property
    def max(self) -> float:
        return max(self._history) if self._history else self._current

    @property
    def min(self) -> float:
        return min(self._history) if self._history else self._current

    @property
    def std(self) -> float:
        if len(self._history) < 2:
            return 0.0
        avg = self.average
        variance = sum((s - avg) ** 2 for s in self._history) / (len(self._history) - 1)
        return variance ** 0.5

    def is_spike(self, multiplier: float = 2.0) -> bool:
        if len(self._history) < 5:
            return False
        return self._current > self.average * multiplier

    def reset(self):
        self._history.clear()
        self._current = 0.0
