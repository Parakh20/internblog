from app.site import render_privacy_page, render_terms_page


def test_privacy_page_mentions_what_data_is_collected():
    html = render_privacy_page()
    assert "Google" in html
    assert "calendar" in html.lower()
    assert "Telegram" in html
    assert "encrypted" in html.lower()


def test_terms_page_renders():
    html = render_terms_page()
    assert "internblog" in html.lower()
