"""Re-run the roll-number -> department join (app/roll_lookup.py) against
posts that were scraped before that feature existed, so already-stored
shortlist/result tables get the Department column too, not just new ones.

Safe to re-run: annotate_roll_departments() is a no-op on content that's
already been annotated.

Usage:
    python scripts/backfill_roll_departments.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.db import SessionLocal, init_db
from app.logging_setup import setup_logging
from app.models import Post
from app.pipeline import build_extraction_attempts
from app.roll_lookup import annotate_roll_departments

logger = logging.getLogger("backfill_roll_departments")


def main() -> int:
    setup_logging(settings.log_dir)
    init_db()

    attempts = build_extraction_attempts()
    with SessionLocal() as db:
        candidates = (
            db.query(Post).filter(Post.raw_html.ilike("%<table%")).order_by(Post.wp_id).all()
        )
        logger.info("%d posts have a table, checking for unjoined roll numbers", len(candidates))
        updated = 0
        for row in candidates:
            annotated = annotate_roll_departments(row.raw_html, attempts=attempts)
            if annotated != row.raw_html:
                row.raw_html = annotated
                updated += 1
        db.commit()
        logger.info("backfill finished, %d of %d posts updated", updated, len(candidates))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
