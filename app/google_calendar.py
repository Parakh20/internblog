"""Push extraction events directly into a personal Google Calendar via the
Calendar API.

The ICS feed (app/calendar_feed.py) only reaches desktop Google Calendar -
the official mobile app does not surface URL-subscribed "Other calendars"
the way the desktop web app does. Pushing events directly into the primary
calendar via the API shows them on mobile immediately, same as any other
event, and needs no subscription step at all.

Auth is a one-time OAuth refresh token (see scripts/google_calendar_auth.py),
exchanged here for short-lived access tokens on each call.
"""

import hashlib
import logging

import httpx

from app.timeutil import parse_ist

logger = logging.getLogger(__name__)

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
EVENTS_ENDPOINT = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
CALENDARS_ENDPOINT = "https://www.googleapis.com/calendar/v3/calendars"
REQUEST_TIMEOUT = 15.0


def get_access_token(client_id: str, client_secret: str, refresh_token: str) -> str | None:
    """Exchange the long-lived refresh token for a short-lived access token."""
    try:
        response = httpx.post(
            TOKEN_ENDPOINT,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()["access_token"]
    except httpx.HTTPError:
        logger.exception("failed to refresh google calendar access token")
        return None


def create_secondary_calendar(access_token: str, summary: str = "Internblog Deadlines") -> str | None:
    """Create a dedicated secondary calendar for a newly signed-in user, so
    their internship deadlines don't mix into their primary calendar
    (lectures, personal events, etc). Called once, on first login."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = httpx.post(
            CALENDARS_ENDPOINT, headers=headers, json={"summary": summary}, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json()["id"]
    except httpx.HTTPError:
        logger.exception("failed to create secondary calendar %r", summary)
        return None


def _to_rfc3339(iso_value: str) -> str | None:
    """Parse an ISO 8601 datetime into an RFC 3339 string with an explicit
    UTC offset, which the Calendar API accepts unambiguously."""
    dt = parse_ist(iso_value)
    return dt.isoformat() if dt is not None else None


def event_id_for(extraction_id: int) -> str:
    """Calendar API event IDs must be lowercase base32hex (a-v, 0-9) and at
    least 5 characters long. The "evt" prefix uses only letters within that
    range, and zero-padding to 3 digits keeps every id >= 5 chars even for
    small extraction ids like 5 (which "evt5" alone would violate)."""
    return f"evt{extraction_id:03d}"


def event_id_for_company(company: str, group: str) -> str:
    """Stable id derived from (company, event group) rather than a specific
    extraction row, so a follow-up post about the same company and the same
    kind of event (e.g. a deadline_extension after a new_listing) updates
    that one calendar event in place instead of creating a duplicate. A
    hex digest only ever uses 0-9a-f, which is already valid base32hex, so
    no character filtering is needed."""
    key = f"{company.strip().lower()}|{group}"
    return "evt" + hashlib.sha1(key.encode()).hexdigest()[:20]


def upsert_event(
    access_token: str,
    calendar_id: str,
    event_id: str,
    summary: str,
    description: str,
    start_iso: str,
    end_iso: str | None,
) -> str:
    """Create or update a calendar event, keyed by a stable client-chosen ID
    so re-running extraction on the same post is idempotent rather than
    creating duplicate events.

    Returns "ok" on success, "calendar_missing" if calendar_id itself
    doesn't exist (deleted after creation, or a stale/invalid id) - the
    caller should clear its stored calendar id and retry against a freshly
    created one, rather than silently failing every push forever - or
    "error" for any other failure."""
    start_rfc3339 = _to_rfc3339(start_iso)
    if start_rfc3339 is None:
        logger.warning("unparseable start time %r for event %s, skipping calendar push", start_iso, event_id)
        return "error"
    end_rfc3339 = _to_rfc3339(end_iso) if end_iso else start_rfc3339

    body = {
        "id": event_id,
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_rfc3339},
        "end": {"dateTime": end_rfc3339},
    }
    headers = {"Authorization": f"Bearer {access_token}"}
    base_url = EVENTS_ENDPOINT.format(calendar_id=calendar_id)
    try:
        response = httpx.post(base_url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
        if response.status_code == 409:
            response = httpx.put(f"{base_url}/{event_id}", headers=headers, json=body, timeout=REQUEST_TIMEOUT)
        if response.status_code == 404:
            logger.warning("calendar %s not found (deleted or invalid), signaling for recreation", calendar_id)
            return "calendar_missing"
        response.raise_for_status()
        return "ok"
    except httpx.HTTPError:
        logger.exception("failed to upsert google calendar event %s", event_id)
        return "error"
