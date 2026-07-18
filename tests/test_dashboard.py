from app.dashboard import _extraction_row, _fmt_ts


def _row(**overrides):
    defaults = dict(
        post_id=42, category="new_listing", company="Acme", role="SWE Intern",
        deadline=None, link="https://blog.example.com/a", posted_at=None,
    )
    defaults.update(overrides)
    return defaults


def test_extraction_row_navigates_to_post_detail_on_click():
    html = _extraction_row(_row())
    assert "onclick=\"location.href='/posts/42'\"" in html


def test_extraction_row_is_visually_clickable():
    html = _extraction_row(_row())
    assert 'style="cursor:pointer"' in html


def test_fmt_ts_converts_utc_to_ist():
    # 09:58:22 UTC -> 15:28:22 IST (+05:30)
    assert _fmt_ts("2026-07-18T09:58:22+00:00") == "2026-07-18 15:28:22 IST"


def test_fmt_ts_returns_dash_for_missing_value():
    assert _fmt_ts(None) == "-"
