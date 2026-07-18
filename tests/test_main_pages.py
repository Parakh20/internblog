import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.db import init_db
from app.models import User


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.config import settings

    engine = create_engine(f"sqlite:///{tmp_path}/test.db", future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(main, "SessionLocal", Session)
    monkeypatch.setattr("app.db.engine", engine)
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    init_db()
    return Session


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def _login_as(client, session_factory, email):
    db = session_factory()
    user = User(google_sub=email, email=email)
    db.add(user)
    db.commit()
    session_id = auth.create_session(db, user.id)
    client.cookies.set(main.settings.session_cookie_name, session_id)
    return user


def test_root_requires_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_root_shows_calendar_view_when_logged_in(client, _fresh_db):
    _login_as(client, _fresh_db, "user@example.com")
    response = client.get("/")
    assert response.status_code == 200
    assert "user@example.com" in response.text


def test_admin_route_404s_for_non_owner(client, _fresh_db):
    _login_as(client, _fresh_db, "not-owner@example.com")
    response = client.get("/admin")
    assert response.status_code == 404


def test_admin_route_200s_for_owner(client, _fresh_db):
    _login_as(client, _fresh_db, "owner@example.com")
    response = client.get("/admin")
    assert response.status_code == 200


def test_settings_updates_sync_toggle(client, _fresh_db):
    _login_as(client, _fresh_db, "user2@example.com")
    response = client.post("/settings", data={}, follow_redirects=False)
    assert response.status_code == 303

    db = _fresh_db()
    updated = db.query(User).filter_by(email="user2@example.com").one()
    assert updated.calendar_sync_enabled is False  # unchecked checkbox omits the field entirely


def test_disconnect_telegram_clears_chat_id(client, _fresh_db):
    user = _login_as(client, _fresh_db, "user3@example.com")
    db = _fresh_db()
    db.query(User).filter_by(id=user.id).update({"telegram_chat_id": "12345"})
    db.commit()

    response = client.post("/settings/disconnect-telegram", follow_redirects=False)
    assert response.status_code == 303

    db2 = _fresh_db()
    updated = db2.query(User).filter_by(id=user.id).one()
    assert updated.telegram_chat_id is None


def test_home_page_generates_a_telegram_link_code_when_not_connected(client, _fresh_db):
    _login_as(client, _fresh_db, "user4@example.com")
    response = client.get("/")
    assert response.status_code == 200

    db = _fresh_db()
    updated = db.query(User).filter_by(email="user4@example.com").one()
    assert updated.telegram_link_code is not None


def test_cycle_route_404s_for_non_admin(client, _fresh_db, monkeypatch):
    monkeypatch.setattr(main, "cycle_job", lambda: None)
    _login_as(client, _fresh_db, "not-owner@example.com")
    response = client.post("/cycle")
    assert response.status_code == 404


def test_cycle_route_200s_for_admin(client, _fresh_db, monkeypatch):
    monkeypatch.setattr(main, "cycle_job", lambda: None)
    _login_as(client, _fresh_db, "owner@example.com")
    response = client.post("/cycle")
    assert response.status_code == 200
