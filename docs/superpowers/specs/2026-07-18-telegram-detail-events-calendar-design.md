# Design: richer Telegram messages, blog-post detail page, secondary events calendar

**Date:** 2026-07-18
**Status:** Approved, ready for implementation planning

## Problem

Telegram notifications are too terse ("go check the blog for details"). The
website's extraction tables show only category/company/role/deadline/link,
with no way to see the original blog post content without leaving the site.
Separately, the Google Calendar push currently dumps everything - both
application deadlines and time-ranged events (tests/OA, PPTs) - into a
single "Internblog Deadlines" calendar, even though the latter already carry
a distinct start/end time range (`Extraction.deadline` /
`Extraction.deadline_end`).

## Scope

Four independent changes, bundled because they touch the same
notification/calendar/site surfaces:

1. Telegram messages include stipend, location, and application link.
2. Website database rows are clickable, opening a detail page with the full
   scraped blog post content.
3. Test/OA/PPT events push to a second, dedicated Google Calendar instead of
   the deadlines calendar.
4. The admin portal (`/admin`) lists registered users.

## 1. Telegram message content

`app/notifications.py::format_notification_message` gains two new
parameters: `location: str | None` and `application_link: str | None`. Each
is appended as its own line when present, after the existing stipend line:

```
New posting: Acme Corp
Role: SDE Intern
Date: 20 Jul 2026, 11:59 PM IST
Stipend: 50000/month
Location: Bangalore
Link: https://forms.gle/...
```

`app/pipeline.py::push_to_all_users` is the only caller; it already has
`extraction.location` and `extraction.application_link` available (both
existing `Extraction` columns) and passes them through. No schema change.

## 2. Blog post detail page

### Route

`GET /posts/{post_id}` in `app/main.py`, gated by the same
`get_current_user_or_redirect` dependency used by `/`. Any signed-in user
can view any post detail (posts are public blog content, not
user-scoped) - this matches today's model where `/` already shows every
extraction to every signed-in user. 404s (via `HTTPException(404)`) if the
post id doesn't exist.

### Rendering

New `render_post_detail(post: Post) -> str` in `app/site.py`, styled with
the existing `_STYLE` block. Shows:
- Post title
- Posted-at timestamp (IST, via the existing `_fmt_posted`-equivalent
  formatting already used in `dashboard.py`)
- A link back to the original blog post (`post.link`)
- A "back to database" link to `/`
- The full post body, rendered as sanitized HTML (see below)

### Sanitization

`post.raw_html` is fetched blog content, not something we fully control
(compromised upstream, injected ad, malformed post). Add `bleach` to
`requirements.txt` and sanitize before rendering:

```python
import bleach

ALLOWED_TAGS = ["p", "br", "b", "strong", "i", "em", "a", "ul", "ol", "li", "span", "div"]
ALLOWED_ATTRS = {"a": ["href"]}

def _sanitize_post_html(raw_html: str) -> str:
    return bleach.clean(raw_html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
```

This strips `<script>`, event handler attributes (`onclick` etc.), and any
tag/attribute outside the allowlist, while preserving the formatting blog
posts actually use (paragraphs, bold, links, lists).

### Row click-through

`app/dashboard.py::_extraction_row` (shared by `/admin` and `/`, via
`app/site.py`) needs the post id threaded through. Both row-building call
sites in `app/main.py` (`home()` and `admin_dashboard()`) already join
`Extraction` with `Post` - add `"post_id": post.id` to the row dict.
`_extraction_row` then renders:

```python
f'<tr style="cursor:pointer" onclick="location.href=\'/posts/{r["post_id"]}\'">'
```

matching the existing plain-HTML-string, no-JS-framework approach already
used throughout `dashboard.py` and `site.py`.

## 3. Secondary "Internblog Events" calendar

### Category split

`app/extraction.py` currently has one `CALENDAR_CATEGORIES` set. Split into:

```python
DEADLINE_CALENDAR_CATEGORIES = frozenset({PostCategory.NEW_LISTING, PostCategory.DEADLINE_EXTENSION})
EVENT_CALENDAR_CATEGORIES = frozenset({PostCategory.TEST_UPDATE, PostCategory.TEST_RESCHEDULE, PostCategory.PPT})
CALENDAR_CATEGORIES = DEADLINE_CALENDAR_CATEGORIES | EVENT_CALENDAR_CATEGORIES
```

`CALENDAR_CATEGORIES` (the union) keeps its current meaning for every
existing consumer that doesn't care which calendar an event lives in: the
ICS feed (`app/calendar_feed.py`), and the "upcoming" queries in
`app/main.py`. Those stay unified across both categories - no behavior
change there.

### Schema

Add `User.event_calendar_id: Mapped[str | None]` (nullable `String(255)`),
mirroring `calendar_id`. Ad-hoc migration added to
`app/db.py::_add_missing_columns`, following the existing
`telegram_link_code` pattern (check column absence, `ALTER TABLE ADD
COLUMN`).

### Lazy creation

Unlike `calendar_id` (created at login in `app/auth.py::upsert_user_from_google`),
`event_calendar_id` is created lazily, the first time it's actually needed -
this means existing signed-in users get it automatically on their next
test/OA/PPT post, without needing to re-authenticate.

New `get_or_create_event_calendar(db: Session, user: User, access_token: str) -> str | None`
in `app/pipeline.py`:
- Returns `user.event_calendar_id` if already set.
- Otherwise calls the existing `create_secondary_calendar(access_token,
  "Internblog Events")`, persists the result to `user.event_calendar_id`,
  commits, and returns it. Returns `None` (skip push) if creation fails,
  same failure handling as every other calendar call in this module.

### Push routing

`push_calendar_event_for_user(db, user, extraction, row)` (gains a `db`
parameter) picks the target calendar:

```python
if extraction.category in {c.value for c in DEADLINE_CALENDAR_CATEGORIES}:
    calendar_id = user.calendar_id
elif extraction.category in {c.value for c in EVENT_CALENDAR_CATEGORIES}:
    calendar_id = get_or_create_event_calendar(db, user, access_token)
else:
    return
if calendar_id is None:
    return
```

`push_to_all_users` passes `db` through to `push_calendar_event_for_user`.

### Out of scope

- No per-calendar sync toggle - the existing single `calendar_sync_enabled`
  checkbox continues to gate both calendars together.
- No migration of already-pushed test/PPT events out of the deadlines
  calendar - only new pushes after this change go to the new calendar.
- ICS feed and "Upcoming" list stay unified (not split by calendar).

## 4. Admin: registered users list

### Data

`app/main.py::admin_dashboard` queries all `User` rows (`db.query(User).all()`,
same simple pattern used elsewhere in this file) and builds a row per user
with: email, name, signed-up-at (`created_at`), calendar sync status
(`calendar_sync_enabled` + whether `calendar_id`/`event_calendar_id` exist),
and whether Telegram is connected (`telegram_chat_id is not None`). No new
column needed - every field already exists on `User`.

Nothing sensitive is exposed: no tokens, no encrypted refresh token, no
session ids - only account-identifying and connection-status fields, and
this route is already admin-gated (`auth.is_admin`, 404 for anyone else).

### Rendering

New `_user_row(user: dict) -> str` and a "Registered users" `<table>` card
in `app/dashboard.py::render_dashboard`, styled consistently with the
existing `.card`/`table` classes - same pattern as the "Database" table
already on that page. Not clickable (no per-user detail page in scope).

## Testing

- `test_notifications.py` (new or extended): `format_notification_message`
  with/without location and application_link.
- `test_site.py` / `test_main_pages.py`: `/posts/{id}` returns sanitized
  content (script tags stripped, allowed formatting preserved), 404s on
  unknown id, redirects to `/login` when unauthenticated; row HTML includes
  the `onclick` navigation.
- `test_pipeline.py` (new or extended): event-category extraction pushes to
  `event_calendar_id`, deadline-category extraction pushes to `calendar_id`;
  `get_or_create_event_calendar` creates-and-persists on first call, reuses
  on subsequent calls.
- `app/db.py` migration: existing-db upgrade test (if such a pattern
  already exists in the test suite) confirming `event_calendar_id` is added
  without dropping data.
- `test_main_pages.py`: `/admin` includes each registered user's email;
  non-admin `/admin` still 404s.

## Files touched

- `app/notifications.py`, `app/pipeline.py` (Telegram fields)
- `app/main.py`, `app/site.py`, `app/dashboard.py`, `requirements.txt`
  (detail page)
- `app/extraction.py`, `app/models.py`, `app/db.py`, `app/pipeline.py`,
  `app/auth.py` (no change expected, verify), `app/google_calendar.py` (no
  change expected, verify) (events calendar)
- `app/main.py`, `app/dashboard.py` (admin user list)
- Corresponding test files under `tests/`
