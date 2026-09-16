from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth
from app.config import settings
from app.models import AllowedEmail, Base, Session as SessionRow, User


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_upsert_user_from_google_creates_new_user_and_calendar(db, monkeypatch):
    monkeypatch.setattr(settings, "secret_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(auth, "create_secondary_calendar", lambda access_token: "new-cal-id")
    profile = {
        "google_sub": "sub-1",
        "email": "a@example.com",
        "name": "A",
        "picture": "https://x/pic.jpg",
        "refresh_token": "rt-1",
        "access_token": "at-1",
    }
    user = auth.upsert_user_from_google(db, profile)

    assert user.google_sub == "sub-1"
    assert user.calendar_id == "new-cal-id"
    from app.crypto import decrypt_token

    assert decrypt_token(user.calendar_refresh_token_encrypted) == "rt-1"


def test_upsert_user_from_google_updates_existing_user_without_recreating_calendar(db, monkeypatch):
    created_calendars = []
    monkeypatch.setattr(
        auth, "create_secondary_calendar", lambda access_token: created_calendars.append(1) or "should-not-be-used"
    )
    existing = User(
        google_sub="sub-2", email="old@example.com", calendar_id="existing-cal",
        calendar_refresh_token_encrypted="ignored",
    )
    db.add(existing)
    db.commit()

    profile = {
        "google_sub": "sub-2", "email": "new@example.com", "name": "B",
        "picture": None, "refresh_token": None, "access_token": "at-2",
    }
    user = auth.upsert_user_from_google(db, profile)

    assert user.email == "new@example.com"
    assert user.calendar_id == "existing-cal"
    assert created_calendars == []


def test_upsert_user_from_google_adopts_migrated_owner_row_by_email(db, monkeypatch):
    """Reproduces scripts/migrate_owner_to_users_table.py's placeholder-row
    scenario: a pre-existing User row with a placeholder google_sub and the
    owner's real email. On the owner's real first login, the profile
    carries a different real google_sub but the same email - this must
    adopt the existing row (updating its google_sub) rather than insert a
    second row and violate the unique email constraint."""
    monkeypatch.setattr(auth, "create_secondary_calendar", lambda access_token: "should-not-be-used")
    placeholder = User(
        google_sub="pending-owner@example.com", email="owner@example.com",
        calendar_id="existing-cal", calendar_refresh_token_encrypted="ignored",
    )
    db.add(placeholder)
    db.commit()
    placeholder_id = placeholder.id

    profile = {
        "google_sub": "real-google-sub-123", "email": "owner@example.com", "name": "Owner",
        "picture": None, "refresh_token": None, "access_token": "at-owner",
    }

    user = auth.upsert_user_from_google(db, profile)  # must not raise IntegrityError

    assert user.id == placeholder_id
    assert user.google_sub == "real-google-sub-123"
    assert db.query(User).filter_by(email="owner@example.com").count() == 1


def test_create_and_get_session_round_trips(db):
    user = User(google_sub="sub-3", email="c@example.com")
    db.add(user)
    db.commit()

    session_id = auth.create_session(db, user.id)
    fetched = auth.get_session_user(db, session_id)

    assert fetched is not None
    assert fetched.id == user.id


def test_get_session_user_returns_none_for_missing_or_expired(db):
    assert auth.get_session_user(db, None) is None
    assert auth.get_session_user(db, "nonexistent") is None

    user = User(google_sub="sub-4", email="d@example.com")
    db.add(user)
    db.commit()
    db.add(SessionRow(id="expired", user_id=user.id, expires_at=datetime.now(timezone.utc) - timedelta(days=1)))
    db.commit()

    assert auth.get_session_user(db, "expired") is None


def test_delete_session_removes_row(db):
    user = User(google_sub="sub-5", email="e@example.com")
    db.add(user)
    db.commit()
    session_id = auth.create_session(db, user.id)

    auth.delete_session(db, session_id)

    assert auth.get_session_user(db, session_id) is None


def test_is_admin_matches_owner_email_only(monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    assert auth.is_admin(User(email="owner@example.com"))
    assert not auth.is_admin(User(email="someone-else@example.com"))


def test_is_admin_is_false_for_everyone_when_owner_email_unset(monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "")
    assert not auth.is_admin(User(email=""))


def test_is_email_allowed_owner_always_allowed_regardless_of_table(db, monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    assert auth.is_email_allowed(db, "owner@example.com")


def test_is_email_allowed_matches_allowlist_case_insensitively(db, monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    db.add(AllowedEmail(email="someone@example.com"))
    db.commit()
    assert auth.is_email_allowed(db, "Someone@Example.com")


def test_is_email_allowed_rejects_email_not_on_allowlist(db, monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    db.add(AllowedEmail(email="someone@example.com"))
    db.commit()
    assert not auth.is_email_allowed(db, "stranger@example.com")


def test_is_email_allowed_rejects_everyone_when_table_empty_except_owner(db, monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    assert not auth.is_email_allowed(db, "anyone@example.com")
