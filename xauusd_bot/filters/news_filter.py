import logging
from typing import Tuple

from ..data.economic_calendar import EconomicCalendar

log = logging.getLogger("xauusd_bot.filters.news")


class NewsFilter:
    def __init__(self, calendar: EconomicCalendar, before_min: int = 30, after_min: int = 30):
        self.calendar = calendar
        self.before_min = before_min
        self.after_min = after_min

    def check(self, symbol: str = "XAUUSD") -> Tuple[bool, str]:
        blocked, reason = self.calendar.is_blocked(self.before_min, self.after_min, symbol=symbol)
        if blocked:
            return False, reason or "news block active"
        return True, "no high-impact news"

    def update_fetch(self) -> bool:
        return self.calendar.fetch()
