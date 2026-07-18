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
