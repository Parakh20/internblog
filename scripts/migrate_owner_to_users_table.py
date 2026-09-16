"""One-off migration: move the original single-user Google Calendar
refresh token / calendar id / Telegram chat id (previously hardcoded in
.env) into a users table row for the owner, before those settings are
removed from app/config.py. Run this once during the deploy that ships
multi-user login, before deleting the old env vars.

Usage: python scripts/google_calendar_auth.py must have already been run
at some point (that's where GOOGLE_CALENDAR_REFRESH_TOKEN originally came
from) - this script just re-homes the values already in .env into the DB.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.crypto import encrypt_token
from app.db import SessionLocal, init_db
from app.models import User

OWNER_EMAIL = os.environ["OWNER_EMAIL"]
OWNER_GOOGLE_SUB = os.environ.get("OWNER_GOOGLE_SUB", "")  # optional, filled in on first real login anyway
REFRESH_TOKEN = os.environ.get("GOOGLE_CALENDAR_REFRESH_TOKEN", "")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def main() -> None:
    if not REFRESH_TOKEN or not CALENDAR_ID:
        print("GOOGLE_CALENDAR_REFRESH_TOKEN / GOOGLE_CALENDAR_ID not set, nothing to migrate.")
        return

    init_db()
    with SessionLocal() as db:
        user = db.query(User).filter_by(email=OWNER_EMAIL).one_or_none()
        if user is None:
            user = User(google_sub=OWNER_GOOGLE_SUB or f"pending-{OWNER_EMAIL}", email=OWNER_EMAIL)
            db.add(user)
        user.calendar_refresh_token_encrypted = encrypt_token(REFRESH_TOKEN)
        user.calendar_id = CALENDAR_ID
        user.calendar_sync_enabled = True
        if TELEGRAM_CHAT_ID:
            user.telegram_chat_id = TELEGRAM_CHAT_ID
        db.commit()
        print(f"Migrated owner row for {OWNER_EMAIL} (id={user.id}).")
        print(
            "Note: google_sub is a placeholder until this user's first real "
            "sign-in via /login, which will overwrite it with their actual sub."
        )


if __name__ == "__main__":
    main()
