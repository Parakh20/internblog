from app.calendar_feed import DeadlineEvent, build_ics


def test_builds_valid_calendar_wrapper():
    ics = build_ics([])
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.endswith("END:VCALENDAR\r\n")
    assert "VERSION:2.0" in ics


def test_includes_one_vevent_per_deadline():
    events = [
        DeadlineEvent(uid="1", company="Acme", role="SWE Intern",
                      deadline="2026-07-20T23:59:59+05:30", deadline_end=None,
                      link="https://blog.example/acme"),
        DeadlineEvent(uid="2", company="Globex", role=None,
                      deadline="2026-08-01T18:00:00+05:30", deadline_end=None,
                      link="https://blog.example/globex"),
    ]
    ics = build_ics(events)
    assert ics.count("BEGIN:VEVENT") == 2
    assert ics.count("END:VEVENT") == 2
    assert "SUMMARY:Acme deadline - SWE Intern" in ics
    assert "SUMMARY:Globex deadline" in ics


def test_ist_naive_deadline_converted_to_utc():
    events = [
        DeadlineEvent(uid="1", company="Acme", role=None,
                      deadline="2026-07-20T23:59:59", deadline_end=None,
                      link="https://blog.example/acme"),
    ]
    ics = build_ics(events)
    # IST is UTC+5:30, so 23:59:59 IST is 18:29:59 UTC the same day.
    assert "DTSTART:20260720T182959Z" in ics


def test_offset_deadline_converted_to_utc():
    events = [
        DeadlineEvent(uid="1", company="Acme", role=None,
                      deadline="2026-07-20T23:59:59+05:30", deadline_end=None,
                      link="https://blog.example/acme"),
    ]
    ics = build_ics(events)
    assert "DTSTART:20260720T182959Z" in ics


def test_unparseable_deadline_is_skipped():
    events = [
        DeadlineEvent(uid="1", company="Acme", role=None, deadline="not a date",
                      deadline_end=None, link="https://x"),
    ]
    ics = build_ics(events)
    assert "BEGIN:VEVENT" not in ics


def test_special_characters_escaped():
    events = [
        DeadlineEvent(uid="1", company="Acme, Inc.; Ltd", role=None,
                      deadline="2026-07-20T10:00:00+05:30", deadline_end=None, link="https://x"),
    ]
    ics = build_ics(events)
    assert "Acme\\, Inc.\\; Ltd" in ics


def test_uid_strips_non_alphanumeric():
    events = [
        DeadlineEvent(uid="abc-123_x!", company="Acme", role=None,
                      deadline="2026-07-20T10:00:00+05:30", deadline_end=None, link="https://x"),
    ]
    ics = build_ics(events)
    assert "UID:abc123x@internblog" in ics


def test_dtend_defaults_to_dtstart_when_no_end_given():
    events = [
        DeadlineEvent(uid="1", company="Acme", role=None,
                      deadline="2026-07-20T23:59:59+05:30", deadline_end=None,
                      link="https://x"),
    ]
    ics = build_ics(events)
    assert "DTSTART:20260720T182959Z" in ics
    assert "DTEND:20260720T182959Z" in ics


def test_dtend_uses_explicit_end_when_given():
    events = [
        DeadlineEvent(uid="1", company="Acme", role=None,
                      deadline="2026-07-20T18:00:00+05:30", deadline_end="2026-07-20T19:00:00+05:30",
                      link="https://x"),
    ]
    ics = build_ics(events)
    assert "DTSTART:20260720T123000Z" in ics
    assert "DTEND:20260720T133000Z" in ics
