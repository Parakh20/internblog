"""Records admin-initiated actions (allowlist changes, manual cycle
trigger) to the AuditLog table, shown on /admin - see app/models.py."""

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(db: Session, actor_email: str, action: str, detail: str = "") -> None:
    db.add(AuditLog(actor_email=actor_email, action=action, detail=detail))
    db.commit()
