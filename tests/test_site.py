from app.models import User
from app.site import render_calendar_view, render_login_page

_EMPTY_STATUS = {"last_fetch": {"ts": None}, "poll_interval_minutes": 2}


def test_login_page_has_sign_in_button():
    html = render_login_page()
    assert "Sign in with Google" in html
    assert "/auth/start" in html


def test_calendar_view_shows_user_email_and_upcoming_events():
    user = User(email="someone@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = render_calendar_view(
        user,
        upcoming=[{"category": "new_listing", "company": "Acme", "role": "SWE Intern", "deadline": "2027-01-01T00:00:00+05:30", "link": "https://x"}],
        recent=[],
        total_extractions=0,
        status=_EMPTY_STATUS,
        is_admin=False,
    )
    assert "someone@example.com" in html
    assert "Acme" in html
    assert "Admin" not in html


def test_calendar_view_shows_admin_link_for_admin_user():
    user = User(email="sharmaparakh05@gmail.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = render_calendar_view(
        user, upcoming=[], recent=[], total_extractions=0, status=_EMPTY_STATUS, is_admin=True
    )
    assert "/admin" in html


def test_calendar_view_escapes_html_in_company_name():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = render_calendar_view(
        user,
        upcoming=[{"category": "new_listing", "company": "<script>alert(1)</script>", "role": None, "deadline": None, "link": "https://x"}],
        recent=[],
        total_extractions=0,
        status=_EMPTY_STATUS,
        is_admin=False,
    )
    assert "<script>" not in html


def test_calendar_view_does_not_render_javascript_uri_as_clickable_link():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = render_calendar_view(
        user,
        upcoming=[{
            "category": "new_listing", "company": "Acme", "role": None,
            "deadline": None, "link": "javascript:alert(1)",
        }],
        recent=[],
        total_extractions=0,
        status=_EMPTY_STATUS,
        is_admin=False,
    )
    assert '<a href="javascript:alert(1)"' not in html
    assert "javascript:alert(1)" in html  # rendered inert as escaped text


def test_calendar_view_shows_total_extractions_and_poll_interval_for_every_user():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = render_calendar_view(
        user,
        upcoming=[],
        recent=[],
        total_extractions=42,
        status={"last_fetch": {"ts": "2026-07-18T09:58:22+00:00"}, "poll_interval_minutes": 2},
        is_admin=False,
    )
    assert "42" in html
    assert "2m" in html


def test_calendar_view_lists_full_database_for_every_user():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = render_calendar_view(
        user,
        upcoming=[],
        recent=[{
            "category": "new_listing", "company": "Quantbox", "role": "Quant Researcher",
            "deadline": "2026-07-19T14:00:00+05:30", "link": "https://x", "posted_at": "2026-07-17T10:00:00",
            "created_at": "2026-07-17T10:05:00+00:00",
        }],
        total_extractions=1,
        status=_EMPTY_STATUS,
        is_admin=False,
    )
    assert "Quantbox" in html
    assert "Quant Researcher" in html


def test_calendar_view_database_link_blocks_unsafe_scheme():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = render_calendar_view(
        user,
        upcoming=[],
        recent=[{
            "category": "new_listing", "company": "Acme", "role": None,
            "deadline": None, "link": "javascript:alert(1)", "posted_at": None, "created_at": None,
        }],
        total_extractions=1,
        status=_EMPTY_STATUS,
        is_admin=False,
    )
    assert '<a href="javascript:alert(1)"' not in html
