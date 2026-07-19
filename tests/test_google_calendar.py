from app.google_calendar import _to_rfc3339, event_id_for, event_id_for_company


def test_event_id_uses_only_valid_charset():
    event_id = event_id_for(42)
    assert all(c in "abcdefghijklmnopqrstuv0123456789" for c in event_id)


def test_event_id_meets_minimum_length_for_small_ids():
    # "evt5" alone would be 4 chars; the Calendar API requires >= 5.
    assert len(event_id_for(5)) >= 5


def test_company_event_id_is_stable_for_same_company_and_group():
    a = event_id_for_company("McKinsey and Company", "deadline")
    b = event_id_for_company("McKinsey and Company", "deadline")
    assert a == b


def test_company_event_id_is_case_and_whitespace_insensitive():
    a = event_id_for_company("McKinsey and Company", "deadline")
    b = event_id_for_company("  mckinsey and company ", "deadline")
    assert a == b


def test_company_event_id_differs_by_group():
    deadline_id = event_id_for_company("McKinsey and Company", "deadline")
    ppt_id = event_id_for_company("McKinsey and Company", "ppt")
    assert deadline_id != ppt_id


def test_company_event_id_uses_only_valid_charset():
    event_id = event_id_for_company("Acme, Inc.", "deadline")
    assert all(c in "abcdefghijklmnopqrstuv0123456789" for c in event_id)


def test_naive_datetime_assumed_ist():
    result = _to_rfc3339("2026-07-20T23:59:59")
    assert result == "2026-07-20T23:59:59+05:30"


def test_offset_datetime_preserved():
    result = _to_rfc3339("2026-07-20T23:59:59+05:30")
    assert result == "2026-07-20T23:59:59+05:30"


def test_unparseable_datetime_returns_none():
    assert _to_rfc3339("not a date") is None


import httpx

from app.google_calendar import create_secondary_calendar, upsert_event


def test_create_secondary_calendar_returns_new_calendar_id(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        assert url == "https://www.googleapis.com/calendar/v3/calendars"
        assert json == {"summary": "Internblog Deadlines"}
        request = httpx.Request("POST", url, headers=headers, json=json)
        return httpx.Response(200, json={"id": "new-cal-id"}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    assert create_secondary_calendar("access-token") == "new-cal-id"


def test_create_secondary_calendar_returns_none_on_error(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(500, json={"error": "boom"}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    assert create_secondary_calendar("access-token") is None


def _upsert(**overrides):
    kwargs = dict(
        access_token="access-token", calendar_id="cal-1", event_id="evt1",
        summary="s", description="d", start_iso="2026-07-20T10:00:00+05:30", end_iso=None,
    )
    kwargs.update(overrides)
    return upsert_event(**kwargs)


def test_upsert_event_returns_ok_on_success(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url, headers=headers, json=json)
        return httpx.Response(200, json={"id": "evt1"}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    assert _upsert() == "ok"


def test_upsert_event_returns_calendar_missing_on_404(monkeypatch):
    # A deleted or never-created calendar 404s on the very POST that would
    # create the event - the caller uses this signal to clear the stale
    # stored calendar id and get a fresh one, rather than failing forever.
    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url, headers=headers, json=json)
        return httpx.Response(404, json={"error": "not found"}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    assert _upsert(calendar_id="deleted-cal") == "calendar_missing"


def test_upsert_event_returns_error_on_other_failure(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url, headers=headers, json=json)
        return httpx.Response(500, json={"error": "boom"}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    assert _upsert() == "error"


def test_upsert_event_returns_error_for_unparseable_start():
    assert _upsert(start_iso="not a date") == "error"


def test_upsert_event_retries_as_put_on_409_conflict(monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append("POST")
        request = httpx.Request("POST", url, headers=headers, json=json)
        return httpx.Response(409, json={"error": "conflict"}, request=request)

    def fake_put(url, headers=None, json=None, timeout=None):
        calls.append("PUT")
        request = httpx.Request("PUT", url, headers=headers, json=json)
        return httpx.Response(200, json={"id": "evt1"}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "put", fake_put)
    assert _upsert() == "ok"
    assert calls == ["POST", "PUT"]
