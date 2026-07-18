from app.models import User
from app.site import render_calendar_view, render_login_page


def test_login_page_has_sign_in_button():
    html = render_login_page()
    assert "Sign in with Google" in html
    assert "/auth/start" in html


def test_calendar_view_shows_user_email_and_upcoming_events():
    user = User(email="someone@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = render_calendar_view(
        user,
        upcoming=[{"category": "new_listing", "company": "Acme", "role": "SWE Intern", "deadline": "2027-01-01T00:00:00+05:30", "link": "https://x"}],
        is_admin=False,
    )
    assert "someone@example.com" in html
    assert "Acme" in html
    assert "Admin" not in html


def test_calendar_view_shows_admin_link_for_admin_user():
    user = User(email="sharmaparakh05@gmail.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = render_calendar_view(user, upcoming=[], is_admin=True)
    assert "/admin" in html


def test_calendar_view_escapes_html_in_company_name():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = render_calendar_view(
        user,
        upcoming=[{"category": "new_listing", "company": "<script>alert(1)</script>", "role": None, "deadline": None, "link": "https://x"}],
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
        is_admin=False,
    )
    assert '<a href="javascript:alert(1)"' not in html
    assert "javascript:alert(1)" in html  # rendered inert as escaped text
