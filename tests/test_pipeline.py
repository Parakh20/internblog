from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import pipeline
from app.config import settings
from app.models import Base, Extraction, TelegramNotification, User
from app.pipeline import _is_upcoming


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings, "secret_encryption_key", Fernet.generate_key().decode())


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
    monkeypatch.setattr(pipeline, "push_calendar_event_for_user", lambda db, user, extraction, row: pushed.append(user.email))

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
        lambda db, user, extraction, row: calendar_pushed.append(user.email),
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
    def flaky_calendar_push(db, user, extraction, row):
        if user.email == "broken@example.com":
            raise RuntimeError("revoked token")

    pushed_ok = []
    monkeypatch.setattr(pipeline, "push_calendar_event_for_user", lambda db, user, extraction, row: (
        flaky_calendar_push(db, user, extraction, row) or pushed_ok.append(user.email)
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


def test_get_or_create_event_calendar_creates_and_persists_on_first_call(db, monkeypatch):
    monkeypatch.setattr(pipeline, "create_secondary_calendar", lambda access_token, summary: "new-evt-cal")

    user = User(google_sub="evt1", email="evt1@example.com")
    db.add(user)
    db.commit()

    result = pipeline.get_or_create_event_calendar(db, user, "access-token")

    assert result == "new-evt-cal"
    assert user.event_calendar_id == "new-evt-cal"
    fetched = db.query(User).filter_by(id=user.id).one()
    assert fetched.event_calendar_id == "new-evt-cal"


def test_get_or_create_event_calendar_reuses_existing(db, monkeypatch):
    monkeypatch.setattr(
        pipeline, "create_secondary_calendar",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not create again")),
    )

    user = User(google_sub="evt2", email="evt2@example.com", event_calendar_id="existing-cal")
    db.add(user)
    db.commit()

    result = pipeline.get_or_create_event_calendar(db, user, "access-token")

    assert result == "existing-cal"


def test_get_or_create_event_calendar_returns_none_on_creation_failure(db, monkeypatch):
    monkeypatch.setattr(pipeline, "create_secondary_calendar", lambda access_token, summary: None)

    user = User(google_sub="evt3", email="evt3@example.com")
    db.add(user)
    db.commit()

    assert pipeline.get_or_create_event_calendar(db, user, "access-token") is None


def test_push_calendar_event_for_user_routes_deadline_category_to_calendar_id(db, monkeypatch):
    monkeypatch.setattr(pipeline, "decrypt_token", lambda ciphertext: ciphertext)
    monkeypatch.setattr(pipeline, "get_access_token", lambda *a: "access-token")
    upserted = []
    monkeypatch.setattr(
        pipeline, "upsert_event",
        lambda access_token, calendar_id, event_id, summary, description, start_iso, end_iso: upserted.append(calendar_id),
    )

    user = User(
        google_sub="route1", email="route1@example.com",
        calendar_id="deadline-cal", calendar_refresh_token_encrypted="ct",
    )
    db.add(user)
    db.commit()

    from app.models import Post

    post = Post(wp_id=10, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    extraction = Extraction(company="Acme", category="new_listing", deadline="2027-01-01T00:00:00+05:30")

    pipeline.push_calendar_event_for_user(db, user, extraction, post)

    assert upserted == ["deadline-cal"]


def test_push_calendar_event_for_user_routes_event_category_to_event_calendar_id(db, monkeypatch):
    monkeypatch.setattr(pipeline, "decrypt_token", lambda ciphertext: ciphertext)
    monkeypatch.setattr(pipeline, "get_access_token", lambda *a: "access-token")
    upserted = []
    monkeypatch.setattr(
        pipeline, "upsert_event",
        lambda access_token, calendar_id, event_id, summary, description, start_iso, end_iso: upserted.append(calendar_id),
    )

    user = User(
        google_sub="route2", email="route2@example.com",
        calendar_id="deadline-cal", event_calendar_id="events-cal", calendar_refresh_token_encrypted="ct",
    )
    db.add(user)
    db.commit()

    from app.models import Post

    post = Post(wp_id=11, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    extraction = Extraction(company="Acme", category="test_update", deadline="2027-01-01T00:00:00+05:30")

    pipeline.push_calendar_event_for_user(db, user, extraction, post)

    assert upserted == ["events-cal"]


def test_push_calendar_event_for_user_lazily_creates_event_calendar(db, monkeypatch):
    monkeypatch.setattr(pipeline, "decrypt_token", lambda ciphertext: ciphertext)
    monkeypatch.setattr(pipeline, "get_access_token", lambda *a: "access-token")
    monkeypatch.setattr(pipeline, "create_secondary_calendar", lambda access_token, summary: "freshly-created-cal")
    upserted = []
    monkeypatch.setattr(
        pipeline, "upsert_event",
        lambda access_token, calendar_id, event_id, summary, description, start_iso, end_iso: upserted.append(calendar_id),
    )

    user = User(
        google_sub="route3", email="route3@example.com",
        calendar_id="deadline-cal", calendar_refresh_token_encrypted="ct",
    )
    db.add(user)
    db.commit()

    from app.models import Post

    post = Post(wp_id=12, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    extraction = Extraction(company="Acme", category="ppt", deadline="2027-01-01T00:00:00+05:30")

    pipeline.push_calendar_event_for_user(db, user, extraction, post)

    assert upserted == ["freshly-created-cal"]
    assert user.event_calendar_id == "freshly-created-cal"


def test_get_or_create_deadline_calendar_creates_and_persists_on_first_call(db, monkeypatch):
    monkeypatch.setattr(pipeline, "create_secondary_calendar", lambda access_token, summary: "new-deadline-cal")

    user = User(google_sub="ddl1", email="ddl1@example.com")
    db.add(user)
    db.commit()

    result = pipeline.get_or_create_deadline_calendar(db, user, "access-token")

    assert result == "new-deadline-cal"
    fetched = db.query(User).filter_by(id=user.id).one()
    assert fetched.calendar_id == "new-deadline-cal"


def test_get_or_create_deadline_calendar_reuses_existing(db, monkeypatch):
    monkeypatch.setattr(
        pipeline, "create_secondary_calendar",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not create again")),
    )

    user = User(google_sub="ddl2", email="ddl2@example.com", calendar_id="existing-deadline-cal")
    db.add(user)
    db.commit()

    assert pipeline.get_or_create_deadline_calendar(db, user, "access-token") == "existing-deadline-cal"


def test_get_or_create_deadline_calendar_returns_none_on_creation_failure(db, monkeypatch):
    monkeypatch.setattr(pipeline, "create_secondary_calendar", lambda access_token, summary: None)

    user = User(google_sub="ddl3", email="ddl3@example.com")
    db.add(user)
    db.commit()

    assert pipeline.get_or_create_deadline_calendar(db, user, "access-token") is None


def test_push_calendar_event_for_user_self_heals_deleted_deadline_calendar(db, monkeypatch):
    """A calendar that was deleted on Google's side (or was never valid)
    404s on the first push - the fix must clear the stale id, create a
    fresh calendar, and retry the same event against it, instead of
    silently failing forever like the pre-fix behavior did in production."""
    monkeypatch.setattr(pipeline, "decrypt_token", lambda ciphertext: ciphertext)
    monkeypatch.setattr(pipeline, "get_access_token", lambda *a: "access-token")
    monkeypatch.setattr(pipeline, "create_secondary_calendar", lambda access_token, summary: "recreated-cal")

    calls = []

    def fake_upsert(access_token, calendar_id, event_id, summary, description, start_iso, end_iso):
        calls.append(calendar_id)
        return "calendar_missing" if calendar_id == "stale-cal" else "ok"

    monkeypatch.setattr(pipeline, "upsert_event", fake_upsert)

    user = User(
        google_sub="heal1", email="heal1@example.com",
        calendar_id="stale-cal", calendar_refresh_token_encrypted="ct",
    )
    db.add(user)
    db.commit()

    from app.models import Post

    post = Post(wp_id=20, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    extraction = Extraction(company="Acme", category="new_listing", deadline="2027-01-01T00:00:00+05:30")

    pipeline.push_calendar_event_for_user(db, user, extraction, post)

    assert calls == ["stale-cal", "recreated-cal"]
    fetched = db.query(User).filter_by(id=user.id).one()
    assert fetched.calendar_id == "recreated-cal"


def test_push_calendar_event_for_user_self_heals_deleted_event_calendar(db, monkeypatch):
    monkeypatch.setattr(pipeline, "decrypt_token", lambda ciphertext: ciphertext)
    monkeypatch.setattr(pipeline, "get_access_token", lambda *a: "access-token")
    monkeypatch.setattr(pipeline, "create_secondary_calendar", lambda access_token, summary: "recreated-evt-cal")

    calls = []

    def fake_upsert(access_token, calendar_id, event_id, summary, description, start_iso, end_iso):
        calls.append(calendar_id)
        return "calendar_missing" if calendar_id == "stale-evt-cal" else "ok"

    monkeypatch.setattr(pipeline, "upsert_event", fake_upsert)

    user = User(
        google_sub="heal2", email="heal2@example.com",
        event_calendar_id="stale-evt-cal", calendar_refresh_token_encrypted="ct",
    )
    db.add(user)
    db.commit()

    from app.models import Post

    post = Post(wp_id=21, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    extraction = Extraction(company="Acme", category="test_update", deadline="2027-01-01T00:00:00+05:30")

    pipeline.push_calendar_event_for_user(db, user, extraction, post)

    assert calls == ["stale-evt-cal", "recreated-evt-cal"]
    fetched = db.query(User).filter_by(id=user.id).one()
    assert fetched.event_calendar_id == "recreated-evt-cal"


def test_push_calendar_event_for_user_gives_event_category_a_default_duration_when_none_stated(db, monkeypatch):
    monkeypatch.setattr(pipeline, "decrypt_token", lambda ciphertext: ciphertext)
    monkeypatch.setattr(pipeline, "get_access_token", lambda *a: "access-token")
    ends = []
    monkeypatch.setattr(
        pipeline, "upsert_event",
        lambda access_token, calendar_id, event_id, summary, description, start_iso, end_iso: (
            ends.append(end_iso) or "ok"
        ),
    )

    user = User(
        google_sub="dur1", email="dur1@example.com",
        event_calendar_id="events-cal", calendar_refresh_token_encrypted="ct",
    )
    db.add(user)
    db.commit()

    from app.models import Post

    post = Post(wp_id=22, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    extraction = Extraction(
        company="Acme", category="test_update",
        deadline="2027-01-01T10:00:00+05:30", deadline_end=None,
    )

    pipeline.push_calendar_event_for_user(db, user, extraction, post)

    assert ends == ["2027-01-01T11:00:00+05:30"]


def test_push_calendar_event_for_user_keeps_stated_duration_for_event_category(db, monkeypatch):
    monkeypatch.setattr(pipeline, "decrypt_token", lambda ciphertext: ciphertext)
    monkeypatch.setattr(pipeline, "get_access_token", lambda *a: "access-token")
    ends = []
    monkeypatch.setattr(
        pipeline, "upsert_event",
        lambda access_token, calendar_id, event_id, summary, description, start_iso, end_iso: (
            ends.append(end_iso) or "ok"
        ),
    )

    user = User(
        google_sub="dur2", email="dur2@example.com",
        event_calendar_id="events-cal", calendar_refresh_token_encrypted="ct",
    )
    db.add(user)
    db.commit()

    from app.models import Post

    post = Post(wp_id=23, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    extraction = Extraction(
        company="Acme", category="test_update",
        deadline="2027-01-01T10:00:00+05:30", deadline_end="2027-01-01T12:00:00+05:30",
    )

    pipeline.push_calendar_event_for_user(db, user, extraction, post)

    assert ends == ["2027-01-01T12:00:00+05:30"]


def test_push_calendar_event_for_user_leaves_deadline_category_as_point_in_time(db, monkeypatch):
    # Deadlines are genuinely instantaneous - no default duration should be
    # applied, unlike test/OA/PPT events.
    monkeypatch.setattr(pipeline, "decrypt_token", lambda ciphertext: ciphertext)
    monkeypatch.setattr(pipeline, "get_access_token", lambda *a: "access-token")
    ends = []
    monkeypatch.setattr(
        pipeline, "upsert_event",
        lambda access_token, calendar_id, event_id, summary, description, start_iso, end_iso: (
            ends.append(end_iso) or "ok"
        ),
    )

    user = User(
        google_sub="dur3", email="dur3@example.com",
        calendar_id="deadline-cal", calendar_refresh_token_encrypted="ct",
    )
    db.add(user)
    db.commit()

    from app.models import Post

    post = Post(wp_id=24, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    extraction = Extraction(
        company="Acme", category="new_listing",
        deadline="2027-01-01T23:59:00+05:30", deadline_end=None,
    )

    pipeline.push_calendar_event_for_user(db, user, extraction, post)

    assert ends == [None]


def test_apply_changes_keeps_earlier_posts_when_a_later_one_crashes(db, monkeypatch):
    # Arrange: second post's extraction blows up mid-cycle.
    from app.change_detection import ChangeSet
    from app.models import Post

    monkeypatch.setattr(pipeline, "annotate_roll_departments", lambda html, attempts=None: html)
    monkeypatch.setattr(pipeline, "build_extraction_attempts", lambda: [])

    def extraction(db_, row):
        if row.wp_id == 2:
            raise RuntimeError("crash")

    monkeypatch.setattr(pipeline, "run_extraction", extraction)
    posts = [
        {"id": 1, "title": {"rendered": "one"}, "content": {"rendered": "a"}, "modified_gmt": "2026-09-01T00:00:00"},
        {"id": 2, "title": {"rendered": "two"}, "content": {"rendered": "b"}, "modified_gmt": "2026-09-01T00:00:00"},
    ]

    # Act
    with pytest.raises(RuntimeError):
        pipeline.apply_changes(db, ChangeSet(new=posts))
    db.rollback()

    # Assert
    assert [p.wp_id for p in db.query(Post).all()] == [1]
