"""Время сообщений чата: Europe/Moscow (или CHAT_TIMEZONE) ↔ UTC в Supabase."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from config import settings


def chat_tz() -> ZoneInfo:
    name = (settings.chat_timezone or "Europe/Moscow").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Moscow")


def format_ts_local(dt: datetime | None) -> str:
    if dt is None:
        return "??:??"
    tz = chat_tz()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(tz).strftime("%d.%m.%Y %H:%M")


def period_local_date(period: str) -> date:
    tz = chat_tz()
    today = datetime.now(tz).date()
    if period == "yesterday":
        return today - timedelta(days=1)
    if period == "day_before":
        return today - timedelta(days=2)
    return today


def parse_time_range(text: str) -> tuple[int | None, int | None, int, int]:
    """
  с 15 до 18 / с 15-18 часов / между 15 и 18
  → (hour_from, hour_to, minute_from, minute_to)
    """
    import re

    blob = text.lower()
    m = re.search(
        r"(?i)"
        r"(?:"
        r"с\s+(?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\s*(?:до|по|-|—)\s*"
        r"(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\s*(?:часов?)?"
        r"|"
        r"между\s+(?P<h3>\d{1,2})\s*(?:и|-|—)\s*(?P<h4>\d{1,2})\s*(?:часами?)?"
        r")",
        blob,
    )
    if not m:
        return None, None, 0, 59

    h1 = m.group("h1") or m.group("h3")
    h2 = m.group("h2") or m.group("h4")
    m1 = m.group("m1") or "0"
    m2 = m.group("m2") or "59"
    if not h1 or not h2:
        return None, None, 0, 59

    hf, ht = int(h1), int(h2)
    if hf > 23 or ht > 23:
        return None, None, 0, 59
    if hf > ht:
        hf, ht = ht, hf
    return hf, ht, int(m1), int(m2)


def utc_bounds_for_query(
    period: str,
    *,
    hour_from: int | None = None,
    hour_to: int | None = None,
    minute_from: int = 0,
    minute_to: int = 59,
) -> tuple[datetime, datetime]:
    tz = chat_tz()
    now = datetime.now(tz)

    if period == "24h" and hour_from is None and hour_to is None:
        start = now - timedelta(hours=24)
        return start.astimezone(ZoneInfo("UTC")), now.astimezone(ZoneInfo("UTC"))

    day = period_local_date(period)
    h0 = hour_from if hour_from is not None else 0
    h1 = hour_to if hour_to is not None else 23
    if h0 > h1:
        h0, h1 = h1, h0

    start_local = datetime.combine(day, time(h0, minute_from), tzinfo=tz)
    if hour_to is not None or hour_from is not None:
        end_local = datetime.combine(day, time(h1, minute_to), tzinfo=tz)
    else:
        end_local = datetime.combine(day, time(23, 59, 59), tzinfo=tz)

    return (
        start_local.astimezone(ZoneInfo("UTC")),
        end_local.astimezone(ZoneInfo("UTC")),
    )
