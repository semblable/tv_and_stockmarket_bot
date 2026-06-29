# utils/timezone_utils.py
"""
Shared timezone / time-parsing helpers.

These were previously copy-pasted across ``cogs/mood.py``, ``cogs/reminders.py``
and ``cogs/productivity.py``. This module is the single source of truth for the
versions that were byte-for-byte identical across those files.

Note: ``cogs/productivity.py`` and ``data_manager_impl/productivity.py`` keep
their own ``_tzinfo_from_name`` implementations on purpose — they have subtly
different semantics (e.g. treating ``"CET"`` as a fixed UTC+1 offset, or
defaulting to UTC instead of Europe/Warsaw) and must NOT be merged with the
mood/reminders version below.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

try:
    # Python 3.9+
    from zoneinfo import ZoneInfo  # type: ignore
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sqlite_utc_timestamp(dt: datetime) -> str:
    """Format a datetime as the UTC ``"YYYY-MM-DD HH:MM:SS"`` SQLite uses."""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_sqlite_utc_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """Parse a SQLite UTC timestamp string back into a tz-aware datetime."""
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        dt = datetime.strptime(ts.strip(), "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def tzinfo_from_name(tz_name: Optional[str]):
    """
    Resolve a timezone name to a tzinfo, preferring Europe/Warsaw for the
    CET/CEST aliases and for the empty/default case (the mood + reminders
    convention). Falls back to a fixed UTC+1 offset if zoneinfo data is missing,
    and to UTC for unknown names.
    """
    name = (tz_name or "").strip()
    if not name:
        # Default to CET/CEST
        if ZoneInfo is not None:
            try:
                return ZoneInfo("Europe/Warsaw")
            except Exception:
                return timezone(timedelta(hours=1), name="CET")
        return timezone(timedelta(hours=1), name="CET")
    if name.upper() in ("UTC", "ETC/UTC", "Z"):
        return timezone.utc
    if name.upper() in ("CET", "CEST"):
        if ZoneInfo is not None:
            try:
                return ZoneInfo("Europe/Warsaw")
            except Exception:
                return timezone(timedelta(hours=1), name="CET")
        return timezone(timedelta(hours=1), name="CET")
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            return timezone.utc
    return timezone.utc


def parse_hhmm(s: str) -> Optional[Tuple[int, int]]:
    """Parse ``"HH:MM"`` into an ``(hour, minute)`` tuple, or None if invalid."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (s or "").strip())
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm
