from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Session as SessionRow, User


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_user_round_trips_all_fields():
    db = _db()
    user = User(
        google_sub="sub-123",
        email="someone@example.com",
        name="Someone",
        picture_url="https://example.com/pic.jpg",
        calendar_refresh_token_encrypted="ciphertext",
        calendar_id="cal-abc",
        calendar_sync_enabled=True,
        telegram_chat_id="12345",
    )
    db.add(user)
    db.commit()

    fetched = db.query(User).filter_by(google_sub="sub-123").one()
    assert fetched.email == "someone@example.com"
    assert fetched.calendar_sync_enabled is True
    assert fetched.telegram_chat_id == "12345"


def test_user_email_and_google_sub_are_unique():
    db = _db()
    db.add(User(google_sub="dup", email="a@example.com"))
    db.commit()
    db.add(User(google_sub="dup", email="b@example.com"))
    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.commit()


def test_session_round_trips_and_links_to_user():
    db = _db()
    user = User(google_sub="sub-456", email="x@example.com")
    db.add(user)
    db.commit()

    session_row = SessionRow(
        id="tok-abc",
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(session_row)
    db.commit()

    fetched = db.query(SessionRow).filter_by(id="tok-abc").one()
    assert fetched.user_id == user.id
