"""Minimal server-rendered status page at the domain root, so checking
whether the monitor is actually up to date doesn't require hitting /health
and reading raw JSON."""

from html import escape

from app.timeutil import IST, parse_gmt, parse_ist


def _badge(ok: bool) -> str:
    color = "#1a7f37" if ok else "#cf222e"
    label = "OK" if ok else "DOWN"
    return f'<span style="color:{color};font-weight:600">{label}</span>'


def _fmt_ts(value: str | None) -> str:
    """For non-blog timestamps (last fetch, extracted-at) which are already
    stored as UTC-aware datetimes, not the blog's IST convention."""
    if not value:
        return "-"
    dt = parse_ist(value)
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z") if dt else value


def _fmt_ist(value: str | None) -> str:
    """Blog dates and deadlines, always shown in IST since that's the
    timezone every date on the blog is actually stated in."""
    if not value:
        return "-"
    dt = parse_ist(value)
    if dt is None:
        return value
    return dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def _fmt_posted(value: str | None) -> str:
    """Post date_gmt/modified_gmt: naive-but-actually-UTC, shown in IST for
    readability since that's the timezone the user actually reads dates in."""
    if not value:
        return "-"
    dt = parse_gmt(value)
    if dt is None:
        return value
    return dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def _extraction_row(r: dict, show_posted: bool = False) -> str:
    extra = f"<td>{escape(_fmt_posted(r['posted_at']))}</td>" if show_posted else ""
    return (
        f"<tr>"
        f"<td>{escape(r['category'])}</td>"
        f"<td>{escape(r['company'] or '-')}</td>"
        f"<td>{escape(r['role'] or '-')}</td>"
        f"<td>{escape(_fmt_ist(r['deadline']))}</td>"
        f'<td><a href="{escape(r["link"])}">post</a></td>'
        f"{extra}"
        f"</tr>"
    )


def render_dashboard(status: dict, upcoming: list[dict], recent: list[dict], total_extractions: int) -> str:
    session = status.get("session") or {}
    last_fetch = status.get("last_fetch") or {}
    counts = status.get("counts") or {}

    upcoming_rows = "\n".join(_extraction_row(r) for r in upcoming) or (
        '<tr><td colspan="5" style="text-align:center;color:#666">Nothing upcoming</td></tr>'
    )
    recent_rows = "\n".join(_extraction_row(r, show_posted=True) for r in recent) or (
        '<tr><td colspan="6" style="text-align:center;color:#666">No extractions yet</td></tr>'
    )

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<title>internblog monitor</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 1000px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ font-size: 1.1rem; margin-top: 2.5rem; }}
  h2 .count {{ color: #999; font-weight: 400; font-size: 0.85rem; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
  th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #e0e0e0; font-size: 0.9rem; }}
  th {{ color: #666; font-weight: 500; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.75rem; margin: 1rem 0; }}
  .card {{ border: 1px solid #e0e0e0; border-radius: 8px; padding: 0.75rem 1rem; }}
  .card .label {{ color: #666; font-size: 0.8rem; }}
  .card .value {{ font-size: 1.1rem; font-weight: 600; }}
  .scroll {{ max-height: 480px; overflow-y: auto; border: 1px solid #e0e0e0; border-radius: 8px; }}
  .scroll table {{ margin-top: 0; }}
  .scroll thead th {{ position: sticky; top: 0; background: #fff; }}
</style>
</head>
<body>
<h1>internblog monitor {_badge(status.get("healthy", False))}</h1>
<div class="grid">
  <div class="card"><div class="label">Session</div><div class="value">{_badge(session.get("session_alive", False))}</div></div>
  <div class="card"><div class="label">Scheduler</div><div class="value">{_badge(status.get("scheduler_running", False))}</div></div>
  <div class="card"><div class="label">Posts</div><div class="value">{counts.get("posts", "-")}</div></div>
  <div class="card"><div class="label">Extractions</div><div class="value">{counts.get("extractions", "-")}</div></div>
  <div class="card"><div class="label">Poll interval</div><div class="value">{status.get("poll_interval_minutes", "-")}m</div></div>
</div>
<div class="grid">
  <div class="card"><div class="label">Last fetch</div><div class="value">{escape(_fmt_ts(last_fetch.get("ts")))}</div></div>
  <div class="card"><div class="label">Fetch status</div><div class="value">{escape(str(last_fetch.get("status", "-")))}</div></div>
  <div class="card"><div class="label">New / modified / removed</div>
    <div class="value">{last_fetch.get("new", 0)} / {last_fetch.get("modified", 0)} / {last_fetch.get("removed", 0)}</div></div>
</div>
{f'<p style="color:#cf222e">Last fetch error: {escape(str(last_fetch.get("error")))}</p>' if last_fetch.get("error") else ""}
{f'<p style="color:#cf222e">Session error: {escape(str(session.get("last_error")))}</p>' if session.get("last_error") else ""}

<h2>Upcoming calendar events <span class="count">({len(upcoming)}, same as your Google Calendar)</span></h2>
<table>
<thead><tr><th>Category</th><th>Company</th><th>Role</th><th>Date</th><th>Link</th></tr></thead>
<tbody>
{upcoming_rows}
</tbody>
</table>

<h2>Database <span class="count">({total_extractions} extractions total, newest post first)</span></h2>
<div class="scroll">
<table>
<thead><tr><th>Category</th><th>Company</th><th>Role</th><th>Deadline</th><th>Link</th><th>Posted</th></tr></thead>
<tbody>
{recent_rows}
</tbody>
</table>
</div>

<p style="color:#999;font-size:0.8rem;margin-top:2rem">Auto-refreshes every 60s. Calendar feed (token not shown here): /calendar/&lt;token&gt;.ics · Health JSON: /health</p>
</body>
</html>"""
