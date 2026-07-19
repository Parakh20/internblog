from datetime import date, datetime

from app.calendar_view import parse_week_param, render_week_view, week_start
from app.timeutil import IST


def test_week_start_returns_monday_of_the_week():
    # 2026-07-22 is a Wednesday
    assert week_start(date(2026, 7, 22)) == date(2026, 7, 20)


def test_week_start_is_idempotent_on_a_monday():
    assert week_start(date(2026, 7, 20)) == date(2026, 7, 20)


def test_parse_week_param_parses_valid_date_to_its_monday():
    assert parse_week_param("2026-07-22") == date(2026, 7, 20)


def test_parse_week_param_defaults_to_current_week_when_missing():
    assert parse_week_param(None) == week_start(datetime.now(IST).date())


def test_parse_week_param_defaults_to_current_week_when_unparseable():
    assert parse_week_param("not-a-date") == week_start(datetime.now(IST).date())


def _event(**overrides):
    defaults = dict(
        post_id=1, category="new_listing", company="Acme", role="SWE Intern",
        deadline_dt=datetime(2026, 7, 21, 18, 30, tzinfo=IST),
    )
    defaults.update(overrides)
    return defaults


def test_render_week_view_places_event_under_its_day():
    html = render_week_view(date(2026, 7, 20), [_event()])
    assert "Tue 21" in html
    assert "Acme" in html
    assert "SWE Intern" in html


def test_render_week_view_shows_time_label():
    html = render_week_view(date(2026, 7, 20), [_event(deadline_dt=datetime(2026, 7, 21, 18, 30, tzinfo=IST))])
    assert "6:30 PM" in html


def test_render_week_view_shows_no_events_placeholder_for_empty_days():
    html = render_week_view(date(2026, 7, 20), [])
    assert "No events" in html


def test_render_week_view_navigates_to_post_detail_on_click():
    html = render_week_view(date(2026, 7, 20), [_event(post_id=42)])
    assert "onclick=\"location.href='/posts/42'\"" in html


def test_render_week_view_escapes_html_in_company_name():
    html = render_week_view(date(2026, 7, 20), [_event(company="<script>alert(1)</script>")])
    assert "<script>alert(1)</script>" not in html


def test_render_week_view_excludes_events_outside_the_week():
    html = render_week_view(date(2026, 7, 20), [_event(deadline_dt=datetime(2026, 8, 1, 10, 0, tzinfo=IST))])
    assert "Acme" not in html


def test_render_week_view_has_prev_today_next_navigation():
    html = render_week_view(date(2026, 7, 20), [])
    assert "week=2026-07-13" in html
    assert "week=2026-07-27" in html
