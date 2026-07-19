"""One-off: re-push every still-upcoming deadline/test/OA/PPT extraction to
each opted-in user's Google Calendar, using the current (self-healing)
push_calendar_event_for_user. Run this after fixing a calendar-sync bug so
already-extracted data gets repaired immediately instead of only trickling
in the next time a new blog post triggers a push. Idempotent - re-running
just upserts the same events again.

Usage: python scripts/repush_upcoming_calendar_events.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal, init_db
from app.extraction import CALENDAR_CATEGORIES
from app.models import Extraction, Post, User
from app.pipeline import _is_upcoming, push_calendar_event_for_user


def main() -> None:
    init_db()
    with SessionLocal() as db:
        rows = (
            db.query(Extraction, Post)
            .join(Post, Extraction.post_id == Post.id)
            .filter(Extraction.category.in_([c.value for c in CALENDAR_CATEGORIES]))
            .filter(Extraction.deadline.isnot(None))
            .all()
        )
        upcoming = [(e, p) for e, p in rows if _is_upcoming(e.deadline)]
        users = [u for u in db.query(User).all() if u.calendar_sync_enabled and u.calendar_refresh_token_encrypted]

        print(f"Re-pushing {len(upcoming)} upcoming calendar-category extraction(s) to {len(users)} synced user(s)...")
        for extraction, post in upcoming:
            for user in users:
                try:
                    push_calendar_event_for_user(db, user, extraction, post)
                except Exception as e:
                    print(f"  failed: user={user.email} extraction={extraction.id} ({extraction.company}): {e}")
        print("Done.")


if __name__ == "__main__":
    main()
