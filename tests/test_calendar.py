import time as time_module
from datetime import datetime, timezone, timedelta

from xauusd_bot.data.economic_calendar import EconomicCalendar


def test_calendar_default_fetch():
    cal = EconomicCalendar()
    result = cal.fetch()
    assert result
    assert len(cal._events) > 0


def test_calendar_upcoming_events_sorted():
    cal = EconomicCalendar()
    cal.fetch()
    events = cal.upcoming_events
    if events:
        timestamps = [e["timestamp"] for e in events]
        assert timestamps == sorted(timestamps)


def test_is_blocked_no_api():
    cal = EconomicCalendar()
    cal._events = []
    ok, reason = cal.is_blocked()
    assert not ok
    assert reason is None


def test_is_blocked_with_event():
    cal = EconomicCalendar()
    now = datetime.now(timezone.utc).timestamp()
    cal._events = [{"title": "NFP", "currency": "USD", "impact": "high", "timestamp": now + 60}]
    ok, reason = cal.is_blocked(before_minutes=30, after_minutes=30)
    assert ok
    assert reason is not None


def test_is_blocked_far_away():
    cal = EconomicCalendar()
    now = datetime.now(timezone.utc).timestamp()
    cal._events = [{"title": "NFP", "currency": "USD", "impact": "high", "timestamp": now + 3600}]
    ok, _ = cal.is_blocked(before_minutes=30, after_minutes=30)
    assert not ok


def test_is_blocked_non_usd_ignored():
    cal = EconomicCalendar()
    now = datetime.now(timezone.utc).timestamp()
    cal._events = [{"title": "EUR CPI", "currency": "EUR", "impact": "high", "timestamp": now + 60}]
    ok, _ = cal.is_blocked(before_minutes=30, after_minutes=30)
    assert not ok


def test_calendar_events_after_now():
    cal = EconomicCalendar()
    cal._events = [{"title": "Past Event", "currency": "USD", "timestamp": datetime.now(timezone.utc).timestamp() - 3600}]
    upcoming = cal.upcoming_events
    assert len(upcoming) == 0