import logging
from datetime import datetime
from typing import Tuple

from ..models import Session
from ..utils.time_utils import current_session

log = logging.getLogger("xauusd_bot.filters.session")


class SessionFilter:
    def __init__(self, london_open: str = "08:00", london_close: str = "17:00",
                 ny_open: str = "13:00", ny_close: str = "22:00",
                 enabled: bool = True):
        self.london_open = london_open
        self.london_close = london_close
        self.ny_open = ny_open
        self.ny_close = ny_close
        self.enabled = enabled

    def check(self, now: datetime | None = None) -> Tuple[bool, str]:
        if not self.enabled:
            return True, "all_sessions_allowed"
        sess = current_session(now, self.london_open, self.london_close, self.ny_open, self.ny_close)
        if sess == Session.CLOSED:
            return False, f"Market closed: {sess.value}"
        return True, sess.value

    def allowed_entry_tfs(self, now: datetime | None = None) -> list[str]:
        if not self.enabled:
            return ["M1", "M5", "M15", "M30"]
        sess = current_session(now, self.london_open, self.london_close, self.ny_open, self.ny_close)
        if sess in (Session.LONDON, Session.NY, Session.LONDON_NY_OVERLAP):
            return ["M1", "M5", "M15", "M30"]
        return ["M15", "M30"]


