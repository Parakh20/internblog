from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import pipeline
from app.models import Base, Extraction, TelegramNotification
from app.pipeline import _is_upcoming, send_or_edit_telegram


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_first_message_for_a_company_group_is_sent_not_edited(db, monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "send_telegram_message", lambda *a: (sent.append(a) or 123))
    monkeypatch.setattr(pipeline, "edit_telegram_message", lambda *a: (_ for _ in ()).throw(AssertionError("should not edit")))

    extraction = Extraction(company="Acme", category="new_listing")
    send_or_edit_telegram(db, extraction, "hello")

    assert len(sent) == 1
    stored = db.query(TelegramNotification).one()
    assert stored.group_key == "acme|deadline"
    assert stored.message_id == 123


def test_followup_in_same_group_edits_existing_message(db, monkeypatch):
    db.add(TelegramNotification(group_key="acme|deadline", message_id=999))
    db.commit()

    edited = []
    monkeypatch.setattr(pipeline, "edit_telegram_message", lambda token, chat, mid, text: (edited.append(mid) or True))
    monkeypatch.setattr(pipeline, "send_telegram_message", lambda *a: (_ for _ in ()).throw(AssertionError("should not send new")))

    extraction = Extraction(company="Acme", category="deadline_extension")
    send_or_edit_telegram(db, extraction, "updated")

    assert edited == [999]


def test_edit_failure_falls_back_to_new_message(db, monkeypatch):
    db.add(TelegramNotification(group_key="acme|deadline", message_id=999))
    db.commit()

    monkeypatch.setattr(pipeline, "edit_telegram_message", lambda *a: False)
    monkeypatch.setattr(pipeline, "send_telegram_message", lambda *a: 456)

    extraction = Extraction(company="Acme", category="deadline_extension")
    send_or_edit_telegram(db, extraction, "updated")

    stored = db.query(TelegramNotification).one()
    assert stored.message_id == 456


def test_company_less_post_always_sends_new_without_grouping(db, monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "send_telegram_message", lambda *a: (sent.append(a) or 1))
    monkeypatch.setattr(pipeline, "edit_telegram_message", lambda *a: (_ for _ in ()).throw(AssertionError("should not edit")))

    extraction = Extraction(company=None, category="test_update")
    send_or_edit_telegram(db, extraction, "mock test reminder")

    assert len(sent) == 1
    assert db.query(TelegramNotification).count() == 0


def test_future_ist_naive_deadline_is_upcoming():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert _is_upcoming(future)


def test_past_deadline_is_not_upcoming():
    past = "2020-01-01T00:00:00+05:30"
    assert not _is_upcoming(past)


def test_unparseable_deadline_is_not_upcoming():
    assert not _is_upcoming("not a date")
