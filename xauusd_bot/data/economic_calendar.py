import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

log = logging.getLogger("xauusd_bot.data.calendar")

HIGH_IMPACT_CURRENCIES = {"USD"}
XAU_KEYWORDS = ("gold", "xau", "precious metals")


class EconomicCalendar:
    def __init__(self, api_url: str = "", api_key: str = ""):
        self.api_url = api_url
        self.api_key = api_key
        self._events: List[dict] = []

    def fetch(self, days_ahead: int = 1) -> bool:
        if not self.api_url:
            log.warning("No economic calendar API configured — using fallback list")
            self._events = self._fallback_events()
            return True
        try:
            import requests
            resp = requests.get(
                self.api_url,
                params={"apikey": self.api_key, "from": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d"),
                        "to": (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days_ahead)).strftime("%Y-%m-%d")},
                timeout=15,
            )
            if resp.status_code == 200:
                self._events = resp.json()
                return True
            log.error("Calendar API returned %d", resp.status_code)
        except Exception as exc:
            log.error("Calendar fetch failed: %s", exc)
        self._events = self._fallback_events()
        return True

    def _fallback_events(self) -> List[dict]:
        known_high = [
            ("Non-Farm Payrolls", "USD", "NFP"),
            ("CPI", "USD", "CPI"),
            ("FOMC", "USD", "FOMC"),
            ("Fed Interest Rate Decision", "USD", "FOMC"),
            ("GDP", "USD", "GDP"),
            ("Retail Sales", "USD", "Retail Sales"),
            ("ISM Manufacturing", "USD", "ISM"),
            ("ISM Services", "USD", "ISM"),
            ("Unemployment Rate", "USD", "Unemployment"),
            ("Initial Jobless Claims", "USD", "Jobless Claims"),
            ("PPI", "USD", "PPI"),
            ("Consumer Confidence", "USD", "Consumer Confidence"),
        ]
        events = []
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        import random
        for name, currency, tag in known_high:
            for offset_hours in [0, 24, 48]:
                event_time = now.replace(hour=13, minute=30, second=0, microsecond=0) + timedelta(hours=offset_hours)
                events.append({
                    "title": name,
                    "currency": currency,
                    "impact": "high",
                    "date": event_time.strftime("%Y-%m-%d %H:%M"),
                    "timestamp": event_time.timestamp(),
                })
        now_ts = now.timestamp()
        return [e for e in events if e["timestamp"] > now_ts]

    def is_blocked(self, before_minutes: int = 30, after_minutes: int = 30, symbol: str = "XAUUSD") -> Tuple[bool, Optional[str]]:
        now = datetime.now(timezone.utc).timestamp()
        target_currencies = {"USD"}
        if "EUR" in symbol.upper():
            target_currencies.add("EUR")

        for event in self._events:
            event_ts = event.get("timestamp", 0)
            if not event_ts:
                continue
            currency = event.get("currency", "")
            if currency not in target_currencies:
                continue
            diff_minutes = (event_ts - now) / 60
            start_block = event_ts - before_minutes * 60
            end_block = event_ts + after_minutes * 60
            if start_block <= now <= end_block:
                title = event.get("title", "Unknown")
                return True, f"News block active: '{title}' ({currency}) — {abs(diff_minutes):.0f} min {'before' if diff_minutes > 0 else 'after'}"
        return False, None

    @property
    def upcoming_events(self) -> List[dict]:
        now = datetime.now(timezone.utc).timestamp()
        return sorted(
            [e for e in self._events if e.get("timestamp", 0) > now],
            key=lambda x: x.get("timestamp", 0),
        )
