import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import auth, main
from app.db import SessionLocal, init_db


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(settings, "secret_encryption_key", Fernet.generate_key().decode())
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(settings.database_url, future=True)
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    monkeypatch.setattr("app.db.engine", engine)
    init_db()


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def test_auth_start_redirects_to_google(client, monkeypatch):
    monkeypatch.setattr(auth, "build_authorization_url", lambda: ("https://accounts.google.com/fake", "state123"))
    response = client.get("/auth/start", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://accounts.google.com/fake"
    assert "internblog_oauth_state" in response.cookies


def test_callback_rejects_mismatched_state(client):
    response = client.get(
        "/auth/callback?code=abc&state=wrong",
        cookies={"internblog_oauth_state": "expected"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_callback_creates_session_and_redirects_home(client, monkeypatch):
    from app.models import AllowedEmail

    db = main.SessionLocal()
    db.add(AllowedEmail(email="x@example.com"))
    db.commit()

    monkeypatch.setattr(auth, "exchange_code_for_tokens", lambda code: {
        "google_sub": "sub-x", "email": "x@example.com", "name": "X",
        "picture": None, "refresh_token": "rt", "access_token": "at",
    })
    monkeypatch.setattr(auth, "create_secondary_calendar", lambda access_token: "cal-x")

    response = client.get(
        "/auth/callback?code=abc&state=expected",
        cookies={"internblog_oauth_state": "expected"},
        follow_redirects=False,
    )
    assert response.status_code == 307
    assert response.headers["location"] == "/"
    assert main.settings.session_cookie_name in response.cookies


def test_callback_rejects_email_not_on_allowlist(client, monkeypatch):
    monkeypatch.setattr(auth, "exchange_code_for_tokens", lambda code: {
        "google_sub": "sub-z", "email": "not-allowed@example.com", "name": "Z",
        "picture": None, "refresh_token": "rt", "access_token": "at",
    })

    response = client.get(
        "/auth/callback?code=abc&state=expected",
        cookies={"internblog_oauth_state": "expected"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert main.settings.session_cookie_name not in response.cookies

    from app.models import User

    db = main.SessionLocal()
    assert db.query(User).filter_by(email="not-allowed@example.com").one_or_none() is None


def test_callback_allows_owner_even_when_allowlist_table_is_empty(client, monkeypatch):
    monkeypatch.setattr(main.settings, "owner_email", "owner@example.com")
    monkeypatch.setattr(auth, "exchange_code_for_tokens", lambda code: {
        "google_sub": "sub-owner", "email": "owner@example.com", "name": "Owner",
        "picture": None, "refresh_token": "rt", "access_token": "at",
    })
    monkeypatch.setattr(auth, "create_secondary_calendar", lambda access_token: "cal-owner")

    response = client.get(
        "/auth/callback?code=abc&state=expected",
        cookies={"internblog_oauth_state": "expected"},
        follow_redirects=False,
    )
    assert response.status_code == 307
    assert main.settings.session_cookie_name in response.cookies


def test_logout_clears_session(client, monkeypatch):
    from app.models import AllowedEmail

    db = main.SessionLocal()
    db.add(AllowedEmail(email="y@example.com"))
    db.commit()

    monkeypatch.setattr(auth, "exchange_code_for_tokens", lambda code: {
        "google_sub": "sub-y", "email": "y@example.com", "name": "Y",
        "picture": None, "refresh_token": "rt", "access_token": "at",
    })
    monkeypatch.setattr(auth, "create_secondary_calendar", lambda access_token: "cal-y")
    login_response = client.get(
        "/auth/callback?code=abc&state=expected",
        cookies={"internblog_oauth_state": "expected"},
        follow_redirects=False,
    )
    session_cookie = login_response.cookies[main.settings.session_cookie_name]

    logout_response = client.get(
        "/logout", cookies={main.settings.session_cookie_name: session_cookie}, follow_redirects=False
    )
    assert logout_response.status_code == 307
    assert logout_response.headers["location"] == "/login"
