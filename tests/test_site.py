from app.models import User
from app.site import render_calendar_view, render_login_page

_EMPTY_STATUS = {"last_fetch": {"ts": None}, "poll_interval_minutes": 2}


def _render(user, **overrides):
    kwargs = dict(
        upcoming=[], recent=[], total_extractions=0, status=_EMPTY_STATUS,
        connect_url=None, is_admin=False,
    )
    kwargs.update(overrides)
    return render_calendar_view(user, **kwargs)


def test_login_page_has_sign_in_button():
    html = render_login_page()
    assert "Sign in with Google" in html
    assert "/auth/start" in html


def test_calendar_view_shows_user_email_and_upcoming_events():
    user = User(email="someone@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = _render(
        user,
        upcoming=[{"category": "new_listing", "company": "Acme", "role": "SWE Intern", "deadline": "2027-01-01T00:00:00+05:30", "link": "https://x"}],
    )
    assert "someone@example.com" in html
    assert "Acme" in html
    assert "Admin" not in html


def test_calendar_view_shows_admin_link_for_admin_user():
    user = User(email="sharmaparakh05@gmail.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = _render(user, is_admin=True)
    assert "/admin" in html


def test_calendar_view_escapes_html_in_company_name():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = _render(
        user,
        upcoming=[{"category": "new_listing", "company": "<script>alert(1)</script>", "role": None, "deadline": None, "link": "https://x"}],
    )
    assert "<script>" not in html


def test_calendar_view_does_not_render_javascript_uri_as_clickable_link():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = _render(
        user,
        upcoming=[{
            "category": "new_listing", "company": "Acme", "role": None,
            "deadline": None, "link": "javascript:alert(1)",
        }],
    )
    assert '<a href="javascript:alert(1)"' not in html
    assert "javascript:alert(1)" in html  # rendered inert as escaped text


def test_calendar_view_shows_total_extractions_and_poll_interval_for_every_user():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = _render(
        user,
        total_extractions=42,
        status={"last_fetch": {"ts": "2026-07-18T09:58:22+00:00"}, "poll_interval_minutes": 2},
    )
    assert "42" in html
    assert "2m" in html


def test_calendar_view_lists_full_database_for_every_user():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = _render(
        user,
        recent=[{
            "category": "new_listing", "company": "Quantbox", "role": "Quant Researcher",
            "deadline": "2026-07-19T14:00:00+05:30", "link": "https://x", "posted_at": "2026-07-17T10:00:00",
            "created_at": "2026-07-17T10:05:00+00:00",
        }],
        total_extractions=1,
    )
    assert "Quantbox" in html
    assert "Quant Researcher" in html


def test_calendar_view_database_link_blocks_unsafe_scheme():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = _render(
        user,
        recent=[{
            "category": "new_listing", "company": "Acme", "role": None,
            "deadline": None, "link": "javascript:alert(1)", "posted_at": None, "created_at": None,
        }],
        total_extractions=1,
    )
    assert '<a href="javascript:alert(1)"' not in html


def test_calendar_view_shows_connect_link_when_not_connected():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = _render(user, connect_url="https://t.me/Internblog_bot?start=abc123")
    assert 'href="https://t.me/Internblog_bot?start=abc123"' in html
    assert "Connect Telegram" in html
    assert "Disconnect Telegram" not in html


def test_calendar_view_shows_disconnect_when_connected():
    user = User(email="u@example.com", telegram_chat_id="999", calendar_sync_enabled=True)
    html = _render(user, connect_url="https://t.me/Internblog_bot?start=abc123")
    assert "Disconnect Telegram" in html
    assert "Connect Telegram" not in html
    # A connected user's connect_url is stale/consumed - must not appear even if passed in.
    assert "start=abc123" not in html


def test_calendar_view_has_no_manual_chat_id_input():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = _render(user)
    assert 'name="telegram_chat_id"' not in html
