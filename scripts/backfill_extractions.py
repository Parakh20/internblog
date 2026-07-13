"""Run Claude extraction for stored posts that have no extraction yet.

Use after topping up API credits, after transient extraction failures, or
after enabling extraction for the first time.

Usage:
    python scripts/backfill_extractions.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.db import SessionLocal, init_db
from app.logging_setup import setup_logging
from app.models import Extraction, Post
from app.pipeline import run_extraction

logger = logging.getLogger("backfill")


def main() -> int:
    setup_logging(settings.log_dir)
    init_db()
    if not settings.anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY is not set")
        return 1

    with SessionLocal() as db:
        extracted_post_ids = {pid for (pid,) in db.query(Extraction.post_id).all()}
        pending = (
            db.query(Post)
            .filter(Post.removed.is_(False), Post.id.notin_(extracted_post_ids))
            .order_by(Post.wp_id)
            .all()
        )
        logger.info("%d posts pending extraction", len(pending))
        done = 0
        for row in pending:
            run_extraction(db, row)
            db.commit()
            done += 1
        logger.info("backfill finished, %d posts processed", done)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
