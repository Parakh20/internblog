"""ICS calendar feed of application deadlines, for subscription in Google
Calendar the same way Codeforces publishes a contest feed: Google polls this
URL on its own schedule and adds/updates events accordingly.
"""

import re
from dataclasses import dataclass
from datetime import timezone

from app.timeutil import parse_ist


@dataclass(frozen=True)
class DeadlineEvent:
    uid: str
    company: str
    role: str | None
    deadline: str
    deadline_end: str | None
    link: str


def _escape_ics_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _format_ics_datetime(iso_value: str) -> str | None:
    """Parse an ISO 8601 datetime into the UTC `YYYYMMDDTHHMMSSZ` form ICS
    requires. Returns None if unparseable."""
    dt = parse_ist(iso_value)
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(events: list[DeadlineEvent]) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//internblog//deadline feed//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:IITB Internship Deadlines",
    ]
    for event in events:
        dtstart = _format_ics_datetime(event.deadline)
        if dtstart is None:
            continue
        # Explicit DTEND (equal to DTSTART when the post gave no end time) so
        # clients render the exact moment instead of guessing a default
        # duration block for a plain point-in-time deadline.
        dtend = _format_ics_datetime(event.deadline_end) if event.deadline_end else dtstart
        summary = _escape_ics_text(f"{event.company} deadline" + (f" - {event.role}" if event.role else ""))
        description = _escape_ics_text(event.link)
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{re.sub(r'[^A-Za-z0-9]', '', event.uid)}@internblog",
                f"DTSTAMP:{dtstart}",
                f"DTSTART:{dtstart}",
                f"DTEND:{dtend}",
                f"SUMMARY:{summary}",
                f"DESCRIPTION:{description}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
