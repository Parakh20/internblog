"""Public-facing pages: the sign-in screen and the logged-in calendar/
upcoming-events view. Same plain-HTML-f-string approach as
app/dashboard.py - no template engine, no JS framework."""

import re
import bleach
from html import escape

from app.dashboard import _extraction_row, _fmt_posted, _fmt_ts, _safe_link
from app.models import Post, User
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
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.75rem; }
  .stats .stat { border: 1px solid #e5e5ef; border-radius: 8px; padding: 0.75rem 1rem; }
  .stats .label { color: #666; font-size: 0.8rem; }
  .stats .value { font-size: 1.1rem; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
  th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #e0e0e0; font-size: 0.9rem; }
  th { color: #666; font-weight: 500; }
  .scroll { max-height: 480px; overflow-y: auto; border: 1px solid #e0e0e0; border-radius: 8px; }
  .scroll table { margin-top: 0; }
  .scroll thead th { position: sticky; top: 0; background: #fff; }
"""


def render_homepage() -> str:
    """Public landing page shown at / for anonymous visitors - explains
    what the app does before asking anyone to sign in. Google's OAuth
    branding review checks the exact "Homepage URL" configured on the
    consent screen (https://internblog.duckdns.org, i.e. this route) for a
    real explanation of the app's purpose, not just a sign-in prompt."""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>internblog</title><style>{_STYLE}</style></head>
<body>
<div class="wrap" style="max-width:640px">
  <div class="card" style="text-align:center;margin-top:3rem">
    <h1>internblog</h1>
    <p>internblog tracks internship postings from the IIT Bombay placement
    blog and keeps their application deadlines, test dates, and PPTs in
    sync with your own Google Calendar - so you never have to check the
    blog manually or miss a date.</p>
    <p>Sign in with Google to get started. We create one dedicated
    "Internblog Deadlines" calendar in your account and only ever write
    events there - we never see or touch any of your other calendars. You
    can also optionally connect Telegram for the same alerts.</p>
    <a class="btn" href="/auth/start">Sign in with Google</a>
    <p style="margin-top:2rem;font-size:0.8rem;color:#999">
      <a href="/privacy" style="color:#999">Privacy Policy</a> &middot;
      <a href="/terms" style="color:#999">Terms of Service</a>
    </p>
  </div>
</div>
</body></html>"""


def render_access_denied_page() -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Access restricted - internblog</title><style>{_STYLE}</style></head>
<body>
<div class="login-wrap"><div class="login-card">
  <h1>internblog</h1>
  <p>internblog is currently limited to a small group of testers while it's
  reviewed against placement office policy. Your Google account isn't on
  that list.</p>
  <p style="margin-top:2rem;font-size:0.8rem;color:#999">
    Think this is a mistake? Contact sharmaparakh05@gmail.com.
  </p>
</div></div>
</body></html>"""


def render_login_page() -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>internblog</title><style>{_STYLE}</style></head>
<body>
<div class="login-wrap"><div class="login-card">
  <h1>internblog</h1>
  <p>Sign in to sync internship deadlines to your Google Calendar, and
  optionally get Telegram alerts.</p>
  <a class="btn" href="/auth/start">Sign in with Google</a>
  <p style="margin-top:2rem;font-size:0.8rem;color:#999">
    <a href="/privacy" style="color:#999">Privacy Policy</a> &middot;
    <a href="/terms" style="color:#999">Terms of Service</a>
  </p>
</div></div>
</body></html>"""


def render_privacy_page() -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Privacy Policy - internblog</title><style>{_STYLE}</style></head>
<body>
<div class="wrap">
  <div class="card">
    <h1>Privacy Policy</h1>
    <p>internblog helps you track internship postings from the IIT Bombay
    placement blog by syncing deadlines to your own Google Calendar and,
    optionally, sending you Telegram notifications.</p>

    <h2>What we collect</h2>
    <ul>
      <li><strong>From Google Sign-In:</strong> your email address, name,
      and profile picture, used only to identify your account and show it
      back to you in the app.</li>
      <li><strong>Calendar access:</strong> with your permission, we create
      a single dedicated calendar named "Internblog Deadlines" in your
      Google account and write internship deadline events into it. We do
      not read, modify, or delete any other calendar you own. Your Google
      Calendar refresh token is encrypted before being stored.</li>
      <li><strong>Telegram (optional):</strong> if you connect Telegram, we
      store the chat id Telegram assigns to your conversation with our bot,
      so we can send you the same deadline notifications there.</li>
    </ul>

    <h2>What we don't do</h2>
    <p>We don't sell or share your data with third parties. We don't read
    your email, your other calendars, or your Telegram messages. The
    internship postings themselves are public information already posted
    on the IIT Bombay placement blog.</p>

    <h2>Data retention</h2>
    <p>Your account data is kept as long as you use the app. You can
    disconnect Telegram at any time from Settings, and can revoke Google
    Calendar access at any time from your
    <a href="https://myaccount.google.com/permissions">Google Account
    permissions page</a>.</p>

    <h2>Contact</h2>
    <p>Questions about this policy: {escape("sharmaparakh05@gmail.com")}</p>
  </div>
</div>
</body></html>"""


def render_terms_page() -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Terms of Service - internblog</title><style>{_STYLE}</style></head>
<body>
<div class="wrap">
  <div class="card">
    <h1>Terms of Service</h1>
    <p>internblog is a free, unofficial tool that mirrors publicly posted
    internship listings from the IIT Bombay placement blog and helps you
    track their deadlines via your own Google Calendar and, optionally,
    Telegram. It is not affiliated with or endorsed by IIT Bombay or the
    placement office.</p>

    <h2>No warranty</h2>
    <p>internblog is provided as-is, with no guarantee of accuracy,
    availability, or timeliness. Always verify deadlines and details
    against the original placement blog post before relying on them.</p>

    <h2>Your account</h2>
    <p>You may disconnect Google Calendar or Telegram, or stop using the
    app, at any time. We may remove your access if the service is
    discontinued or misused.</p>

    <h2>Contact</h2>
    <p>Questions: {escape("sharmaparakh05@gmail.com")}</p>
  </div>
</div>
</body></html>"""


_ALLOWED_POST_TAGS = ["p", "br", "b", "strong", "i", "em", "a", "ul", "ol", "li", "span", "div"]
_ALLOWED_POST_ATTRS = {"a": ["href"]}


def _sanitize_post_html(raw_html: str) -> str:
    """Strips scripts, event handler attributes, and any tag/attribute
    outside a conservative allowlist from scraped blog post content before
    it's rendered on our own page - raw_html is fetched content we don't
    fully control, not something we authored."""
    # bleach's strip=True drops the <script> tag itself but leaves its text
    # content behind as inert-looking text, which would still put the
    # script's source in the rendered page - strip the whole element first.
    cleaned = re.sub(r'<script[^>]*>.*?</script>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
    return bleach.clean(cleaned, tags=_ALLOWED_POST_TAGS, attributes=_ALLOWED_POST_ATTRS, strip=True)


def render_post_detail(post: Post) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{escape(post.title)} - internblog</title><style>{_STYLE}</style></head>
<body>
<div class="wrap">
  <div class="card">
    <p><a href="/">&larr; Back to database</a></p>
    <h1>{escape(post.title)}</h1>
    <p style="color:#666;font-size:0.85rem">Posted {escape(_fmt_posted(post.date_gmt))} &middot; {_safe_link(post.link)}</p>
    <div>{_sanitize_post_html(post.raw_html)}</div>
  </div>
</div>
</body></html>"""


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


def _telegram_section(user: User, connect_url: str | None) -> str:
    if user.telegram_chat_id:
        return (
            "<p>Telegram notifications are connected.</p>"
            '<form method="post" action="/settings/disconnect-telegram">'
            '<button type="submit">Disconnect Telegram</button>'
            "</form>"
        )
    if connect_url:
        return (
            "<p>Not connected. Tap the link below, then hit Start in Telegram - "
            "no chat ID to find or paste.</p>"
            f'<a class="btn" href="{escape(connect_url)}">Connect Telegram</a>'
        )
    return "<p>Telegram connection is not configured on this server.</p>"


def render_calendar_view(
    user: User,
    upcoming: list[dict],
    recent: list[dict],
    total_extractions: int,
    status: dict,
    connect_url: str | None,
    is_admin: bool,
) -> str:
    admin_link = '<a href="/admin">Admin</a>' if is_admin else ""
    rows = "\n".join(_upcoming_row(e) for e in upcoming) or "<p>Nothing upcoming.</p>"
    sync_status = "syncing to your Google Calendar" if user.calendar_sync_enabled else "sync paused"

    last_fetch = status.get("last_fetch") or {}
    recent_rows = "\n".join(_extraction_row(r, show_posted=True) for r in recent) or (
        '<tr><td colspan="6" style="text-align:center;color:#666">No extractions yet</td></tr>'
    )

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
      <label><input type="checkbox" name="calendar_sync_enabled" {"checked" if user.calendar_sync_enabled else ""}> Sync to my Google Calendar</label>
      <button type="submit">Save</button>
    </form>
    <h3>Telegram notifications</h3>
    {_telegram_section(user, connect_url)}
  </div>
  <div class="card">
    <h2>Status</h2>
    <div class="stats">
      <div class="stat"><div class="label">Last fetch</div><div class="value">{escape(_fmt_ts(last_fetch.get("ts")))}</div></div>
      <div class="stat"><div class="label">Poll interval</div><div class="value">{status.get("poll_interval_minutes", "-")}m</div></div>
      <div class="stat"><div class="label">Total extractions</div><div class="value">{total_extractions}</div></div>
    </div>
  </div>
  <div class="card">
    <h2>Database <span style="color:#999;font-weight:400;font-size:0.85rem">({total_extractions} extractions total, newest post first)</span></h2>
    <div class="scroll">
    <table>
    <thead><tr><th>Category</th><th>Company</th><th>Role</th><th>Deadline</th><th>Link</th><th>Posted</th></tr></thead>
    <tbody>
    {recent_rows}
    </tbody>
    </table>
    </div>
  </div>
</div>
</body></html>"""
