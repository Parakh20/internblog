import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.config import settings
from app.db import init_db
from app.models import User


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path}/test.db", future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(main, "SessionLocal", Session)
    monkeypatch.setattr("app.db.engine", engine)
    monkeypatch.setattr(settings, "telegram_webhook_secret", "test-secret")
    init_db()
    return Session


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def _make_user(db_factory, **kwargs) -> User:
    db = db_factory()
    user = User(google_sub=kwargs.pop("google_sub", "sub-1"), email=kwargs.pop("email", "u@example.com"), **kwargs)
    db.add(user)
    db.commit()
    return user


def test_webhook_rejects_missing_secret_header(client, db_factory):
    response = client.post("/telegram/webhook", json={"message": {"chat": {"id": 1}, "text": "/start x"}})
    assert response.status_code == 403


def test_webhook_rejects_wrong_secret_header(client, db_factory):
    response = client.post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": 1}, "text": "/start x"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert response.status_code == 403


def test_webhook_connects_chat_id_for_matching_code(client, db_factory, monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_telegram_message", lambda *a: (sent.append(a) or 1))
    user = _make_user(db_factory, telegram_link_code="abc123")

    response = client.post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": 999888}, "text": "/start abc123"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
    )

    assert response.status_code == 200
    db = db_factory()
    updated = db.query(User).filter_by(id=user.id).one()
    assert updated.telegram_chat_id == "999888"
    assert updated.telegram_link_code is None
    assert len(sent) == 1


def test_webhook_is_a_noop_for_unknown_code(client, db_factory, monkeypatch):
    monkeypatch.setattr(main, "send_telegram_message", lambda *a: 1)
    _make_user(db_factory, telegram_link_code="abc123")

    response = client.post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": 1}, "text": "/start nonexistent"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
    )
    assert response.status_code == 200
    db = db_factory()
    unchanged = db.query(User).filter_by(telegram_link_code="abc123").one()
    assert unchanged.telegram_chat_id is None


def test_webhook_is_a_noop_for_non_start_messages(client, db_factory, monkeypatch):
    monkeypatch.setattr(main, "send_telegram_message", lambda *a: 1)
    response = client.post(
        "/telegram/webhook",
        json={"message": {"chat": {"id": 1}, "text": "hello"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
    )
    assert response.status_code == 200
