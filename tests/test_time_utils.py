from datetime import datetime, time, timezone, timedelta

from xauusd_bot.utils.time_utils import current_session, is_scalping_session, is_restricted_session, broker_date, minutes_since, _utcnow
from xauusd_bot.models import Session


def _t(h, m=0):
    return datetime(2025, 1, 15, h, m, tzinfo=timezone.utc).replace(tzinfo=None)


def test_session_london():
    s = current_session(now=_t(9, 0))
    assert s == Session.LONDON


def test_session_ny():
    s = current_session(now=_t(21, 0))  # NY only (London closed)
    assert s == Session.NY


def test_session_overlap():
    s = current_session(now=_t(15, 0))
    assert s == Session.LONDON_NY_OVERLAP


def test_session_asian():
    s = current_session(now=_t(3, 0))
    assert s == Session.ASIAN


def test_session_closed_before_london():
    s = current_session(now=_t(7, 0))
    assert s == Session.ASIAN


def test_session_closed_after_ny():
    s = current_session(now=_t(23, 0))
    assert s == Session.ASIAN


def test_scalping_sessions():
    assert is_scalping_session(Session.LONDON)
    assert is_scalping_session(Session.NY)
    assert is_scalping_session(Session.LONDON_NY_OVERLAP)
    assert not is_scalping_session(Session.ASIAN)
    assert not is_scalping_session(Session.CLOSED)


def test_restricted_session():
    assert is_restricted_session(Session.ASIAN)
    assert not is_restricted_session(Session.LONDON)
    assert not is_restricted_session(Session.NY)
    assert not is_restricted_session(Session.LONDON_NY_OVERLAP)
    assert not is_restricted_session(Session.CLOSED)


def test_broker_date_default():
    d = broker_date(now=_t(12, 0))
    assert d == "2025-01-15"


def test_broker_date_no_rollback():
    d = broker_date(now=_t(12, 0), reset_hour=5)
    assert d == "2025-01-15"


def test_broker_date_rollback():
    d = broker_date(now=_t(3, 0), reset_hour=5)
    assert d == "2025-01-14"


def test_broker_date_exactly_reset():
    d = broker_date(now=_t(5, 0), reset_hour=5)
    assert d == "2025-01-15"


def test_minutes_since():
    then = _utcnow() - timedelta(minutes=30)
    m = minutes_since(then)
    assert 29 <= m <= 31


def test_minutes_since_zero():
    m = minutes_since(_utcnow())
    assert m < 0.1