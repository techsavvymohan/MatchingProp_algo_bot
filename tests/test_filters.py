import sys
from pathlib import Path
from datetime import datetime, time, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from xauusd_bot.models import Session
from xauusd_bot.filters.session_filter import SessionFilter
from xauusd_bot.filters.spread_filter import SpreadFilter
from xauusd_bot.filters.news_filter import NewsFilter
from xauusd_bot.data.spread import SpreadTracker
from xauusd_bot.data.economic_calendar import EconomicCalendar
from xauusd_bot.utils.time_utils import current_session


# ── SessionFilter ──

def test_session_london():
    sf = SessionFilter("08:00", "17:00", "13:00", "22:00")
    london_time = datetime(2025, 1, 1, 10, 0)
    ok, name = sf.check(london_time)
    assert ok
    assert "london" in name


def test_session_ny():
    sf = SessionFilter("08:00", "17:00", "13:00", "22:00")
    ny_time = datetime(2025, 1, 1, 14, 0)
    ok, name = sf.check(ny_time)
    assert ok


def test_session_overlap():
    sf = SessionFilter("08:00", "17:00", "13:00", "22:00")
    overlap = datetime(2025, 1, 1, 14, 0)
    sess = current_session(overlap)
    assert sess == Session.LONDON_NY_OVERLAP


def test_session_asian():
    sf = SessionFilter("08:00", "17:00", "13:00", "22:00")
    asian = datetime(2025, 1, 1, 3, 0)
    sess = current_session(asian)
    assert sess == Session.ASIAN


def test_entry_tfs_scalping_session():
    sf = SessionFilter()
    tfs = sf.allowed_entry_tfs(datetime(2025, 1, 1, 14, 0))
    assert "M1" in tfs
    assert "M5" in tfs


def test_entry_tfs_asian():
    sf = SessionFilter()
    tfs = sf.allowed_entry_tfs(datetime(2025, 1, 1, 3, 0))
    assert "M1" not in tfs
    assert "M15" in tfs


# ── SpreadFilter ──

def test_spread_ok():
    st = SpreadTracker(10)
    for s in [15, 16, 14, 15, 16]:
        st.update(s)
    sf = SpreadFilter(st, 2.0)
    st.update(15)
    ok, _ = sf.check()
    assert ok


def test_spread_blocked():
    st = SpreadTracker(10)
    for s in [15, 16, 14, 15, 16]:
        st.update(s)
    sf = SpreadFilter(st, 1.5)
    st.update(50)
    ok, _ = sf.check()
    assert not ok


def test_spread_no_data():
    st = SpreadTracker(10)
    sf = SpreadFilter(st, 1.5)
    ok, _ = sf.check()
    assert ok


# ── NewsFilter ──

def test_news_not_blocked():
    cal = EconomicCalendar()
    cal._events = []
    nf = NewsFilter(cal, 30, 30)
    ok, _ = nf.check()
    assert ok


def test_news_blocked():
    now = datetime.now(timezone.utc).timestamp()
    cal = EconomicCalendar()
    cal._events = [{
        "title": "NFP",
        "currency": "USD",
        "impact": "high",
        "timestamp": now + 600,
    }]
    nf = NewsFilter(cal, 30, 30)
    ok, reason = nf.check()
    assert not ok
    assert reason is not None


def test_news_non_usd_ignored():
    now = datetime.now(timezone.utc).timestamp()
    cal = EconomicCalendar()
    cal._events = [{
        "title": "EUR CPI",
        "currency": "EUR",
        "impact": "high",
        "timestamp": now + 600,
    }]
    nf = NewsFilter(cal, 30, 30)
    ok, _ = nf.check()
    assert ok


def test_upcoming_events():
    cal = EconomicCalendar()
    cal.fetch()
    events = cal.upcoming_events
    assert len(events) > 0
