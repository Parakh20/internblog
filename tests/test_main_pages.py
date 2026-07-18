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


def test_root_shows_public_homepage_when_not_logged_in(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "internship" in response.text.lower()
    assert "calendar" in response.text.lower()
    assert "/auth/start" in response.text


def test_privacy_and_terms_pages_are_public(client):
    assert client.get("/privacy").status_code == 200
    assert client.get("/terms").status_code == 200


def test_google_site_verification_file_is_public_and_unmodified(client):
    response = client.get("/google162e56c4a13e2140.html")
    assert response.status_code == 200
    assert response.text == "google-site-verification: google162e56c4a13e2140.html"


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


def test_admin_dashboard_lists_registered_users(client, _fresh_db):
    _login_as(client, _fresh_db, "owner@example.com")
    db = _fresh_db()
    db.add(User(google_sub="listed", email="listed-user@example.com", name="Listed User"))
    db.commit()

    response = client.get("/admin")
    assert response.status_code == 200
    assert "listed-user@example.com" in response.text
    assert "Listed User" in response.text


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


def test_post_detail_requires_login(client, _fresh_db):
    from app.models import Post

    db = _fresh_db()
    db.add(Post(
        wp_id=1, slug="a", title="Acme - SDE Intern", link="https://blog.example.com/a",
        date_gmt="2026-07-17T10:00:00", modified_gmt="2026-07-17T10:00:00",
        content_hash="h", raw_html="<p>Hi</p>",
    ))
    db.commit()

    response = client.get("/posts/1", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_post_detail_shows_full_content_when_logged_in(client, _fresh_db):
    from app.models import Post

    _login_as(client, _fresh_db, "reader@example.com")
    db = _fresh_db()
    db.add(Post(
        wp_id=2, slug="b", title="Acme - SDE Intern", link="https://blog.example.com/b",
        date_gmt="2026-07-17T10:00:00", modified_gmt="2026-07-17T10:00:00",
        content_hash="h", raw_html="<p>We are hiring.</p>",
    ))
    db.commit()
    post_id = db.query(Post).filter_by(wp_id=2).one().id

    response = client.get(f"/posts/{post_id}")
    assert response.status_code == 200
    assert "We are hiring." in response.text


def test_post_detail_404s_for_unknown_post(client, _fresh_db):
    _login_as(client, _fresh_db, "reader2@example.com")
    response = client.get("/posts/999999")
    assert response.status_code == 404
