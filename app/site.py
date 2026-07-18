"""Public-facing pages: the sign-in screen and the logged-in calendar/
upcoming-events view. Same plain-HTML-f-string approach as
app/dashboard.py - no template engine, no JS framework."""

from html import escape

from app.models import User
from app.timeutil import IST, parse_ist

_STYLE = """
  :root { --accent: #4f46e5; }
  body { font-family: -apple-system, sans-serif; margin: 0; color: #1a1a1a; background: #f7f7fb; }
  .nav { display: flex; align-items: center; justify-content: space-between; padding: 1rem 2rem; background: #fff; border-bottom: 1px solid #e5e5ef; }
  .nav a { color: var(--accent); text-decoration: none; font-weight: 600; margin-left: 1rem; }
  .wrap { max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
  .card { background: #fff; border-radius: 12px; padding: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 1.5rem; }
  .chip { display: inline-block; padding: 0.2rem 0.6rem; border-radius: 999px; background: var(--accent); color: #fff; font-size: 0.75rem; margin-right: 0.5rem; }
  .login-wrap { display: flex; align-items: center; justify-content: center; height: 100vh; }
  .login-card { text-align: center; padding: 3rem; }
  .btn { display: inline-block; background: var(--accent); color: #fff; padding: 0.75rem 1.5rem; border-radius: 8px; text-decoration: none; font-weight: 600; }
  input[type=text] { padding: 0.5rem; border: 1px solid #ddd; border-radius: 6px; width: 220px; }
  .upcoming-row { padding: 0.75rem 0; border-bottom: 1px solid #eee; }
"""


def render_login_page() -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>internblog</title><style>{_STYLE}</style></head>
<body>
<div class="login-wrap"><div class="login-card">
  <h1>internblog</h1>
  <p>Sign in to sync internship deadlines to your Google Calendar, and
  optionally get Telegram alerts.</p>
  <a class="btn" href="/auth/start">Sign in with Google</a>
</div></div>
</body></html>"""


def _safe_link(link: str) -> str:
    """Renders a clickable anchor only for http(s) links. Untrusted schemes
    like javascript: or data: are rendered as inert escaped text instead,
    since html.escape() alone prevents attribute breakout but not a
    dangerous scheme executing on click."""
    if link.startswith("http://") or link.startswith("https://"):
        return f'<a href="{escape(link)}">post</a>'
    return escape(link)


def _upcoming_row(event: dict) -> str:
    dt = parse_ist(event["deadline"]) if event.get("deadline") else None
    when = dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST") if dt else "-"
    role = f" &middot; {escape(event['role'])}" if event.get("role") else ""
    return (
        f'<div class="upcoming-row">'
        f'<span class="chip">{escape(event["category"])}</span>'
        f'<strong>{escape(event["company"] or "Unknown company")}</strong>{role}'
        f'<div>{escape(when)} &middot; {_safe_link(event["link"])}</div>'
        f'</div>'
    )


def render_calendar_view(user: User, upcoming: list[dict], is_admin: bool) -> str:
    admin_link = '<a href="/admin">Admin</a>' if is_admin else ""
    rows = "\n".join(_upcoming_row(e) for e in upcoming) or "<p>Nothing upcoming.</p>"
    sync_status = "syncing to your Google Calendar" if user.calendar_sync_enabled else "sync paused"
    telegram_value = escape(user.telegram_chat_id or "")

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>internblog</title><style>{_STYLE}</style></head>
<body>
<div class="nav">
  <strong>internblog</strong>
  <div>{escape(user.email)} {admin_link} <a href="/logout">Sign out</a></div>
</div>
<div class="wrap">
  <div class="card">
    <h2>Upcoming</h2>
    {rows}
  </div>
  <div class="card">
    <h2>Settings</h2>
    <p>Your events are {escape(sync_status)} ({escape(user.calendar_id or "not created yet")}).</p>
    <form method="post" action="/settings">
      <label>Telegram chat ID (optional): <input type="text" name="telegram_chat_id" value="{telegram_value}"></label>
      <label><input type="checkbox" name="calendar_sync_enabled" {"checked" if user.calendar_sync_enabled else ""}> Sync to my Google Calendar</label>
      <button type="submit">Save</button>
    </form>
  </div>
</div>
</body></html>"""
