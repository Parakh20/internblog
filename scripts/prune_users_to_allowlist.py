"""One-off cleanup: remove every User (and their Session / TelegramNotification
rows) whose email is not in ALLOWED_EMAILS, now that public sign-up is being
restricted to a small explicit allowlist (placement office policy). Dry-run
by default - pass --apply to actually delete.

Usage: python scripts/prune_users_to_allowlist.py [--apply]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.db import SessionLocal, init_db
from app.models import Session as SessionRow
from app.models import TelegramNotification, User


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually delete rows (default: dry run)")
    args = parser.parse_args()

    allowed = settings.allowed_email_set
    if not allowed:
        print("ALLOWED_EMAILS is not set - refusing to run (would delete every user).")
        return

    init_db()
    with SessionLocal() as db:
        to_remove = [u for u in db.query(User).all() if u.email.lower() not in allowed]

        if not to_remove:
            print("No users to remove - every existing user already matches the allowlist.")
            return

        print(f"{'Deleting' if args.apply else 'Would delete'} {len(to_remove)} user(s):")
        for u in to_remove:
            print(f"  - {u.email} (id={u.id})")

        if not args.apply:
            print("\nDry run only - re-run with --apply to actually delete.")
            return

        for u in to_remove:
            db.query(SessionRow).filter_by(user_id=u.id).delete()
            db.query(TelegramNotification).filter(
                TelegramNotification.group_key.like(f"{u.id}|%")
            ).delete(synchronize_session=False)
            db.delete(u)
        db.commit()
        print("Done.")


if __name__ == "__main__":
    main()
