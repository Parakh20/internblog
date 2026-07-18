from app.dashboard import _extraction_row


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
