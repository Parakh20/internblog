"""FastAPI app: health endpoint plus the APScheduler-driven monitoring loop."""

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from sqlalchemy import func, select, text

from app.blog_client import BlogClient
from app.config import settings
from app.db import SessionLocal, init_db
from app.logging_setup import setup_logging
from app.models import Extraction, FetchLog, Post
from app.pipeline import run_cycle
from app.session_state import SessionMonitor

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
    cycle_job()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="internblog-monitor", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
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


@app.post("/cycle")
def trigger_cycle() -> dict:
    """Manually trigger one monitoring cycle (debugging aid)."""
    cycle_job()
    return {"triggered": True, "session": monitor.status()}
