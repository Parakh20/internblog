"""FastAPI app: health endpoint plus the APScheduler-driven monitoring loop."""

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select, text

from app import auth
from app.blog_client import BlogClient
from app.calendar_feed import DeadlineEvent, build_ics
from app.config import settings
from app.dashboard import render_dashboard
from app.db import SessionLocal, init_db
from app.extraction import CALENDAR_CATEGORIES
from app.logging_setup import setup_logging
from app.models import Extraction, FetchLog, Post, User
from app.pipeline import _is_upcoming, run_cycle
from app.session_state import SessionMonitor
from app.site import render_calendar_view, render_login_page
from app.timeutil import parse_gmt

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
    )
    scheduler.start()
    logger.info("scheduler started, polling every %d minutes", settings.poll_interval_minutes)
    # Run off the event loop thread: cycle_job() reaches session_refresh's
    # sync_playwright(), which raises if called from a thread with a running
    # asyncio loop - true here since lifespan itself runs on that loop.
    await asyncio.to_thread(cycle_job)
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


@app.get("/", response_class=HTMLResponse)
def home(user: User = Depends(get_current_user_or_redirect)) -> str:
    status = _compute_status()
    with SessionLocal() as db:
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
        user, upcoming, recent, total_extractions, status, is_admin=auth.is_admin(user)
    )


@app.post("/settings")
def save_settings(
    request: Request,
    user: User = Depends(get_current_user_or_redirect),
    telegram_chat_id: str = Form(""),
    calendar_sync_enabled: bool = Form(False),
) -> Response:
    with SessionLocal() as db:
        db_user = db.query(User).filter_by(id=user.id).one()
        db_user.telegram_chat_id = telegram_chat_id.strip() or None
        db_user.calendar_sync_enabled = calendar_sync_enabled
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(user: User = Depends(get_current_user_or_redirect)) -> str:
    if not auth.is_admin(user):
        raise HTTPException(status_code=404)
    status = _compute_status()
    with SessionLocal() as db:
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

        recent_rows = db.execute(
            select(Extraction, Post).join(Post, Extraction.post_id == Post.id)
        ).all()
        recent = sorted(
            (
                {
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
            # Newest post first, by when it actually appeared on the blog -
            # not the extracted deadline, and not our own extraction time.
            key=lambda r: parse_gmt(r["posted_at"]) or _EPOCH,
            reverse=True,
        )
    return render_dashboard(status, upcoming, recent, total_extractions)


@app.post("/cycle")
def trigger_cycle(user: User = Depends(get_current_user_or_redirect)) -> dict:
    """Manually trigger one monitoring cycle (debugging aid). Admin-only."""
    if not auth.is_admin(user):
        raise HTTPException(status_code=404)
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
