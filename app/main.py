"""FastAPI app: health endpoint plus the APScheduler-driven monitoring loop."""

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select, text

from app import audit, auth
from app.blog_client import BlogClient
from app.calendar_feed import DeadlineEvent, build_ics
from app.calendar_view import parse_week_param, render_week_view
from app.config import settings
from app.dashboard import render_dashboard
from app.db import SessionLocal, init_db
from app.extraction import CALENDAR_CATEGORIES
from app.logging_setup import read_recent_log_lines, setup_logging
from app.models import AllowedEmail, AuditLog, Extraction, FetchLog, Post, User
from app.notifications import send_telegram_message
from app.pipeline import _is_upcoming, run_cycle
from app.session_state import SessionMonitor
from app.site import (
    render_access_denied_page,
    render_calendar_view,
    render_homepage,
    render_login_page,
    render_post_detail,
    render_privacy_page,
    render_terms_page,
)
from app.telegram_link import build_connect_url, generate_link_code, parse_start_command
from app.timeutil import IST, parse_gmt, parse_ist

# Sort key for "no date" extractions in the dashboard's newest-event-first
# ordering - sorts below every real date rather than needing special-casing.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

logger = logging.getLogger(__name__)

monitor = SessionMonitor()
blog_client = BlogClient(settings.blog_base_url, settings.storage_state_path)
scheduler = BackgroundScheduler()


def cycle_job() -> None:
    run_cycle(blog_client, monitor, SessionLocal)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_dir)
    init_db()
    scheduler.add_job(
        cycle_job,
        "interval",
        minutes=settings.poll_interval_minutes,
        id="monitor_cycle",
        max_instances=1,
        coalesce=True,
        # First cycle runs right away on the scheduler's worker thread, not
        # inline: after downtime it can mean hundreds of LLM calls, and
        # awaiting it here kept the server from answering any request until
        # it finished. The worker thread also has no asyncio loop, which
        # session_refresh's sync_playwright() requires.
        next_run_time=datetime.now(timezone.utc),
    )
    scheduler.start()
    logger.info("scheduler started, polling every %d minutes", settings.poll_interval_minutes)
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="internblog-monitor", lifespan=lifespan)


class RedirectToLogin(Exception):
    pass


@app.exception_handler(RedirectToLogin)
def _redirect_to_login(request: Request, exc: RedirectToLogin) -> Response:
    return RedirectResponse("/login", status_code=303)


OAUTH_STATE_COOKIE = "internblog_oauth_state"


def get_current_user_or_redirect(request: Request) -> User:
    session_id = request.cookies.get(settings.session_cookie_name)
    with SessionLocal() as db:
        user = auth.get_session_user(db, session_id)
    if user is None:
        raise RedirectToLogin()
    return user


@app.get("/auth/start")
def auth_start() -> Response:
    """Begins the Google OAuth redirect. The /login page (added in Task 7)
    links here rather than redirecting to Google directly, so a visitor
    sees an explanatory card first."""
    url, state = auth.build_authorization_url()
    response = RedirectResponse(url, status_code=307)
    response.set_cookie(OAUTH_STATE_COOKIE, state, httponly=True, secure=True, samesite="lax", max_age=600)
    return response


@app.get("/auth/callback")
def auth_callback(request: Request, code: str, state: str) -> Response:
    expected_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not expected_state or not secrets.compare_digest(expected_state, state):
        raise HTTPException(status_code=400, detail="invalid oauth state")

    profile = auth.exchange_code_for_tokens(code)
    with SessionLocal() as db:
        if not auth.is_email_allowed(db, profile["email"]):
            response = HTMLResponse(render_access_denied_page(), status_code=403)
            response.delete_cookie(OAUTH_STATE_COOKIE)
            return response

        user = auth.upsert_user_from_google(db, profile)
        session_id = auth.create_session(db, user.id)

    response = RedirectResponse("/", status_code=307)
    response.delete_cookie(OAUTH_STATE_COOKIE)
    response.set_cookie(
        settings.session_cookie_name, session_id, httponly=True, secure=True, samesite="lax",
        max_age=settings.session_ttl_days * 86400,
    )
    return response


@app.get("/logout")
def logout(request: Request) -> Response:
    session_id = request.cookies.get(settings.session_cookie_name)
    with SessionLocal() as db:
        auth.delete_session(db, session_id)
    response = RedirectResponse("/login", status_code=307)
    response.delete_cookie(settings.session_cookie_name)
    return response


def _compute_status() -> dict:
    db_ok = True
    counts = {}
    last_fetch = None
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            counts = {
                "posts": db.scalar(select(func.count(Post.id))),
                "extractions": db.scalar(select(func.count(Extraction.id))),
            }
            row = db.execute(
                select(FetchLog).order_by(FetchLog.ts.desc()).limit(1)
            ).scalar_one_or_none()
            if row is not None:
                last_fetch = {
                    "ts": row.ts.isoformat() if row.ts else None,
                    "status": row.status,
                    "posts_count": row.posts_count,
                    "new": row.new_count,
                    "modified": row.modified_count,
                    "removed": row.removed_count,
                    "error": row.error,
                }
    except Exception as e:
        db_ok = False
        logger.exception("health check db failure")
        counts = {"error": str(e)}

    session_status = monitor.status()
    healthy = db_ok and session_status["session_alive"] and scheduler.running
    return {
        "healthy": healthy,
        "scheduler_running": scheduler.running,
        "poll_interval_minutes": settings.poll_interval_minutes,
        "db_ok": db_ok,
        "counts": counts,
        "session": session_status,
        "last_fetch": last_fetch,
    }


@app.get("/health")
def health() -> dict:
    return _compute_status()


@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    return render_login_page()


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page() -> str:
    return render_privacy_page()


@app.get("/terms", response_class=HTMLResponse)
def terms_page() -> str:
    return render_terms_page()


@app.get("/posts/{post_id}", response_class=HTMLResponse)
def post_detail(post_id: int, user: User = Depends(get_current_user_or_redirect)) -> str:
    with SessionLocal() as db:
        post = db.query(Post).filter_by(id=post_id).one_or_none()
    if post is None:
        raise HTTPException(status_code=404)
    return render_post_detail(post)


@app.get("/calendar", response_class=HTMLResponse)
def calendar_view(
    week: str | None = None, user: User = Depends(get_current_user_or_redirect)
) -> str:
    """Week-grid view of every deadline/test/OA/PPT extraction, global
    across all users (not scoped to the viewer's own synced events) - see
    app/calendar_view.py. Distinct from the ICS feed at /calendar/{token}.ics,
    which FastAPI routes separately since that path always ends in .ics."""
    monday = parse_week_param(week)
    week_end = monday + timedelta(days=7)
    with SessionLocal() as db:
        rows = db.execute(
            select(Extraction, Post)
            .join(Post, Extraction.post_id == Post.id)
            .where(Extraction.category.in_([c.value for c in CALENDAR_CATEGORIES]))
            .where(Extraction.deadline.is_not(None))
        ).all()

    events = []
    for extraction, post in rows:
        dt = parse_ist(extraction.deadline)
        if dt is None:
            continue
        local_date = dt.astimezone(IST).date()
        if monday <= local_date < week_end:
            events.append(
                {
                    "post_id": post.id,
                    "category": extraction.category,
                    "company": extraction.company,
                    "role": extraction.role,
                    "deadline_dt": dt,
                }
            )
    return render_week_view(monday, events)


@app.get("/google162e56c4a13e2140.html", response_class=HTMLResponse)
def google_site_verification() -> str:
    """Google Search Console domain-ownership verification file, required
    before OAuth branding (privacy/terms links) can show on the real
    consent screen. Content must match the file Search Console issued
    exactly - do not reformat or add anything else to this response."""
    return "google-site-verification: google162e56c4a13e2140.html"


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> str:
    """Anonymous visitors see a public homepage explaining the app's
    purpose (this is the exact "Homepage URL" Google's OAuth branding
    review checks) - only signed-in visitors see the calendar view."""
    session_id = request.cookies.get(settings.session_cookie_name)
    with SessionLocal() as db:
        session_user = auth.get_session_user(db, session_id)
    if session_user is None:
        return render_homepage()

    status = _compute_status()
    with SessionLocal() as db:
        db_user = db.query(User).filter_by(id=session_user.id).one()
        if not db_user.telegram_chat_id and not db_user.telegram_link_code:
            db_user.telegram_link_code = generate_link_code()
            db.commit()
        connect_url = (
            build_connect_url(settings.telegram_bot_username, db_user.telegram_link_code)
            if db_user.telegram_link_code
            else None
        )
        user = db_user

        total_extractions = db.scalar(select(func.count(Extraction.id))) or 0

        calendar_rows = db.execute(
            select(Extraction, Post)
            .join(Post, Extraction.post_id == Post.id)
            .where(Extraction.category.in_([c.value for c in CALENDAR_CATEGORIES]))
            .where(Extraction.deadline.is_not(None))
        ).all()
        upcoming = sorted(
            (
                {
                    "category": extraction.category, "company": extraction.company,
                    "role": extraction.role, "deadline": extraction.deadline, "link": post.link,
                }
                for extraction, post in calendar_rows
                if _is_upcoming(extraction.deadline)
            ),
            key=lambda r: r["deadline"],
        )

        recent_rows = db.execute(
            select(Extraction, Post).join(Post, Extraction.post_id == Post.id)
        ).all()
        recent = sorted(
            (
                {
                    "post_id": post.id,
                    "category": extraction.category,
                    "company": extraction.company,
                    "role": extraction.role,
                    "deadline": extraction.deadline,
                    "link": post.link,
                    "posted_at": post.date_gmt,
                    "created_at": extraction.created_at.isoformat() if extraction.created_at else None,
                }
                for extraction, post in recent_rows
            ),
            # Same ordering as the admin dashboard: newest post first, by
            # when it actually appeared on the blog.
            key=lambda r: parse_gmt(r["posted_at"]) or _EPOCH,
            reverse=True,
        )
    return render_calendar_view(
        user, upcoming, recent, total_extractions, status, connect_url, is_admin=auth.is_admin(user)
    )


@app.post("/settings")
def save_settings(
    request: Request,
    user: User = Depends(get_current_user_or_redirect),
    calendar_sync_enabled: bool = Form(False),
) -> Response:
    with SessionLocal() as db:
        db_user = db.query(User).filter_by(id=user.id).one()
        db_user.calendar_sync_enabled = calendar_sync_enabled
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/settings/disconnect-telegram")
def disconnect_telegram(user: User = Depends(get_current_user_or_redirect)) -> Response:
    with SessionLocal() as db:
        db_user = db.query(User).filter_by(id=user.id).one()
        db_user.telegram_chat_id = None
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict:
    """Receives every Telegram Bot API Update (registered via setWebhook's
    secret_token param, see docs/deployment.md). Only /start <code>
    messages do anything - everything else is a silent no-op, matching
    Telegram's expectation that a webhook always returns 200 quickly."""
    if not secrets.compare_digest(
        request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), settings.telegram_webhook_secret
    ):
        raise HTTPException(status_code=403)

    update = await request.json()
    parsed = parse_start_command(update)
    if parsed is None:
        return {"ok": True}
    chat_id, code = parsed

    with SessionLocal() as db:
        matched = db.query(User).filter_by(telegram_link_code=code).one_or_none()
        if matched is None:
            return {"ok": True}
        matched.telegram_chat_id = str(chat_id)
        matched.telegram_link_code = None
        db.commit()

    send_telegram_message(
        settings.telegram_bot_token, str(chat_id),
        "You're connected! internblog will send internship deadline updates here.",
    )
    return {"ok": True}


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(user: User = Depends(get_current_user_or_redirect)) -> str:
    if not auth.is_admin(user):
        raise HTTPException(status_code=404)
    status = _compute_status()
    with SessionLocal() as db:
        total_extractions = db.scalar(select(func.count(Extraction.id))) or 0

        db_users = db.query(User).all()
        joined_emails = {u.email.lower() for u in db_users}
        users = [
            {
                "email": u.email,
                "name": u.name,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "calendar_sync_enabled": u.calendar_sync_enabled,
                "has_calendar": u.calendar_id is not None,
                "has_event_calendar": u.event_calendar_id is not None,
                "telegram_connected": u.telegram_chat_id is not None,
                "status": "Joined",
            }
            for u in db_users
        ]
        # Allowlisted emails that haven't signed in yet - shown so an admin
        # sees an add take effect immediately, not just once someone signs in.
        users.extend(
            {
                "email": a.email,
                "name": None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "calendar_sync_enabled": False,
                "has_calendar": False,
                "has_event_calendar": False,
                "telegram_connected": False,
                "status": "Invited",
            }
            for a in db.query(AllowedEmail).all()
            if a.email.lower() not in joined_emails
        )

        calendar_rows = db.execute(
            select(Extraction, Post)
            .join(Post, Extraction.post_id == Post.id)
            .where(Extraction.category.in_([c.value for c in CALENDAR_CATEGORIES]))
            .where(Extraction.deadline.is_not(None))
        ).all()
        upcoming = sorted(
            (
                {
                    "post_id": post.id,
                    "category": extraction.category,
                    "company": extraction.company,
                    "role": extraction.role,
                    "deadline": extraction.deadline,
                    "link": post.link,
                }
                for extraction, post in calendar_rows
                if _is_upcoming(extraction.deadline)
            ),
            key=lambda r: r["deadline"],
        )

        recent_rows_query = db.execute(
            select(Extraction, Post).join(Post, Extraction.post_id == Post.id)
        ).all()
        recent = sorted(
            (
                {
                    "post_id": post.id,
                    "category": extraction.category,
                    "company": extraction.company,
                    "role": extraction.role,
                    "deadline": extraction.deadline,
                    "link": post.link,
                    "posted_at": post.date_gmt,
                    "created_at": extraction.created_at.isoformat() if extraction.created_at else None,
                }
                for extraction, post in recent_rows_query
            ),
            # Newest post first, by when it actually appeared on the blog -
            # not the extracted deadline, and not our own extraction time.
            key=lambda r: parse_gmt(r["posted_at"]) or _EPOCH,
            reverse=True,
        )

        audit_entries = [
            {
                "ts": a.ts.isoformat() if a.ts else None,
                "actor_email": a.actor_email,
                "action": a.action,
                "detail": a.detail,
            }
            for a in db.execute(select(AuditLog).order_by(AuditLog.ts.desc()).limit(100)).scalars().all()
        ]

    log_lines = read_recent_log_lines(settings.log_dir / "internblog.jsonl")
    return render_dashboard(
        status, upcoming, recent, total_extractions, users, settings.owner_email, audit_entries, log_lines
    )


@app.post("/admin/allowlist/add")
def admin_allowlist_add(
    user: User = Depends(get_current_user_or_redirect),
    email: str = Form(...),
) -> Response:
    if not auth.is_admin(user):
        raise HTTPException(status_code=404)
    email = email.strip().lower()
    with SessionLocal() as db:
        if email and db.query(AllowedEmail).filter_by(email=email).one_or_none() is None:
            db.add(AllowedEmail(email=email))
            db.commit()
            audit.record(db, user.email, "allowlist_add", detail=email)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/allowlist/remove")
def admin_allowlist_remove(
    user: User = Depends(get_current_user_or_redirect),
    email: str = Form(...),
) -> Response:
    """Removing an email deletes both the allowlist entry and any existing
    account for it (Session/TelegramNotification rows too), so access is
    revoked immediately rather than only for future sign-in attempts. The
    owner can't be removed via this route - is_email_allowed always lets
    them through regardless of the table, so removing them here would only
    delete their account without actually blocking them."""
    if not auth.is_admin(user):
        raise HTTPException(status_code=404)
    email = email.strip().lower()
    if email == settings.owner_email.lower():
        raise HTTPException(status_code=400, detail="cannot remove the owner")

    with SessionLocal() as db:
        db.query(AllowedEmail).filter(func.lower(AllowedEmail.email) == email).delete(synchronize_session=False)
        existing = db.query(User).filter(func.lower(User.email) == email).one_or_none()
        if existing is not None:
            from app.models import Session as SessionRow
            from app.models import TelegramNotification

            db.query(SessionRow).filter_by(user_id=existing.id).delete()
            db.query(TelegramNotification).filter(
                TelegramNotification.group_key.like(f"{existing.id}|%")
            ).delete(synchronize_session=False)
            db.delete(existing)
        db.commit()
        audit.record(db, user.email, "allowlist_remove", detail=email)
    return RedirectResponse("/admin", status_code=303)


@app.post("/cycle")
def trigger_cycle(user: User = Depends(get_current_user_or_redirect)) -> dict:
    """Manually trigger one monitoring cycle (debugging aid). Admin-only."""
    if not auth.is_admin(user):
        raise HTTPException(status_code=404)
    with SessionLocal() as db:
        audit.record(db, user.email, "cycle_trigger")
    cycle_job()
    return {"triggered": True, "session": monitor.status()}


@app.get("/calendar/{token}.ics")
def calendar_feed(token: str) -> Response:
    """ICS feed of application deadlines for Google Calendar subscription.
    The token guards the feed since it must be publicly fetchable."""
    if not settings.calendar_feed_token or token != settings.calendar_feed_token:
        raise HTTPException(status_code=404)

    with SessionLocal() as db:
        rows = (
            db.execute(
                select(Extraction, Post)
                .join(Post, Extraction.post_id == Post.id)
                .where(Extraction.category.in_([c.value for c in CALENDAR_CATEGORIES]))
                .where(Extraction.deadline.is_not(None))
            )
            .all()
        )
        events = [
            DeadlineEvent(
                uid=f"extraction-{extraction.id}",
                company=extraction.company or "Unknown company",
                role=extraction.role,
                deadline=extraction.deadline,
                deadline_end=extraction.deadline_end,
                link=post.link,
            )
            for extraction, post in rows
        ]

    return Response(content=build_ics(events), media_type="text/calendar")
