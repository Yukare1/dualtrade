from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo
import pandas as pd
import exchange_calendars as xcals

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


@lru_cache(maxsize=1)
def exchange():
    return xcals.get_calendar("XNYS", start="2020-01-01", end="2035-12-31")


def session(day: date):
    c = exchange(); d = pd.Timestamp(day)
    if not c.is_session(d):
        return None
    return int(c.session_open(d).timestamp()), int(c.session_close(d).timestamp())


def previous_session(day: date):
    d = day - timedelta(days=1)
    while session(d) is None:
        d -= timedelta(days=1)
    return d


def next_session(day: date):
    d = day + timedelta(days=1)
    while session(d) is None:
        d += timedelta(days=1)
    return d


def earnings_window(day: date, outside_hours=True):
    prior = session(previous_session(day))
    last = next_session(day) if outside_hours else (day if session(day) else next_session(day))
    return {"close_by": prior[1] - 300, "blocked_until": session(last)[1], "date": day.isoformat()}


def now_ny(ts):
    return datetime.fromtimestamp(ts, UTC).astimezone(NY)


def macro_gate(events, now, style):
    blocked = any(e["time"] - 1800 <= now <= e.get("end", e["time"]) + 1800 for e in events)
    flatten = style == "day" and any(e["time"] - 300 <= now <= e.get("end", e["time"]) + 1800 for e in events)
    return blocked, flatten
