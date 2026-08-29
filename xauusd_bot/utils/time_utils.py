from datetime import datetime, time, timedelta, timezone
from typing import Optional

from ..models import Session


def _parse_time(t_str: str) -> time:
    parts = t_str.strip().split(":")
    return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def current_session(
    now: Optional[datetime] = None,
    london_open: str = "08:00",
    london_close: str = "17:00",
    ny_open: str = "13:00",
    ny_close: str = "22:00",
) -> Session:
    if now is None:
        now = _utcnow()
    t = now.time()

    lo = _parse_time(london_open)
    lc = _parse_time(london_close)
    no = _parse_time(ny_open)
    nc = _parse_time(ny_close)

    if lo <= t < lc and no <= t < nc:
        return Session.LONDON_NY_OVERLAP
    if lo <= t < lc:
        return Session.LONDON
    if no <= t < nc:
        return Session.NY
    if t < lo or t >= nc:
        return Session.ASIAN
    return Session.CLOSED


def is_scalping_session(session: Session) -> bool:
    return session in (Session.LONDON, Session.NY, Session.LONDON_NY_OVERLAP)


def is_restricted_session(session: Session) -> bool:
    return session == Session.ASIAN


def broker_date(
    now: Optional[datetime] = None,
    reset_hour: int = 0,
    reset_tz: str = "UTC",
) -> str:
    if now is None:
        now = _utcnow()
    if reset_tz.upper() != "UTC" and reset_hour == 0:
        now = now
    if now.hour < reset_hour:
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")


def minutes_since(then: datetime) -> float:
    return (_utcnow() - then).total_seconds() / 60.0
