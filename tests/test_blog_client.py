import json

import pytest

from app.blog_client import (
    CookieLoadError,
    classify_response,
    load_session_cookie,
)


class TestClassifyResponse:
    def test_json_200_is_ok(self):
        assert classify_response(200, "", "application/json; charset=UTF-8") == "ok"

    def test_redirect_to_sso_is_session_expired(self):
        location = "https://sso.iitb.ac.in/authorize?response_type=code"
        assert classify_response(302, location, "") == "session_expired"

    def test_html_200_is_session_expired(self):
        assert classify_response(200, "", "text/html; charset=UTF-8") == "session_expired"

    def test_401_is_session_expired(self):
        assert classify_response(401, "", "application/json") == "session_expired"

    def test_redirect_elsewhere_is_error(self):
        assert classify_response(302, "https://example.com/", "") == "error"

    def test_server_error_is_error(self):
        assert classify_response(500, "", "text/html") == "error"


class TestLoadSessionCookie:
    def test_loads_cookie_value(self, tmp_path):
        state = {"cookies": [{"name": "mod_auth_openidc_session", "value": "abc123"}]}
        path = tmp_path / "storage_state.json"
        path.write_text(json.dumps(state))
        assert load_session_cookie(path) == "abc123"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(CookieLoadError):
            load_session_cookie(tmp_path / "nope.json")

    def test_missing_cookie_raises(self, tmp_path):
        path = tmp_path / "storage_state.json"
        path.write_text(json.dumps({"cookies": []}))
        with pytest.raises(CookieLoadError):
            load_session_cookie(path)
