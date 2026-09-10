import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

log = logging.getLogger("xauusd_bot.data.calendar")

HIGH_IMPACT_CURRENCIES = {"USD"}
XAU_KEYWORDS = ("gold", "xau", "precious metals")
FOREX_FACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


@dataclass
class CalendarEvent:
    title: str
    currency: str
    impact: str
    timestamp: float
    date: str

    @property
    def time(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


class EconomicCalendar:
    def __init__(self, api_url: str = "", api_key: str = "", cache_ttl_seconds: int = 1800):
        self.api_url = api_url
        self.api_key = api_key
        self.cache_ttl_seconds = cache_ttl_seconds
        self._last_fetch_ts: float = 0.0
        self._events: List[Union[CalendarEvent, dict]] = []

    def fetch(self, days_ahead: int = 1, force: bool = False) -> bool:
        """Fetch genuine economic calendar events from authentic sources only.
        
        Zero artificial or synthetic fallback events are generated. If no feeds
        are reachable, events list remains empty (or retains valid prior events).
        """
        now_ts = datetime.now(timezone.utc).timestamp()
        if not force and self._events and (now_ts - self._last_fetch_ts < self.cache_ttl_seconds):
            return True

        # 1. Try Custom API URL if configured
        if self.api_url:
            try:
                import requests
                resp = requests.get(
                    self.api_url,
                    params={
                        "apikey": self.api_key,
                        "from": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d"),
                        "to": (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days_ahead)).strftime("%Y-%m-%d"),
                    },
                    timeout=15,
                )
                if resp.status_code == 200:
                    raw_events = resp.json()
                    parsed = []
                    for row in raw_events:
                        ts = row.get("timestamp")
                        if not ts and row.get("date"):
                            try:
                                ts = datetime.fromisoformat(row["date"]).timestamp()
                            except Exception:
                                pass
                        parsed.append(CalendarEvent(
                            title=row.get("title") or row.get("event", "Economic Event"),
                            currency=row.get("currency") or row.get("region_code", "USD"),
                            impact=str(row.get("impact", "high")).lower(),
                            timestamp=float(ts or 0),
                            date=str(row.get("date", "")),
                        ))
                    self._events = parsed
                    self._last_fetch_ts = now_ts
                    log.info("Loaded %d calendar events from custom API endpoint", len(parsed))
                    return True
                log.error("Calendar API returned status code %d", resp.status_code)
            except Exception as exc:
                log.error("Custom calendar API fetch failed: %s", exc)

        # 2. Try London Strategic Edge if API key is provided
        lse_key = self.api_key or os.getenv("LSE_API_KEY", "")
        if lse_key:
            try:
                import requests
                now_utc = datetime.now(timezone.utc)
                start_date = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
                end_date = (now_utc + timedelta(days=days_ahead + 1)).strftime("%Y-%m-%d")
                lse_url = "https://api.londonstrategicedge.com/vault/ref/economic_calendar"
                headers = {"x-api-key": lse_key}
                resp = requests.get(
                    lse_url,
                    params={"region": "US", "start": start_date, "end": end_date, "limit": 100},
                    headers=headers,
                    timeout=12,
                )
                if resp.status_code == 200:
                    raw_events = resp.json()
                    events = []
                    for row in raw_events:
                        dt_str = row.get("datetime") or f"{row.get('date')} 12:00:00"
                        try:
                            dt = datetime.fromisoformat(dt_str)
                        except Exception:
                            continue
                        events.append(CalendarEvent(
                            title=row.get("event", "Economic Event"),
                            currency=row.get("region_code", "USD"),
                            impact="high",
                            date=dt_str,
                            timestamp=dt.timestamp(),
                        ))
                    if events:
                        self._events = events
                        self._last_fetch_ts = now_ts
                        log.info("Loaded %d genuine calendar events from London Strategic Edge", len(events))
                        return True
            except Exception as exc:
                log.warning("LSE calendar query failed: %s", exc)

        # 3. Try ForexFactory Live Public Feed (Authentic real-time releases)
        try:
            import json
            import urllib.request
            req = urllib.request.Request(
                FOREX_FACTORY_URL,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    raw_data = json.loads(resp.read().decode("utf-8"))
                    events = []
                    for row in raw_data:
                        date_str = row.get("date", "")
                        if not date_str:
                            continue
                        try:
                            dt = datetime.fromisoformat(date_str)
                            ts = dt.timestamp()
                        except Exception:
                            continue

                        events.append(CalendarEvent(
                            title=row.get("title", "Economic Event"),
                            currency=row.get("country", "USD"),
                            impact=str(row.get("impact", "low")).lower(),
                            timestamp=ts,
                            date=date_str,
                        ))
                    if events:
                        self._events = events
                        self._last_fetch_ts = now_ts
                        log.info("Loaded %d authentic live events from ForexFactory", len(events))
                        return True
        except Exception as exc:
            log.warning("ForexFactory live calendar query failed (%s)", exc)

        # 4. If prior genuine events exist, retain them; otherwise leave empty
        if self._events:
            log.warning("Calendar query unreachable; retaining %d existing events", len(self._events))
            return True

        self._events = []
        log.info("No economic calendar feed available — running with 0 news blocks (strictly original data).")
        return False

    def is_blocked(self, before_minutes: int = 30, after_minutes: int = 30, symbol: str = "XAUUSD") -> Tuple[bool, Optional[str]]:
        now = datetime.now(timezone.utc).timestamp()
        target_currencies = {"USD"}
        if "EUR" in symbol.upper():
            target_currencies.add("EUR")

        for event in self._events:
            event_ts = event.get("timestamp", 0) if isinstance(event, dict) else getattr(event, "timestamp", 0)
            if not event_ts:
                continue
            currency = event.get("currency", "") if isinstance(event, dict) else getattr(event, "currency", "")
            if currency not in target_currencies:
                continue
            impact = str(event.get("impact", "") if isinstance(event, dict) else getattr(event, "impact", "")).lower()
            if impact not in ("high", "red"):
                continue
            diff_minutes = (event_ts - now) / 60
            start_block = event_ts - before_minutes * 60
            end_block = event_ts + after_minutes * 60
            if start_block <= now <= end_block:
                title = event.get("title", "Unknown") if isinstance(event, dict) else getattr(event, "title", "Unknown")
                return True, f"News block active: '{title}' ({currency}) — {abs(diff_minutes):.0f} min {'before' if diff_minutes > 0 else 'after'}"
        return False, None

    @property
    def events(self) -> List[Union[CalendarEvent, dict]]:
        return list(self._events)

    @property
    def upcoming_events(self) -> List[Union[CalendarEvent, dict]]:
        now = datetime.now(timezone.utc).timestamp()
        filtered = [
            e for e in self._events
            if (e.get("timestamp", 0) if isinstance(e, dict) else getattr(e, "timestamp", 0)) > now
        ]
        return sorted(
            filtered,
            key=lambda x: x.get("timestamp", 0) if isinstance(x, dict) else getattr(x, "timestamp", 0),
        )
