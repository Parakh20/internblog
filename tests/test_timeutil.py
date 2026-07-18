from app.timeutil import parse_gmt, parse_ist


def test_naive_value_assumed_ist():
    dt = parse_ist("2026-07-20T23:59:59")
    assert dt.isoformat() == "2026-07-20T23:59:59+05:30"


def test_offset_value_preserved():
    dt = parse_ist("2026-07-20T23:59:59+05:30")
    assert dt.isoformat() == "2026-07-20T23:59:59+05:30"


def test_none_returns_none():
    assert parse_ist(None) is None


def test_empty_string_returns_none():
    assert parse_ist("") is None


def test_unparseable_returns_none():
    assert parse_ist("not a date") is None


def test_naive_gmt_value_assumed_utc_not_ist():
    dt = parse_gmt("2026-07-20T23:59:59")
    assert dt.isoformat() == "2026-07-20T23:59:59+00:00"


def test_gmt_none_returns_none():
    assert parse_gmt(None) is None
