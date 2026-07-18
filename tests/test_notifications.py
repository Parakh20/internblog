from app.extraction import PostCategory
from app.notifications import format_notification_message


def test_includes_company_role_deadline_and_stipend():
    msg = format_notification_message(
        category=PostCategory.NEW_LISTING,
        company="Acme",
        role="SWE Intern",
        deadline="2026-07-20T23:59:59+05:30",
        stipend="Rs 50,000/month",
    )
    assert "Acme" in msg
    assert "SWE Intern" in msg
    assert "2026-07-20T23:59:59+05:30" in msg
    assert "Rs 50,000/month" in msg


def test_no_link_in_message_body():
    # The post link used to be appended to every message; the user found
    # that noisy and asked for it to be dropped.
    msg = format_notification_message(
        category=PostCategory.NEW_LISTING,
        company="Acme", role="SWE Intern", deadline="2026-07-20T23:59:59+05:30",
        stipend=None,
    )
    assert "http" not in msg


def test_omits_missing_optional_fields():
    msg = format_notification_message(
        category=PostCategory.NEW_LISTING,
        company="Acme", role=None, deadline=None, stipend=None,
    )
    assert "Role:" not in msg
    assert "Date:" not in msg
    assert "Stipend:" not in msg


def test_falls_back_when_company_missing():
    msg = format_notification_message(
        category=PostCategory.NEW_LISTING,
        company=None, role=None, deadline=None, stipend=None,
    )
    assert "Unknown company" in msg


def test_label_reflects_category():
    msg = format_notification_message(
        category=PostCategory.TEST_RESCHEDULE,
        company="Acme", role=None, deadline="2026-07-22T10:00:00+05:30", stipend=None,
    )
    assert msg.startswith("Test/OA rescheduled: Acme")


def test_administrative_notices_are_not_sent():
    from app.extraction import NOTIFY_CATEGORIES

    assert PostCategory.ADMINISTRATIVE not in NOTIFY_CATEGORIES
    assert PostCategory.OTHER not in NOTIFY_CATEGORIES


def test_includes_location_when_present():
    msg = format_notification_message(
        category=PostCategory.NEW_LISTING,
        company="Acme", role="SWE Intern", deadline=None, stipend=None,
        location="Bangalore",
    )
    assert "Location: Bangalore" in msg


def test_includes_application_link_when_present():
    msg = format_notification_message(
        category=PostCategory.NEW_LISTING,
        company="Acme", role="SWE Intern", deadline=None, stipend=None,
        application_link="https://forms.gle/abc123",
    )
    assert "Link: https://forms.gle/abc123" in msg


def test_omits_location_and_link_when_absent():
    msg = format_notification_message(
        category=PostCategory.NEW_LISTING,
        company="Acme", role=None, deadline=None, stipend=None,
    )
    assert "Location:" not in msg
    assert "Link:" not in msg
