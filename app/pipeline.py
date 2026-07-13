"""One monitoring cycle: fetch, snapshot, diff, extract, store.

Every cycle writes a FetchLog row regardless of outcome so the health
endpoint and logs always reflect reality.
"""

import json
import logging
import time

import anthropic
from sqlalchemy.orm import Session

from app.blog_client import BlogClient, CookieLoadError, SessionExpiredError
from app.change_detection import ChangeSet, KnownPost, detect_changes, post_hash
from app.config import settings
from app.extraction import dedup_key, extract_posting
from app.models import Extraction, FetchLog, Post
from app.session_refresh import silent_refresh
from app.session_state import SessionMonitor
from app.snapshots import save_snapshot

logger = logging.getLogger(__name__)

REFRESH_COOLDOWN_SECONDS = 600
_last_refresh_attempt = 0.0


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


def run_extraction(db: Session, row: Post) -> None:
    if not settings.extraction_enabled:
        return
    if not settings.anthropic_api_key:
        logger.warning("ANTHROPIC_API_KEY not set, skipping extraction for post %s", row.wp_id)
        return
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    parsed = extract_posting(
        client,
        settings.anthropic_model,
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
    db.add(
        Extraction(
            post_id=row.id,
            dedup_key=key,
            company=parsed.company,
            role=parsed.role,
            deadline=parsed.deadline,
            cgpa_cutoff=parsed.cgpa_cutoff,
            eligible_branches=json.dumps(parsed.eligible_branches),
            stipend=parsed.stipend,
            location=parsed.location,
            application_link=parsed.application_link,
            raw_json=parsed.model_dump_json(),
        )
    )
    logger.info(
        "extracted post %s: company=%r role=%r deadline=%r",
        row.wp_id, parsed.company, parsed.role, parsed.deadline,
    )


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
