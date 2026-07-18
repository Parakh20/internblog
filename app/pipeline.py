"""One monitoring cycle: fetch, snapshot, diff, extract, store.

Every cycle writes a FetchLog row regardless of outcome so the health
endpoint and logs always reflect reality.
"""

import json
import logging
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.blog_client import BlogClient, CookieLoadError, SessionExpiredError
from app.change_detection import ChangeSet, KnownPost, detect_changes, post_hash
from app.config import settings
from app.extraction import (
    CALENDAR_CATEGORIES,
    CALENDAR_EVENT_GROUP,
    NOTIFY_CATEGORIES,
    NOTIFY_EVENT_GROUP,
    PostCategory,
    dedup_key,
    extract_posting,
    make_llm_client,
)
from app.crypto import decrypt_token
from app.google_calendar import (
    event_id_for,
    event_id_for_company,
    get_access_token,
    upsert_event,
)
from app.models import Extraction, FetchLog, Post, TelegramNotification, User
from app.notifications import edit_telegram_message, format_notification_message, send_telegram_message
from app.session_refresh import silent_refresh
from app.session_state import SessionMonitor
from app.snapshots import save_snapshot
from app.timeutil import parse_ist

logger = logging.getLogger(__name__)

REFRESH_COOLDOWN_SECONDS = 90
_last_refresh_attempt = 0.0


def _is_upcoming(iso_value: str) -> bool:
    """True if a deadline hasn't passed yet. Guards against notifying or
    calendar-pushing already-elapsed dates - e.g. when backfilling
    extraction for old posts that were never processed the first time."""
    dt = parse_ist(iso_value)
    return dt is not None and dt > datetime.now(timezone.utc)


def _try_silent_refresh() -> bool:
    """Attempt a credential-free session refresh, at most once per cooldown."""
    global _last_refresh_attempt
    now = time.monotonic()
    if now - _last_refresh_attempt < REFRESH_COOLDOWN_SECONDS:
        logger.info("skipping silent refresh, within cooldown")
        return False
    _last_refresh_attempt = now
    from app.config import PROJECT_ROOT

    return silent_refresh(
        PROJECT_ROOT / "browser_profile",
        settings.blog_base_url + "/",
        settings.storage_state_path,
    )


def load_known_posts(db: Session) -> dict[int, KnownPost]:
    rows = db.query(Post.wp_id, Post.modified_gmt, Post.content_hash, Post.removed).all()
    return {
        wp_id: KnownPost(modified_gmt=modified, content_hash=chash, removed=removed)
        for wp_id, modified, chash, removed in rows
    }


def upsert_post(db: Session, wp_post: dict) -> Post:
    row = db.query(Post).filter_by(wp_id=wp_post["id"]).one_or_none()
    title = wp_post.get("title", {}).get("rendered", "")
    content = wp_post.get("content", {}).get("rendered", "")
    if row is None:
        row = Post(wp_id=wp_post["id"])
        db.add(row)
    row.slug = wp_post.get("slug", "")
    row.title = title
    row.link = wp_post.get("link", "")
    row.date_gmt = wp_post.get("date_gmt", "")
    row.modified_gmt = wp_post.get("modified_gmt", "")
    row.content_hash = post_hash(wp_post)
    row.raw_html = content
    row.removed = False
    from app.models import utcnow

    row.last_changed_at = utcnow()
    db.flush()
    return row


def _build_extraction_attempts() -> list[tuple]:
    attempts = []
    if settings.groq_api_key:
        groq_client = make_llm_client(settings.groq_base_url, settings.groq_api_key)
        attempts.append((groq_client, settings.groq_model))
    if settings.llm_api_key:
        openrouter_client = make_llm_client(settings.llm_base_url, settings.llm_api_key)
        attempts.extend((openrouter_client, model) for model in settings.llm_model_list)
    return attempts


def run_extraction(db: Session, row: Post) -> None:
    if not settings.extraction_enabled:
        return
    attempts = _build_extraction_attempts()
    if not attempts:
        logger.warning("no LLM API key set, skipping extraction for post %s", row.wp_id)
        return
    parsed = extract_posting(
        attempts,
        title=row.title,
        content_html=row.raw_html,
        post_date=row.date_gmt,
    )
    if parsed is None:
        return
    key = dedup_key(parsed.company, parsed.role, parsed.deadline)
    existing = (
        db.query(Extraction).filter_by(post_id=row.id, dedup_key=key).one_or_none()
    )
    if existing is not None:
        logger.info("extraction unchanged for post %s (dedup key match)", row.wp_id)
        return
    extraction = Extraction(
        post_id=row.id,
        dedup_key=key,
        company=parsed.company,
        role=parsed.role,
        deadline=parsed.deadline,
        deadline_end=parsed.event_end,
        cgpa_cutoff=parsed.cgpa_cutoff,
        eligible_branches=json.dumps(parsed.eligible_branches),
        stipend=parsed.stipend,
        location=parsed.location,
        application_link=parsed.application_link,
        category=parsed.category,
        raw_json=parsed.model_dump_json(),
    )
    db.add(extraction)
    db.flush()  # assigns extraction.id, needed for the calendar event key below
    logger.info(
        "extracted post %s: category=%s company=%r role=%r deadline=%r",
        row.wp_id, parsed.category, parsed.company, parsed.role, parsed.deadline,
    )
    # Only a stated deadline can be stale; a post with no deadline at all
    # (e.g. a listing that doesn't mention one) is never suppressed here.
    is_stale = bool(parsed.deadline) and not _is_upcoming(parsed.deadline)
    if not is_stale and (parsed.category in NOTIFY_CATEGORIES or parsed.category in CALENDAR_CATEGORIES):
        push_to_all_users(db, extraction, row)


def send_or_edit_telegram_for_user(db: Session, user: User, extraction: Extraction, message: str) -> None:
    """Same edit-in-place behavior as before, but the group key is scoped
    per user so one user's deadline-extension edit never touches another
    user's message."""
    group = NOTIFY_EVENT_GROUP.get(PostCategory(extraction.category))
    group_key = (
        f"{user.id}|{extraction.company.strip().lower()}|{group}"
        if extraction.company and group
        else None
    )

    existing = (
        db.query(TelegramNotification).filter_by(group_key=group_key).one_or_none()
        if group_key
        else None
    )
    if existing is not None and edit_telegram_message(
        settings.telegram_bot_token, user.telegram_chat_id, existing.message_id, message
    ):
        return

    message_id = send_telegram_message(settings.telegram_bot_token, user.telegram_chat_id, message)
    if message_id is None or group_key is None:
        return
    if existing is not None:
        existing.message_id = message_id
    else:
        db.add(TelegramNotification(group_key=group_key, message_id=message_id))
    db.commit()


def push_calendar_event_for_user(user: User, extraction: Extraction, row: Post) -> None:
    refresh_token = decrypt_token(user.calendar_refresh_token_encrypted)
    access_token = get_access_token(
        settings.google_oauth_client_id, settings.google_oauth_client_secret, refresh_token
    )
    if access_token is None:
        return
    summary = f"{extraction.company or 'Unknown company'}" + (f" - {extraction.role}" if extraction.role else "")
    group = CALENDAR_EVENT_GROUP.get(PostCategory(extraction.category))
    event_id = (
        event_id_for_company(extraction.company, group)
        if extraction.company and group
        else event_id_for(extraction.id)
    )
    upsert_event(
        access_token, user.calendar_id, event_id,
        summary=summary, description=row.link,
        start_iso=extraction.deadline, end_iso=extraction.deadline_end,
    )


def push_to_all_users(db: Session, extraction: Extraction, row: Post) -> None:
    """Loop over every signed-in user and push this extraction to whichever
    channels they've opted into. A failure for one user (revoked token, bad
    chat id) is logged and skipped - it never blocks another user's push or
    aborts the fetch cycle."""
    message = None
    for user in db.query(User).all():
        if user.calendar_sync_enabled and user.calendar_refresh_token_encrypted:
            try:
                push_calendar_event_for_user(user, extraction, row)
            except Exception:
                logger.exception("calendar push failed for user %s", user.email)
        if user.telegram_chat_id:
            if message is None:
                message = format_notification_message(
                    category=PostCategory(extraction.category), company=extraction.company,
                    role=extraction.role, deadline=extraction.deadline, stipend=extraction.stipend,
                )
            try:
                send_or_edit_telegram_for_user(db, user, extraction, message)
            except Exception:
                logger.exception("telegram push failed for user %s", user.email)


def apply_changes(db: Session, changes: ChangeSet) -> None:
    for wp_post in changes.new + changes.modified + changes.restored:
        row = upsert_post(db, wp_post)
        run_extraction(db, row)
    for wp_id in changes.removed_wp_ids:
        row = db.query(Post).filter_by(wp_id=wp_id).one_or_none()
        if row is not None:
            row.removed = True
            logger.info("post %s removed from blog", wp_id)


def run_cycle(client: BlogClient, monitor: SessionMonitor, db_factory) -> None:
    db: Session = db_factory()
    log_row = FetchLog(status="error", session_alive=monitor.alive)
    try:
        try:
            result = client.fetch_posts()
        except (SessionExpiredError, CookieLoadError) as e:
            logger.warning("session check failed (%s), attempting silent refresh", e)
            refreshed = _try_silent_refresh()
            retried = False
            if refreshed:
                try:
                    result = client.fetch_posts()
                    retried = True
                except (SessionExpiredError, CookieLoadError) as e2:
                    e = e2
            if not retried:
                monitor.mark_expired(str(e))
                log_row.status = "session_expired"
                log_row.session_alive = False
                log_row.error = str(e)
                return
        except Exception as e:
            logger.exception("fetch failed")
            log_row.status = "fetch_error"
            log_row.error = str(e)
            return

        monitor.mark_ok()
        log_row.session_alive = True
        log_row.posts_count = len(result.posts)

        snapshot_path = save_snapshot(settings.snapshot_dir, result.posts)
        logger.info("snapshot saved to %s (%d posts)", snapshot_path.name, len(result.posts))

        known = load_known_posts(db)
        changes = detect_changes(result.posts, known)
        log_row.new_count = len(changes.new)
        log_row.modified_count = len(changes.modified) + len(changes.restored)
        log_row.removed_count = len(changes.removed_wp_ids)

        if changes.has_changes:
            logger.info(
                "changes detected: %d new, %d modified, %d removed, %d restored",
                len(changes.new), len(changes.modified),
                len(changes.removed_wp_ids), len(changes.restored),
            )
            apply_changes(db, changes)
        else:
            logger.info("no changes (%d posts)", len(result.posts))

        log_row.status = "ok"
        db.commit()
    finally:
        try:
            db.add(log_row)
            db.commit()
        except Exception:
            logger.exception("failed to write fetch log")
            db.rollback()
        db.close()
