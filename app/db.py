"""Database engine and session factory."""

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Base


def _ensure_sqlite_dir(url: str) -> None:
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)
engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _add_missing_columns() -> None:
    """create_all only adds new tables, not columns on existing ones. This
    project has no migration tool, so patch known column additions here."""
    inspector = inspect(engine)
    if "extractions" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("extractions")}
    if "category" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE extractions ADD COLUMN category VARCHAR(32) DEFAULT 'other'"))
            # Superseded field from before the category column existed; backfill
            # so older rows still show up as a job listing rather than 'other'.
            if "is_job_posting" in existing:
                conn.execute(
                    text("UPDATE extractions SET category = 'new_listing' WHERE is_job_posting")
                )
    if "deadline_end" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE extractions ADD COLUMN deadline_end VARCHAR(64)"))
    if "is_job_posting" in existing:
        # Dead column: the ORM model no longer sets it (category replaced
        # it), and it has no server-side DEFAULT, so a NOT NULL constraint
        # left over from before this migration rejects every new insert.
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE extractions DROP COLUMN is_job_posting"))

    if "users" in inspector.get_table_names():
        users_columns = {c["name"] for c in inspector.get_columns("users")}
        if "telegram_link_code" not in users_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN telegram_link_code VARCHAR(64)"))
                conn.execute(
                    text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_telegram_link_code ON users (telegram_link_code)")
                )
        if "event_calendar_id" not in users_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN event_calendar_id VARCHAR(255)"))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()
