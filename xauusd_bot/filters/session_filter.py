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

    def check_ny_liquidity_session(
        self,
        now: datetime | None = None,
        start_time: str = "10:00",
        end_time: str = "11:00",
        tz_name: str = "America/New_York",
    ) -> Tuple[bool, str]:
        from ..utils.time_utils import is_in_ny_session, to_ny_time
        in_sess = is_in_ny_session(now, start_time, end_time, tz_name)
        ny_time_str = to_ny_time(now, tz_name).strftime("%H:%M:%S")
        if in_sess:
            return True, f"NY Liquidity Window Active [{ny_time_str} NY]"
        return False, f"Outside NY Liquidity Window [{ny_time_str} NY, target: {start_time}-{end_time}]"



