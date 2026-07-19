from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import audit
from app.models import AuditLog, Base


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_record_creates_audit_log_row():
    db = _db()
    audit.record(db, "owner@example.com", "allowlist_add", detail="added new@example.com")

    stored = db.query(AuditLog).one()
    assert stored.actor_email == "owner@example.com"
    assert stored.action == "allowlist_add"
    assert stored.detail == "added new@example.com"
    assert stored.ts is not None


def test_record_defaults_detail_to_empty_string():
    db = _db()
    audit.record(db, "owner@example.com", "cycle_trigger")

    stored = db.query(AuditLog).one()
    assert stored.detail == ""
