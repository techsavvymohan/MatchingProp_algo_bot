import time as time_module
from datetime import datetime, timezone, timedelta

from xauusd_bot.data.economic_calendar import EconomicCalendar, CalendarEvent


def test_calendar_default_fetch():
    cal = EconomicCalendar()
    result = cal.fetch()
    # Either network succeeded and loaded authentic events, or network was unreachable and events list is empty.
    # In neither case should any artificial fallback events ever be fabricated.
    if result:
        assert len(cal.events) > 0
        for ev in cal.events:
            curr = ev.currency if hasattr(ev, "currency") else ev["currency"]
            assert len(curr) >= 2
    else:
        assert len(cal.events) == 0


def test_calendar_upcoming_events_sorted():
    cal = EconomicCalendar()
    now_ts = datetime.now(timezone.utc).timestamp()
    cal._events = [
        {"title": "B", "currency": "USD", "impact": "high", "timestamp": now_ts + 200},
        {"title": "A", "currency": "USD", "impact": "high", "timestamp": now_ts + 100},
    ]
    events = cal.upcoming_events
    assert len(events) == 2
    timestamps = [e["timestamp"] if isinstance(e, dict) else e.timestamp for e in events]
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


def test_calendar_offline_zero_artificial_events(monkeypatch):
    import urllib.request
    import requests

    def mock_urlopen(*args, **kwargs):
        raise urllib.error.URLError("No network connection")

    def mock_requests_get(*args, **kwargs):
        raise requests.exceptions.ConnectionError("No network connection")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setattr(requests, "get", mock_requests_get)
    monkeypatch.setenv("LSE_API_KEY", "")
    cal = EconomicCalendar()
    result = cal.fetch(force=True)
    assert not result
    assert len(cal._events) == 0
    assert len(cal.events) == 0
    blocked, reason = cal.is_blocked()
    assert not blocked
    assert reason is None


def test_calendar_event_dataclass_access():
    now_ts = datetime.now(timezone.utc).timestamp()
    ev = CalendarEvent(
        title="US Non-Farm Payrolls",
        currency="USD",
        impact="high",
        timestamp=now_ts,
        date="2026-09-10T12:30:00Z",
    )
    assert ev.currency == "USD"
    assert ev["currency"] == "USD"
    assert ev.get("currency") == "USD"
    assert abs(ev.time.timestamp() - now_ts) < 1e-4