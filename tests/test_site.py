from app.models import Post, User
from app.config import settings
from app.site import (
    render_access_denied_page,
    render_calendar_view,
    render_login_page,
    render_post_detail,
    render_privacy_page,
    render_terms_page,
)

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
    assert "<script>alert(1)</script>" not in html


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
            "post_id": 1, "category": "new_listing", "company": "Quantbox", "role": "Quant Researcher",
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
            "post_id": 2, "category": "new_listing", "company": "Acme", "role": None,
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


def _post(**overrides):
    defaults = dict(
        id=1, wp_id=1, slug="acme-sde-intern", title="Acme - SDE Intern",
        link="https://blog.example.com/acme-sde-intern", date_gmt="2026-07-17T10:00:00",
        modified_gmt="2026-07-17T10:00:00", content_hash="h", raw_html="<p>We are hiring.</p>",
    )
    defaults.update(overrides)
    return Post(**defaults)


def test_post_detail_shows_title_and_content():
    html = render_post_detail(_post())
    assert "Acme - SDE Intern" in html
    assert "We are hiring." in html


def test_post_detail_links_back_to_original_post():
    html = render_post_detail(_post())
    assert 'href="https://blog.example.com/acme-sde-intern"' in html


def test_post_detail_strips_script_tags():
    html = render_post_detail(_post(raw_html="<p>Hi</p><script>alert(1)</script>"))
    assert "<script>" not in html
    assert "alert(1)" not in html


def test_post_detail_strips_event_handler_attributes():
    html = render_post_detail(_post(raw_html='<p onclick="alert(1)">Hi</p>'))
    assert "onclick" not in html


def test_post_detail_preserves_allowed_formatting():
    html = render_post_detail(_post(raw_html='<p>Apply <a href="https://apply.example.com">here</a></p>'))
    assert '<a href="https://apply.example.com">here</a>' in html


def test_post_detail_preserves_shortlist_table_structure():
    raw_html = (
        "<table><thead><tr><th>Roll Number</th><th>Name</th></tr></thead>"
        "<tbody><tr><td>24B0001</td><td>Asha Test Student</td></tr></tbody></table>"
    )
    html = render_post_detail(_post(raw_html=raw_html))
    assert "<table>" in html
    assert "<tr><th>Roll Number</th><th>Name</th></tr>" in html
    assert "<tr><td>24B0001</td><td>Asha Test Student</td></tr>" in html


def test_policy_pages_show_configured_owner_email_as_contact(monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    for page in (render_privacy_page(), render_terms_page(), render_access_denied_page()):
        assert "owner@example.com" in page
