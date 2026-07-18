"""Verifies the ad-hoc column-addition migration in app/db.py runs safely
against a database created before event_calendar_id existed, without
dropping any existing data - the same style of check this project already
relies on for is_job_posting -> category and the telegram_link_code add."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db import _add_missing_columns
from app.models import Base


def test_migration_adds_event_calendar_id_to_pre_existing_users_table(tmp_path):
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    # Simulate a pre-migration users table: drop the column, insert a row.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users DROP COLUMN event_calendar_id"))
        conn.execute(
            text(
                "INSERT INTO users (google_sub, email, calendar_sync_enabled, created_at) "
                "VALUES ('legacy-sub', 'legacy@example.com', 1, '2026-01-01 00:00:00')"
            )
        )

    import app.db as db_module

    original_engine = db_module.engine
    db_module.engine = engine
    try:
        _add_missing_columns()
    finally:
        db_module.engine = original_engine

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("users")}
    assert "event_calendar_id" in columns

    Session = sessionmaker(bind=engine)
    session = Session()
    row = session.execute(text("SELECT email FROM users WHERE google_sub = 'legacy-sub'")).one()
    assert row.email == "legacy@example.com"
