from app.extraction import dedup_key


def test_dedup_key_is_case_and_whitespace_insensitive():
    a = dedup_key("Acme Corp", "SDE Intern", "2026-07-20T23:59:00+05:30")
    b = dedup_key("  acme corp ", "sde intern", "2026-07-20T23:59:00+05:30")
    assert a == b


def test_dedup_key_differs_on_deadline_change():
    a = dedup_key("Acme", "SDE", "2026-07-20")
    b = dedup_key("Acme", "SDE", "2026-07-22")
    assert a != b


def test_dedup_key_handles_none_fields():
    a = dedup_key(None, None, None)
    b = dedup_key("", "", "")
    assert a == b
