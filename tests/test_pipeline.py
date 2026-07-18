from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import pipeline
from app.models import Base, Extraction, TelegramNotification, User
from app.pipeline import _is_upcoming


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

    user = User(google_sub="tp1", email="tp1@example.com", telegram_chat_id="1")
    db.add(user)
    db.commit()

    extraction = Extraction(company="Acme", category="new_listing")
    pipeline.send_or_edit_telegram_for_user(db, user, extraction, "hello")

    assert len(sent) == 1
    stored = db.query(TelegramNotification).one()
    assert stored.group_key == f"{user.id}|acme|deadline"
    assert stored.message_id == 123


def test_followup_in_same_group_edits_existing_message(db, monkeypatch):
    user = User(google_sub="tp2", email="tp2@example.com", telegram_chat_id="1")
    db.add(user)
    db.commit()
    db.add(TelegramNotification(group_key=f"{user.id}|acme|deadline", message_id=999))
    db.commit()

    edited = []
    monkeypatch.setattr(pipeline, "edit_telegram_message", lambda token, chat, mid, text: (edited.append(mid) or True))
    monkeypatch.setattr(pipeline, "send_telegram_message", lambda *a: (_ for _ in ()).throw(AssertionError("should not send new")))

    extraction = Extraction(company="Acme", category="deadline_extension")
    pipeline.send_or_edit_telegram_for_user(db, user, extraction, "updated")

    assert edited == [999]


def test_edit_failure_falls_back_to_new_message(db, monkeypatch):
    user = User(google_sub="tp3", email="tp3@example.com", telegram_chat_id="1")
    db.add(user)
    db.commit()
    db.add(TelegramNotification(group_key=f"{user.id}|acme|deadline", message_id=999))
    db.commit()

    monkeypatch.setattr(pipeline, "edit_telegram_message", lambda *a: False)
    monkeypatch.setattr(pipeline, "send_telegram_message", lambda *a: 456)

    extraction = Extraction(company="Acme", category="deadline_extension")
    pipeline.send_or_edit_telegram_for_user(db, user, extraction, "updated")

    stored = db.query(TelegramNotification).one()
    assert stored.message_id == 456


def test_company_less_post_always_sends_new_without_grouping(db, monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "send_telegram_message", lambda *a: (sent.append(a) or 1))
    monkeypatch.setattr(pipeline, "edit_telegram_message", lambda *a: (_ for _ in ()).throw(AssertionError("should not edit")))

    user = User(google_sub="tp4", email="tp4@example.com", telegram_chat_id="1")
    db.add(user)
    db.commit()

    extraction = Extraction(company=None, category="test_update")
    pipeline.send_or_edit_telegram_for_user(db, user, extraction, "mock test reminder")

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


def test_calendar_push_only_reaches_sync_enabled_users_with_a_refresh_token(db, monkeypatch):
    pushed = []
    monkeypatch.setattr(pipeline, "push_calendar_event_for_user", lambda user, extraction, row: pushed.append(user.email))

    synced = User(google_sub="s1", email="synced@example.com", calendar_sync_enabled=True, calendar_refresh_token_encrypted="ct")
    not_synced = User(google_sub="s2", email="off@example.com", calendar_sync_enabled=False, calendar_refresh_token_encrypted="ct")
    no_token = User(google_sub="s3", email="notoken@example.com", calendar_sync_enabled=True, calendar_refresh_token_encrypted=None)
    db.add_all([synced, not_synced, no_token])
    db.commit()

    extraction = Extraction(company="Acme", category="new_listing", deadline="2027-01-01T00:00:00+05:30")
    from app.models import Post

    post = Post(wp_id=1, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    pipeline.push_to_all_users(db, extraction, post)

    assert pushed == ["synced@example.com"]


def test_telegram_push_only_reaches_users_with_a_chat_id(db, monkeypatch):
    sent_to = []
    monkeypatch.setattr(
        pipeline, "send_or_edit_telegram_for_user",
        lambda db, user, extraction, message: sent_to.append(user.email),
    )
    monkeypatch.setattr(pipeline, "push_calendar_event_for_user", lambda *a: None)

    with_chat = User(google_sub="s4", email="chat@example.com", telegram_chat_id="111")
    without_chat = User(google_sub="s5", email="nochat@example.com", telegram_chat_id=None)
    db.add_all([with_chat, without_chat])
    db.commit()

    extraction = Extraction(company="Acme", category="new_listing", deadline="2027-01-01T00:00:00+05:30")
    from app.models import Post

    post = Post(wp_id=2, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    pipeline.push_to_all_users(db, extraction, post)

    assert sent_to == ["chat@example.com"]


def test_shortlist_result_never_reaches_calendar_push_but_still_sends_telegram(db, monkeypatch):
    """shortlist_result is in NOTIFY_CATEGORIES but deliberately excluded
    from CALENDAR_CATEGORIES (nothing to attend/act on by a date) - it must
    never reach push_calendar_event_for_user, even for an opted-in user,
    while telegram push still happens since it's a valid NOTIFY category."""
    calendar_pushed = []
    telegram_sent = []
    monkeypatch.setattr(
        pipeline, "push_calendar_event_for_user",
        lambda user, extraction, row: calendar_pushed.append(user.email),
    )
    monkeypatch.setattr(
        pipeline, "send_or_edit_telegram_for_user",
        lambda db, user, extraction, message: telegram_sent.append(user.email),
    )

    user = User(
        google_sub="s6", email="optedin@example.com", telegram_chat_id="222",
        calendar_sync_enabled=True, calendar_refresh_token_encrypted="ct",
    )
    db.add(user)
    db.commit()

    extraction = Extraction(company="Acme", category="shortlist_result", deadline=None)
    from app.models import Post

    post = Post(wp_id=3, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    pipeline.push_to_all_users(db, extraction, post)

    assert calendar_pushed == []
    assert telegram_sent == ["optedin@example.com"]


def test_one_users_push_failure_does_not_block_another_users_push(db, monkeypatch):
    def flaky_calendar_push(user, extraction, row):
        if user.email == "broken@example.com":
            raise RuntimeError("revoked token")

    pushed_ok = []
    monkeypatch.setattr(pipeline, "push_calendar_event_for_user", lambda user, extraction, row: (
        flaky_calendar_push(user, extraction, row) or pushed_ok.append(user.email)
    ))

    broken = User(google_sub="s6", email="broken@example.com", calendar_sync_enabled=True, calendar_refresh_token_encrypted="ct")
    healthy = User(google_sub="s7", email="healthy@example.com", calendar_sync_enabled=True, calendar_refresh_token_encrypted="ct")
    db.add_all([broken, healthy])
    db.commit()

    extraction = Extraction(company="Acme", category="new_listing", deadline="2027-01-01T00:00:00+05:30")
    from app.models import Post

    post = Post(wp_id=3, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    pipeline.push_to_all_users(db, extraction, post)

    assert pushed_ok == ["healthy@example.com"]


def test_send_or_edit_telegram_for_user_scopes_group_key_by_user(db, monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "send_telegram_message", lambda *a: (sent.append(a) or 42))
    monkeypatch.setattr(pipeline, "edit_telegram_message", lambda *a: (_ for _ in ()).throw(AssertionError("should not edit")))

    user = User(google_sub="s8", email="u@example.com", telegram_chat_id="999")
    db.add(user)
    db.commit()
    extraction = Extraction(company="Acme", category="new_listing")

    pipeline.send_or_edit_telegram_for_user(db, user, extraction, "hi")

    stored = db.query(TelegramNotification).one()
    assert str(user.id) in stored.group_key
