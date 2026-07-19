"""Week-grid calendar view at /calendar (distinct from the ICS feed at
/calendar/{token}.ics): a simplified Google-Calendar-style week layout -
seven day columns, each listing that day's events with a time label,
without proportional hour-by-hour positioning. Same plain-HTML-f-string
approach as app/site.py / app/dashboard.py - no template engine, no JS
framework."""

from datetime import date, datetime, timedelta
from html import escape

from app.timeutil import IST

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

_STYLE = """
  :root { --accent: #4f46e5; }
  body { font-family: -apple-system, sans-serif; margin: 0; color: #1a1a1a; background: #f7f7fb; }
  .nav { display: flex; align-items: center; justify-content: space-between; padding: 1rem 2rem; background: #fff; border-bottom: 1px solid #e5e5ef; }
  .nav a { color: var(--accent); text-decoration: none; font-weight: 600; margin-left: 1rem; }
  .wrap { max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }
  .chip { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px; background: var(--accent); color: #fff; font-size: 0.7rem; }
  .cal-toolbar { display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; }
  .cal-toolbar a { color: var(--accent); text-decoration: none; font-weight: 600; }
  .cal-range { font-weight: 600; margin-right: auto; }
  .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 0.75rem; }
  .cal-day { background: #fff; border-radius: 8px; padding: 0.75rem; min-height: 120px; border: 1px solid #e5e5ef; }
  .cal-day-header { font-size: 0.85rem; color: #666; margin-bottom: 0.5rem; }
  .cal-event { padding: 0.4rem; border-radius: 6px; background: #f7f7fb; margin-bottom: 0.4rem; font-size: 0.8rem; }
  .cal-time { color: #666; font-size: 0.75rem; }
  .cal-company { font-weight: 600; }
  .cal-role { color: #666; }
"""


def week_start(for_date: date) -> date:
    """Monday of the week containing for_date."""
    return for_date - timedelta(days=for_date.weekday())


def parse_week_param(value: str | None) -> date:
    """Parses the ?week=YYYY-MM-DD query param into that week's Monday,
    defaulting to the current week (in IST) if missing or unparseable."""
    if value:
        try:
            return week_start(date.fromisoformat(value))
        except ValueError:
            pass
    return week_start(datetime.now(IST).date())


def _fmt_time(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%I:%M %p").lstrip("0")


def _event_chip(e: dict) -> str:
    role_line = f'<div class="cal-role">{escape(e["role"])}</div>' if e["role"] else ""
    return (
        f'<div class="cal-event" style="cursor:pointer" onclick="location.href=\'/posts/{e["post_id"]}\'">'
        f'<div class="cal-time">{escape(_fmt_time(e["deadline_dt"]))}</div>'
        f'<span class="chip">{escape(e["category"])}</span>'
        f'<div class="cal-company">{escape(e["company"] or "Unknown company")}</div>'
        f"{role_line}"
        f"</div>"
    )


def _day_column(day: date, events: list[dict]) -> str:
    rows = "\n".join(_event_chip(e) for e in events) or (
        '<p style="color:#999;font-size:0.8rem">No events</p>'
    )
    is_today = day == datetime.now(IST).date()
    header_style = ' style="font-weight:700;color:var(--accent)"' if is_today else ""
    return f"""
    <div class="cal-day">
      <div class="cal-day-header"{header_style}>{DAY_NAMES[day.weekday()]} {day.day}</div>
      {rows}
    </div>"""


def render_week_view(monday: date, events: list[dict]) -> str:
    days = [monday + timedelta(days=i) for i in range(7)]
    by_day: dict[date, list[dict]] = {d: [] for d in days}
    for e in events:
        d = e["deadline_dt"].astimezone(IST).date()
        if d in by_day:
            by_day[d].append(e)
    for d in days:
        by_day[d].sort(key=lambda e: e["deadline_dt"])

    columns = "\n".join(_day_column(d, by_day[d]) for d in days)
    prev_week = (monday - timedelta(days=7)).isoformat()
    next_week = (monday + timedelta(days=7)).isoformat()
    this_week = week_start(datetime.now(IST).date()).isoformat()
    range_label = f"{monday.strftime('%d %b')} - {(monday + timedelta(days=6)).strftime('%d %b %Y')}"

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Calendar - internblog</title><style>{_STYLE}</style></head>
<body>
<div class="nav">
  <strong>internblog</strong>
  <div><a href="/">Home</a></div>
</div>
<div class="wrap">
  <div class="cal-toolbar">
    <a href="/calendar?week={prev_week}">&larr; Prev</a>
    <a href="/calendar?week={this_week}">Today</a>
    <a href="/calendar?week={next_week}">Next &rarr;</a>
    <span class="cal-range">{escape(range_label)}</span>
  </div>
  <div class="cal-grid">
    {columns}
  </div>
</div>
</body></html>"""
