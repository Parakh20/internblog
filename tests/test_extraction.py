from app.extraction import InternshipExtraction, PostCategory, dedup_key


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


def test_single_time_in_event_end_recovered_into_deadline():
    # Some models put a post's only stated time into event_end and leave
    # deadline null, even with no start-end range - recover it since
    # notifications/calendar filter on deadline being set.
    parsed = InternshipExtraction(
        category=PostCategory.PPT,
        deadline=None,
        event_end="2026-07-10T19:00:00",
        summary="PPT notice",
    )
    assert parsed.deadline == "2026-07-10T19:00:00"
    assert parsed.event_end is None


def test_null_eligible_branches_coerced_to_empty_list():
    # Models sometimes emit null instead of [] when unstated; this must not
    # raise a ValidationError and silently drop the whole extraction.
    parsed = InternshipExtraction(
        category=PostCategory.PPT,
        eligible_branches=None,
        summary="PPT notice",
    )
    assert parsed.eligible_branches == []


def test_null_summary_coerced_to_empty_string():
    parsed = InternshipExtraction(category=PostCategory.ADMINISTRATIVE, summary=None)
    assert parsed.summary == ""


def test_role_list_joined_into_string():
    parsed = InternshipExtraction(
        category=PostCategory.NEW_LISTING,
        role=["Software Engineering Intern", "Quant Research Intern"],
        summary="x",
    )
    assert parsed.role == "Software Engineering Intern, Quant Research Intern"


def test_application_link_list_takes_first():
    parsed = InternshipExtraction(
        category=PostCategory.NEW_LISTING,
        application_link=["https://a.example/1", "https://a.example/2"],
        summary="x",
    )
    assert parsed.application_link == "https://a.example/1"


def test_year_hallucination_clamped_to_season():
    # Seen on real posts: PPT/test dates coming back a year early (2025
    # instead of 2026) even though the prompt states the season explicitly.
    parsed = InternshipExtraction.model_validate(
        {"category": "ppt", "deadline": "2025-07-16T19:30:00", "summary": "x"}
    )
    assert parsed.deadline == "2026-07-16T19:30:00"


def test_time_only_value_combined_with_post_date_fallback():
    # Seen on real posts: the model returns just "11:00:00" with no date at
    # all, which would otherwise be silently dropped by every downstream
    # consumer (all of which require a full ISO datetime).
    parsed = InternshipExtraction.model_validate(
        {"category": "test_update", "deadline": "11:00:00", "summary": "x"},
        context={"post_date": "2026-07-10T08:00:00"},
    )
    assert parsed.deadline == "2026-07-10T11:00:00"


def test_time_only_value_without_fallback_left_as_is():
    parsed = InternshipExtraction.model_validate(
        {"category": "test_update", "deadline": "11:00:00", "summary": "x"}
    )
    assert parsed.deadline == "11:00:00"


def test_unparseable_deadline_left_untouched_for_downstream_to_drop():
    parsed = InternshipExtraction.model_validate(
        {"category": "test_update", "deadline": "not a date", "summary": "x"}
    )
    assert parsed.deadline == "not a date"


def test_genuine_range_is_not_touched():
    parsed = InternshipExtraction(
        category=PostCategory.TEST_UPDATE,
        deadline="2026-07-10T18:00:00",
        event_end="2026-07-10T19:00:00",
        summary="Test window",
    )
    assert parsed.deadline == "2026-07-10T18:00:00"
    assert parsed.event_end == "2026-07-10T19:00:00"


def test_deadline_and_event_categories_partition_calendar_categories():
    assert DEADLINE_CALENDAR_CATEGORIES | EVENT_CALENDAR_CATEGORIES == CALENDAR_CATEGORIES
    assert DEADLINE_CALENDAR_CATEGORIES & EVENT_CALENDAR_CATEGORIES == frozenset()


def test_deadline_categories_are_new_listing_and_extension():
    assert DEADLINE_CALENDAR_CATEGORIES == frozenset(
        {PostCategory.NEW_LISTING, PostCategory.DEADLINE_EXTENSION}
    )


def test_event_categories_are_tests_and_ppt():
    assert EVENT_CALENDAR_CATEGORIES == frozenset(
        {PostCategory.TEST_UPDATE, PostCategory.TEST_RESCHEDULE, PostCategory.PPT}
    )