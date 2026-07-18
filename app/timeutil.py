"""Shared datetime convention: every date the blog states is IST, and the
extraction prompt tells the model to emit naive ISO 8601 in that case. Used
by notifications, the ICS feed, the Google Calendar push, and the dashboard.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def parse_ist(iso_value: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime, assuming IST when no offset is given.
    Returns None if unparseable or absent."""
    if not iso_value:
        return None
    try:
        dt = datetime.fromisoformat(iso_value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt


def parse_gmt(iso_value: str | None) -> datetime | None:
    """Parse a WordPress `date_gmt`/`modified_gmt` value, which is naive but
    actually UTC (not the blog-content IST convention parse_ist assumes).
    Returns None if unparseable or absent."""
    if not iso_value:
        return None
    try:
        dt = datetime.fromisoformat(iso_value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
