"""One-off seed: populate the AllowedEmail table from every User row that
already exists, so the current members can still sign back in (session
expiry, cleared cookies, etc.) after the admin-managed allowlist ships -
the owner doesn't need an entry since is_email_allowed always lets them
through. Safe to re-run - skips emails already in the table.

Usage: python scripts/seed_allowlist_from_existing_users.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.db import SessionLocal, init_db
from app.models import AllowedEmail, User


def main() -> None:
    init_db()
    with SessionLocal() as db:
        existing = {a.email.lower() for a in db.query(AllowedEmail).all()}
        added = []
        for u in db.query(User).all():
            email = u.email.lower()
            if email == settings.owner_email.lower() or email in existing:
                continue
            db.add(AllowedEmail(email=email))
            added.append(email)
        db.commit()

    if added:
        print(f"Added {len(added)} email(s) to the allowlist:")
        for email in added:
            print(f"  - {email}")
    else:
        print("Nothing to add - every existing user is already on the allowlist (or is the owner).")


if __name__ == "__main__":
    main()
