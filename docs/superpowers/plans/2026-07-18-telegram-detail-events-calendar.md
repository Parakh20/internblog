# Telegram fields, post detail page, events calendar, admin user list Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Richer Telegram notifications, a clickable blog-post detail page on the website, a second Google Calendar for time-ranged events (tests/OA/PPT), and an admin-visible list of registered users.

**Architecture:** All four changes are additive to the existing FastAPI + SQLAlchemy + plain-HTML-f-string app (no template engine, no JS framework, no migration tool — ad-hoc `ALTER TABLE` in `app/db.py::_add_missing_columns`). No new architectural layers; each change extends an existing module along its established pattern.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, pytest, httpx, `bleach` (new dependency, HTML sanitizer).

## Global Constraints

- Follow the existing plain-HTML-f-string rendering style in `app/site.py` / `app/dashboard.py` — no template engine, no JS framework.
- Every new/changed function keeps type annotations (existing codebase convention).
- `html.escape()` on every piece of untrusted string data rendered into HTML, exactly as done throughout `app/dashboard.py` and `app/site.py` today.
- No DB migration tool — new columns go through `app/db.py::_add_missing_columns`, following the existing `telegram_link_code` pattern (check-then-`ALTER TABLE ADD COLUMN`).
- Tests use pytest, in-memory or tmp_path SQLite, matching the fixtures already in `tests/test_pipeline.py`, `tests/test_models.py`, `tests/test_main_pages.py`.
- Run the full suite (`pytest`) after each task, not just the new test, since several tasks touch shared functions (`_extraction_row`, `push_calendar_event_for_user`) with existing callers/tests.

---

### Task 1: Telegram message includes stipend, location, and application link

**Files:**
- Modify: `app/notifications.py:28-43` (`format_notification_message`)
- Modify: `app/pipeline.py:230-233` (the one caller, inside `push_to_all_users`)
- Test: `tests/test_notifications.py`

**Interfaces:**
- Produces: `format_notification_message(category: PostCategory, company: str | None, role: str | None, deadline: str | None, stipend: str | None, location: str | None = None, application_link: str | None = None) -> str`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_notifications.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_notifications.py -v`
Expected: `test_includes_location_when_present` and `test_includes_application_link_when_present` FAIL with `TypeError: format_notification_message() got an unexpected keyword argument 'location'`.

- [ ] **Step 3: Implement**

Replace `format_notification_message` in `app/notifications.py`:

```python
def format_notification_message(
    category: PostCategory,
    company: str | None,
    role: str | None,
    deadline: str | None,
    stipend: str | None,
    location: str | None = None,
    application_link: str | None = None,
) -> str:
    label = _CATEGORY_LABELS.get(category, "Update")
    lines = [f"{label}: {company or 'Unknown company'}"]
    if role:
        lines.append(f"Role: {role}")
    if deadline:
        lines.append(f"Date: {deadline}")
    if stipend:
        lines.append(f"Stipend: {stipend}")
    if location:
        lines.append(f"Location: {location}")
    if application_link:
        lines.append(f"Link: {application_link}")
    return "\n".join(lines)
```

In `app/pipeline.py`, update the one call site inside `push_to_all_users` (around line 230):

```python
            if message is None:
                message = format_notification_message(
                    category=PostCategory(extraction.category), company=extraction.company,
                    role=extraction.role, deadline=extraction.deadline, stipend=extraction.stipend,
                    location=extraction.location, application_link=extraction.application_link,
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_notifications.py tests/test_pipeline.py -v`
Expected: all PASS (including the pre-existing `test_no_link_in_message_body`, which passes `application_link` as unset/`None` implicitly via keyword defaults, so `"http"` still doesn't appear).

- [ ] **Step 5: Commit**

```bash
git add app/notifications.py app/pipeline.py tests/test_notifications.py
git commit -m "feat: include stipend, location, and application link in telegram messages"
```

---

### Task 2: Blog post content sanitizer and detail page renderer

**Files:**
- Modify: `requirements.txt` (add `bleach`)
- Modify: `app/site.py` (add `_sanitize_post_html`, `render_post_detail`, imports)
- Test: `tests/test_site.py`

**Interfaces:**
- Consumes: `app.models.Post` (`title`, `raw_html`, `link`, `date_gmt` fields — all exist today); `app.dashboard._fmt_posted(value: str | None) -> str` (exists today, formats `date_gmt` in IST).
- Produces: `render_post_detail(post: Post) -> str`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_site.py`:

```python
from app.models import Post
from app.site import render_post_detail


def _post(**overrides):
    defaults = dict(
        id=1, wp_id=1, slug="acme-sde-intern", title="Acme - SDE Intern",
        link="https://blog.example.com/acme-sde-intern", date_gmt="2026-07-17T10:00:00",
        modified_gmt="2026-07-17T10:00:00", content_hash="h", raw_html="<p>We are hiring.</p>",
    )
    defaults.update(overrides)
    return Post(**defaults)


def test_post_detail_shows_title_and_content():
    html = render_post_detail(_post())
    assert "Acme - SDE Intern" in html
    assert "We are hiring." in html


def test_post_detail_links_back_to_original_post():
    html = render_post_detail(_post())
    assert 'href="https://blog.example.com/acme-sde-intern"' in html


def test_post_detail_strips_script_tags():
    html = render_post_detail(_post(raw_html="<p>Hi</p><script>alert(1)</script>"))
    assert "<script>" not in html
    assert "alert(1)" not in html


def test_post_detail_strips_event_handler_attributes():
    html = render_post_detail(_post(raw_html='<p onclick="alert(1)">Hi</p>'))
    assert "onclick" not in html


def test_post_detail_preserves_allowed_formatting():
    html = render_post_detail(_post(raw_html='<p>Apply <a href="https://apply.example.com">here</a></p>'))
    assert '<a href="https://apply.example.com">here</a>' in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_site.py -v -k post_detail`
Expected: FAIL with `ImportError: cannot import name 'render_post_detail' from 'app.site'`.

- [ ] **Step 3: Implement**

Add `bleach` to `requirements.txt` (append a new line, alphabetical position not required — matches the existing unsorted list):

```
bleach>=6.1
```

Install it:

```bash
pip install bleach
```

In `app/site.py`, update the imports at the top and add the new functions. Change:

```python
from app.dashboard import _extraction_row, _fmt_ts, _safe_link
from app.models import User
```

to:

```python
import bleach

from app.dashboard import _extraction_row, _fmt_posted, _fmt_ts, _safe_link
from app.models import Post, User
```

Add near the bottom of `app/site.py` (after `render_terms_page`, before `_upcoming_row`):

```python
_ALLOWED_POST_TAGS = ["p", "br", "b", "strong", "i", "em", "a", "ul", "ol", "li", "span", "div"]
_ALLOWED_POST_ATTRS = {"a": ["href"]}


def _sanitize_post_html(raw_html: str) -> str:
    """Strips scripts, event handler attributes, and any tag/attribute
    outside a conservative allowlist from scraped blog post content before
    it's rendered on our own page - raw_html is fetched content we don't
    fully control, not something we authored."""
    return bleach.clean(raw_html, tags=_ALLOWED_POST_TAGS, attributes=_ALLOWED_POST_ATTRS, strip=True)


def render_post_detail(post: Post) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{escape(post.title)} - internblog</title><style>{_STYLE}</style></head>
<body>
<div class="wrap">
  <div class="card">
    <p><a href="/">&larr; Back to database</a></p>
    <h1>{escape(post.title)}</h1>
    <p style="color:#666;font-size:0.85rem">Posted {escape(_fmt_posted(post.date_gmt))} &middot; {_safe_link(post.link)}</p>
    <div>{_sanitize_post_html(post.raw_html)}</div>
  </div>
</div>
</body></html>"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_site.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt app/site.py tests/test_site.py
git commit -m "feat: add sanitized blog post detail page renderer"
```

---

### Task 3: `/posts/{post_id}` route

**Files:**
- Modify: `app/main.py` (imports + new route)
- Test: `tests/test_main_pages.py`

**Interfaces:**
- Consumes: `render_post_detail(post: Post) -> str` (Task 2); `get_current_user_or_redirect` (existing dependency, `app/main.py:82-88`).
- Produces: `GET /posts/{post_id}` — 200 with rendered HTML if found, 404 if not, redirect to `/login` if unauthenticated (via the existing `RedirectToLogin` exception handler).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_main_pages.py`:

```python
def test_post_detail_requires_login(client, _fresh_db):
    from app.models import Post

    db = _fresh_db()
    db.add(Post(
        wp_id=1, slug="a", title="Acme - SDE Intern", link="https://blog.example.com/a",
        date_gmt="2026-07-17T10:00:00", modified_gmt="2026-07-17T10:00:00",
        content_hash="h", raw_html="<p>Hi</p>",
    ))
    db.commit()

    response = client.get("/posts/1", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/login"


def test_post_detail_shows_full_content_when_logged_in(client, _fresh_db):
    from app.models import Post

    _login_as(client, _fresh_db, "reader@example.com")
    db = _fresh_db()
    db.add(Post(
        wp_id=2, slug="b", title="Acme - SDE Intern", link="https://blog.example.com/b",
        date_gmt="2026-07-17T10:00:00", modified_gmt="2026-07-17T10:00:00",
        content_hash="h", raw_html="<p>We are hiring.</p>",
    ))
    db.commit()
    post_id = db.query(Post).filter_by(wp_id=2).one().id

    response = client.get(f"/posts/{post_id}")
    assert response.status_code == 200
    assert "We are hiring." in response.text


def test_post_detail_404s_for_unknown_post(client, _fresh_db):
    _login_as(client, _fresh_db, "reader2@example.com")
    response = client.get("/posts/999999")
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main_pages.py -v -k post_detail`
Expected: FAIL with 404 Not Found (route doesn't exist yet — FastAPI's default 404 for an unregistered path) instead of the expected 307/200/404 behavior.

- [ ] **Step 3: Implement**

In `app/main.py`, update the `app.site` import (around line 26):

```python
from app.site import render_calendar_view, render_homepage, render_login_page, render_post_detail, render_privacy_page, render_terms_page
```

Add the route after `terms_page()` (around line 191, before the Google verification route):

```python
@app.get("/posts/{post_id}", response_class=HTMLResponse)
def post_detail(post_id: int, user: User = Depends(get_current_user_or_redirect)) -> str:
    with SessionLocal() as db:
        post = db.query(Post).filter_by(id=post_id).one_or_none()
    if post is None:
        raise HTTPException(status_code=404)
    return render_post_detail(post)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main_pages.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_main_pages.py
git commit -m "feat: add /posts/{id} route for viewing full blog post content"
```

---

### Task 4: Clickable database rows

**Files:**
- Modify: `app/dashboard.py:59-70` (`_extraction_row`)
- Modify: `app/main.py` (`home()` and `admin_dashboard()` row-building dicts)
- Test: `tests/test_dashboard.py` (new file)
- Test: `tests/test_site.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_extraction_row(r: dict, show_posted: bool = False) -> str` now requires `r["post_id"]` (previously not required — every caller must supply it).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dashboard.py`:

```python
from app.dashboard import _extraction_row


def _row(**overrides):
    defaults = dict(
        post_id=42, category="new_listing", company="Acme", role="SWE Intern",
        deadline=None, link="https://blog.example.com/a", posted_at=None,
    )
    defaults.update(overrides)
    return defaults


def test_extraction_row_navigates_to_post_detail_on_click():
    html = _extraction_row(_row())
    assert "onclick=\"location.href='/posts/42'\"" in html


def test_extraction_row_is_visually_clickable():
    html = _extraction_row(_row())
    assert 'style="cursor:pointer"' in html
```

Add to `tests/test_site.py`, updating the two existing `recent=[{...}]` fixtures to include `post_id` (required now that `_extraction_row` reads it unconditionally):

```python
def test_calendar_view_lists_full_database_for_every_user():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = _render(
        user,
        recent=[{
            "post_id": 1, "category": "new_listing", "company": "Quantbox", "role": "Quant Researcher",
            "deadline": "2026-07-19T14:00:00+05:30", "link": "https://x", "posted_at": "2026-07-17T10:00:00",
            "created_at": "2026-07-17T10:05:00+00:00",
        }],
        total_extractions=1,
    )
    assert "Quantbox" in html
    assert "Quant Researcher" in html


def test_calendar_view_database_link_blocks_unsafe_scheme():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = _render(
        user,
        recent=[{
            "post_id": 2, "category": "new_listing", "company": "Acme", "role": None,
            "deadline": None, "link": "javascript:alert(1)", "posted_at": None, "created_at": None,
        }],
        total_extractions=1,
    )
    assert '<a href="javascript:alert(1)"' not in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboard.py tests/test_site.py -v -k "extraction_row or lists_full_database or database_link"`
Expected: `test_dashboard.py` tests FAIL with `ImportError` or `KeyError: 'post_id'`; the two `test_site.py` tests currently PASS before the fixture update (they'll KeyError only once `_extraction_row` is changed in Step 3 — this is fine, they were written to match the *target* state).

- [ ] **Step 3: Implement**

In `app/dashboard.py`, replace `_extraction_row`:

```python
def _extraction_row(r: dict, show_posted: bool = False) -> str:
    extra = f"<td>{escape(_fmt_posted(r['posted_at']))}</td>" if show_posted else ""
    return (
        f'<tr style="cursor:pointer" onclick="location.href=\'/posts/{r["post_id"]}\'">'
        f"<td>{escape(r['category'])}</td>"
        f"<td>{escape(r['company'] or '-')}</td>"
        f"<td>{escape(r['role'] or '-')}</td>"
        f"<td>{escape(_fmt_ist(r['deadline']))}</td>"
        f"<td>{_safe_link(r['link'])}</td>"
        f"{extra}"
        f"</tr>"
    )
```

In `app/main.py`, add `"post_id": post.id` to every dict comprehension that's consumed (directly or via `render_dashboard`) by `_extraction_row`. This is all four dict comprehensions built from `Extraction, Post` joins:

`home()` — only the `recent` comprehension needs it (the `upcoming` list there is rendered by `site.py::_upcoming_row`, which doesn't use `post_id`):

```python
        recent = sorted(
            (
                {
                    "post_id": post.id,
                    "category": extraction.category,
                    "company": extraction.company,
                    "role": extraction.role,
                    "deadline": extraction.deadline,
                    "link": post.link,
                    "posted_at": post.date_gmt,
                    "created_at": extraction.created_at.isoformat() if extraction.created_at else None,
                }
                for extraction, post in recent_rows
            ),
            key=lambda r: parse_gmt(r["posted_at"]) or _EPOCH,
            reverse=True,
        )
```

`admin_dashboard()` — both `upcoming` and `recent` need it, since `render_dashboard` passes both through `_extraction_row`:

```python
        upcoming = sorted(
            (
                {
                    "post_id": post.id,
                    "category": extraction.category,
                    "company": extraction.company,
                    "role": extraction.role,
                    "deadline": extraction.deadline,
                    "link": post.link,
                }
                for extraction, post in calendar_rows
                if _is_upcoming(extraction.deadline)
            ),
            key=lambda r: r["deadline"],
        )

        recent_rows_query = db.execute(
            select(Extraction, Post).join(Post, Extraction.post_id == Post.id)
        ).all()
        recent = sorted(
            (
                {
                    "post_id": post.id,
                    "category": extraction.category,
                    "company": extraction.company,
                    "role": extraction.role,
                    "deadline": extraction.deadline,
                    "link": post.link,
                    "posted_at": post.date_gmt,
                    "created_at": extraction.created_at.isoformat() if extraction.created_at else None,
                }
                for extraction, post in recent_rows_query
            ),
            key=lambda r: parse_gmt(r["posted_at"]) or _EPOCH,
            reverse=True,
        )
```

(Renamed the local variable `recent_rows` to `recent_rows_query` only inside `admin_dashboard` to avoid shadowing confusion with the outer `recent` — purely cosmetic, keep `home()`'s variable name as `recent_rows` unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dashboard.py tests/test_site.py tests/test_main_pages.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/dashboard.py app/main.py tests/test_dashboard.py tests/test_site.py
git commit -m "feat: make database table rows clickable, opening the full post detail page"
```

---

### Task 5: Split calendar categories into deadline vs. event buckets

**Files:**
- Modify: `app/extraction.py:79-89` (`CALENDAR_CATEGORIES`)
- Test: `tests/test_extraction.py`

**Interfaces:**
- Produces: `DEADLINE_CALENDAR_CATEGORIES: frozenset[PostCategory]`, `EVENT_CALENDAR_CATEGORIES: frozenset[PostCategory]`, `CALENDAR_CATEGORIES: frozenset[PostCategory]` (unchanged value, now derived as the union — every existing consumer keeps working unmodified).

- [ ] **Step 1: Write the failing tests**

Check `tests/test_extraction.py` first for existing conventions:

Run: `sed -n '1,40p' tests/test_extraction.py`

Add to `tests/test_extraction.py`:

```python
def test_deadline_and_event_categories_partition_calendar_categories():
    from app.extraction import CALENDAR_CATEGORIES, DEADLINE_CALENDAR_CATEGORIES, EVENT_CALENDAR_CATEGORIES

    assert DEADLINE_CALENDAR_CATEGORIES | EVENT_CALENDAR_CATEGORIES == CALENDAR_CATEGORIES
    assert DEADLINE_CALENDAR_CATEGORIES & EVENT_CALENDAR_CATEGORIES == frozenset()


def test_deadline_categories_are_new_listing_and_extension():
    from app.extraction import DEADLINE_CALENDAR_CATEGORIES

    assert DEADLINE_CALENDAR_CATEGORIES == frozenset({PostCategory.NEW_LISTING, PostCategory.DEADLINE_EXTENSION})


def test_event_categories_are_tests_and_ppt():
    from app.extraction import EVENT_CALENDAR_CATEGORIES

    assert EVENT_CALENDAR_CATEGORIES == frozenset(
        {PostCategory.TEST_UPDATE, PostCategory.TEST_RESCHEDULE, PostCategory.PPT}
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_extraction.py -v -k calendar_categories or deadline_categories or event_categories`
Expected: FAIL with `ImportError: cannot import name 'DEADLINE_CALENDAR_CATEGORIES'`.

- [ ] **Step 3: Implement**

In `app/extraction.py`, replace the `CALENDAR_CATEGORIES` definition (lines 79-89):

```python
# Application-deadline categories: push to the user's "Internblog Deadlines"
# calendar as a single point-in-time event.
DEADLINE_CALENDAR_CATEGORIES = frozenset(
    {
        PostCategory.NEW_LISTING,
        PostCategory.DEADLINE_EXTENSION,
    }
)

# Time-ranged event categories (tests/OA, PPTs): push to a separate
# "Internblog Events" calendar, using the post's start/end time range.
EVENT_CALENDAR_CATEGORIES = frozenset(
    {
        PostCategory.TEST_UPDATE,
        PostCategory.TEST_RESCHEDULE,
        PostCategory.PPT,
    }
)

# Union of both buckets - the existing meaning of "worth a calendar entry
# when it carries a date", used by the ICS feed and the "upcoming" queries,
# which don't care which calendar a given event lives in.
CALENDAR_CATEGORIES = DEADLINE_CALENDAR_CATEGORIES | EVENT_CALENDAR_CATEGORIES
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_extraction.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/extraction.py tests/test_extraction.py
git commit -m "feat: split calendar categories into deadline and event buckets"
```

---

### Task 6: `User.event_calendar_id` column and migration

**Files:**
- Modify: `app/models.py:98-99` (`User` model, add column after `calendar_id`)
- Modify: `app/db.py:48-55` (`_add_missing_columns`)
- Test: `tests/test_models.py`
- Test: `tests/test_db_migration.py` (new file)

**Interfaces:**
- Produces: `User.event_calendar_id: Mapped[str | None]`, nullable, no unique constraint (same shape as `calendar_id`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_models.py`:

```python
def test_user_round_trips_event_calendar_id():
    db = _db()
    user = User(google_sub="sub-evt", email="evt@example.com", calendar_id="cal-abc", event_calendar_id="cal-evt")
    db.add(user)
    db.commit()

    fetched = db.query(User).filter_by(google_sub="sub-evt").one()
    assert fetched.event_calendar_id == "cal-evt"


def test_user_event_calendar_id_defaults_to_none():
    db = _db()
    user = User(google_sub="sub-noevt", email="noevt@example.com")
    db.add(user)
    db.commit()

    fetched = db.query(User).filter_by(google_sub="sub-noevt").one()
    assert fetched.event_calendar_id is None
```

Create `tests/test_db_migration.py`:

```python
"""Verifies the ad-hoc column-addition migration in app/db.py runs safely
against a database created before event_calendar_id existed, without
dropping any existing data - the same style of check this project already
relies on for is_job_posting -> category and the telegram_link_code add."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.db import _add_missing_columns
from app.models import Base


def test_migration_adds_event_calendar_id_to_pre_existing_users_table(tmp_path):
    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    # Simulate a pre-migration users table: drop the column, insert a row.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users DROP COLUMN event_calendar_id"))
        conn.execute(
            text(
                "INSERT INTO users (google_sub, email, calendar_sync_enabled, created_at) "
                "VALUES ('legacy-sub', 'legacy@example.com', 1, '2026-01-01 00:00:00')"
            )
        )

    import app.db as db_module

    original_engine = db_module.engine
    db_module.engine = engine
    try:
        _add_missing_columns()
    finally:
        db_module.engine = original_engine

    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("users")}
    assert "event_calendar_id" in columns

    Session = sessionmaker(bind=engine)
    session = Session()
    row = session.execute(text("SELECT email FROM users WHERE google_sub = 'legacy-sub'")).one()
    assert row.email == "legacy@example.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_models.py tests/test_db_migration.py -v`
Expected: `test_models.py` new tests FAIL with `TypeError: 'event_calendar_id' is an invalid keyword argument for User`; `test_db_migration.py` fails at the `ALTER TABLE users DROP COLUMN event_calendar_id` setup step since the column doesn't exist yet to drop (`OperationalError: no such column`).

- [ ] **Step 3: Implement**

In `app/models.py`, add the column to `User` right after `calendar_id` (line 99):

```python
    calendar_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_calendar_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

In `app/db.py`, extend `_add_missing_columns` — add this block inside the existing `if "users" in inspector.get_table_names():` section, after the `telegram_link_code` block:

```python
    if "users" in inspector.get_table_names():
        users_columns = {c["name"] for c in inspector.get_columns("users")}
        if "telegram_link_code" not in users_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN telegram_link_code VARCHAR(64)"))
                conn.execute(
                    text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_telegram_link_code ON users (telegram_link_code)")
                )
        if "event_calendar_id" not in users_columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN event_calendar_id VARCHAR(255)"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models.py tests/test_db_migration.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models.py app/db.py tests/test_models.py tests/test_db_migration.py
git commit -m "feat: add User.event_calendar_id column with ad-hoc migration"
```

---

### Task 7: Route event-category pushes to the secondary events calendar

**Files:**
- Modify: `app/pipeline.py:194-237` (`push_calendar_event_for_user`, `push_to_all_users`, new `get_or_create_event_calendar`)
- Test: `tests/test_pipeline.py` (update existing monkeypatches + add new tests)

**Interfaces:**
- Consumes: `DEADLINE_CALENDAR_CATEGORIES`, `EVENT_CALENDAR_CATEGORIES` (Task 5); `User.event_calendar_id` (Task 6); existing `create_secondary_calendar(access_token: str, summary: str = "Internblog Deadlines") -> str | None` (`app/google_calendar.py:49`).
- Produces: `get_or_create_event_calendar(db: Session, user: User, access_token: str) -> str | None`; `push_calendar_event_for_user(db: Session, user: User, extraction: Extraction, row: Post) -> None` (signature change: **gains a leading `db` parameter** — every existing caller/monkeypatch must be updated).

- [ ] **Step 1: Write the failing tests**

This task changes an existing function's signature, which breaks several tests in `tests/test_pipeline.py` that monkeypatch `push_calendar_event_for_user` with a 3-argument lambda. Update those first, then add new tests.

In `tests/test_pipeline.py`, update every monkeypatch of `push_calendar_event_for_user` to accept the new leading `db` argument. There are three call sites:

```python
def test_calendar_push_only_reaches_sync_enabled_users_with_a_refresh_token(db, monkeypatch):
    pushed = []
    monkeypatch.setattr(pipeline, "push_calendar_event_for_user", lambda db, user, extraction, row: pushed.append(user.email))
    # ... rest unchanged
```

```python
def test_telegram_push_only_reaches_users_with_a_chat_id(db, monkeypatch):
    sent_to = []
    monkeypatch.setattr(
        pipeline, "send_or_edit_telegram_for_user",
        lambda db, user, extraction, message: sent_to.append(user.email),
    )
    monkeypatch.setattr(pipeline, "push_calendar_event_for_user", lambda *a: None)
    # ... rest unchanged (this one already used *a, no change needed)
```

```python
def test_shortlist_result_never_reaches_calendar_push_but_still_sends_telegram(db, monkeypatch):
    calendar_pushed = []
    telegram_sent = []
    monkeypatch.setattr(
        pipeline, "push_calendar_event_for_user",
        lambda db, user, extraction, row: calendar_pushed.append(user.email),
    )
    # ... rest unchanged
```

```python
def test_one_users_push_failure_does_not_block_another_users_push(db, monkeypatch):
    def flaky_calendar_push(db, user, extraction, row):
        if user.email == "broken@example.com":
            raise RuntimeError("revoked token")

    pushed_ok = []
    monkeypatch.setattr(pipeline, "push_calendar_event_for_user", lambda db, user, extraction, row: (
        flaky_calendar_push(db, user, extraction, row) or pushed_ok.append(user.email)
    ))
    # ... rest unchanged
```

Add new tests for the routing/lazy-creation logic:

```python
def test_get_or_create_event_calendar_creates_and_persists_on_first_call(db, monkeypatch):
    monkeypatch.setattr(pipeline, "create_secondary_calendar", lambda access_token, summary: "new-evt-cal")

    user = User(google_sub="evt1", email="evt1@example.com")
    db.add(user)
    db.commit()

    result = pipeline.get_or_create_event_calendar(db, user, "access-token")

    assert result == "new-evt-cal"
    assert user.event_calendar_id == "new-evt-cal"
    fetched = db.query(User).filter_by(id=user.id).one()
    assert fetched.event_calendar_id == "new-evt-cal"


def test_get_or_create_event_calendar_reuses_existing(db, monkeypatch):
    monkeypatch.setattr(
        pipeline, "create_secondary_calendar",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not create again")),
    )

    user = User(google_sub="evt2", email="evt2@example.com", event_calendar_id="existing-cal")
    db.add(user)
    db.commit()

    result = pipeline.get_or_create_event_calendar(db, user, "access-token")

    assert result == "existing-cal"


def test_get_or_create_event_calendar_returns_none_on_creation_failure(db, monkeypatch):
    monkeypatch.setattr(pipeline, "create_secondary_calendar", lambda access_token, summary: None)

    user = User(google_sub="evt3", email="evt3@example.com")
    db.add(user)
    db.commit()

    assert pipeline.get_or_create_event_calendar(db, user, "access-token") is None


def test_push_calendar_event_for_user_routes_deadline_category_to_calendar_id(db, monkeypatch):
    monkeypatch.setattr(pipeline, "get_access_token", lambda *a: "access-token")
    upserted = []
    monkeypatch.setattr(
        pipeline, "upsert_event",
        lambda access_token, calendar_id, event_id, summary, description, start_iso, end_iso: upserted.append(calendar_id),
    )

    user = User(
        google_sub="route1", email="route1@example.com",
        calendar_id="deadline-cal", calendar_refresh_token_encrypted="ct",
    )
    db.add(user)
    db.commit()

    from app.models import Post

    post = Post(wp_id=10, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    extraction = Extraction(company="Acme", category="new_listing", deadline="2027-01-01T00:00:00+05:30")

    pipeline.push_calendar_event_for_user(db, user, extraction, post)

    assert upserted == ["deadline-cal"]


def test_push_calendar_event_for_user_routes_event_category_to_event_calendar_id(db, monkeypatch):
    monkeypatch.setattr(pipeline, "get_access_token", lambda *a: "access-token")
    upserted = []
    monkeypatch.setattr(
        pipeline, "upsert_event",
        lambda access_token, calendar_id, event_id, summary, description, start_iso, end_iso: upserted.append(calendar_id),
    )

    user = User(
        google_sub="route2", email="route2@example.com",
        calendar_id="deadline-cal", event_calendar_id="events-cal", calendar_refresh_token_encrypted="ct",
    )
    db.add(user)
    db.commit()

    from app.models import Post

    post = Post(wp_id=11, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    extraction = Extraction(company="Acme", category="test_update", deadline="2027-01-01T00:00:00+05:30")

    pipeline.push_calendar_event_for_user(db, user, extraction, post)

    assert upserted == ["events-cal"]


def test_push_calendar_event_for_user_lazily_creates_event_calendar(db, monkeypatch):
    monkeypatch.setattr(pipeline, "get_access_token", lambda *a: "access-token")
    monkeypatch.setattr(pipeline, "create_secondary_calendar", lambda access_token, summary: "freshly-created-cal")
    upserted = []
    monkeypatch.setattr(
        pipeline, "upsert_event",
        lambda access_token, calendar_id, event_id, summary, description, start_iso, end_iso: upserted.append(calendar_id),
    )

    user = User(
        google_sub="route3", email="route3@example.com",
        calendar_id="deadline-cal", calendar_refresh_token_encrypted="ct",
    )
    db.add(user)
    db.commit()

    from app.models import Post

    post = Post(wp_id=12, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    extraction = Extraction(company="Acme", category="ppt", deadline="2027-01-01T00:00:00+05:30")

    pipeline.push_calendar_event_for_user(db, user, extraction, post)

    assert upserted == ["freshly-created-cal"]
    assert user.event_calendar_id == "freshly-created-cal"
```

Also update `push_to_all_users`'s internal call to pass `db` through — this is covered by Step 3's implementation; no test changes needed beyond the monkeypatch signature updates above, since those tests assert on behavior, not the exact call signature.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline.py -v`
Expected: the updated-signature tests currently FAIL with `TypeError: <lambda>() takes 3 positional arguments but 4 were given` (since `push_to_all_users` still calls the old 3-arg signature); the new `get_or_create_event_calendar` and routing tests FAIL with `AttributeError: module 'app.pipeline' has no attribute 'get_or_create_event_calendar'`.

- [ ] **Step 3: Implement**

In `app/pipeline.py`, update the import block (around line 17-26) to include the new category sets and `create_secondary_calendar`:

```python
from app.extraction import (
    CALENDAR_CATEGORIES,
    CALENDAR_EVENT_GROUP,
    DEADLINE_CALENDAR_CATEGORIES,
    EVENT_CALENDAR_CATEGORIES,
    NOTIFY_CATEGORIES,
    NOTIFY_EVENT_GROUP,
    PostCategory,
    dedup_key,
    extract_posting,
    make_llm_client,
)
from app.crypto import decrypt_token
from app.google_calendar import (
    create_secondary_calendar,
    event_id_for,
    event_id_for_company,
    get_access_token,
    upsert_event,
)
```

Add `get_or_create_event_calendar` and replace `push_calendar_event_for_user` (currently lines 194-212):

```python
def get_or_create_event_calendar(db: Session, user: User, access_token: str) -> str | None:
    """Lazily creates the user's secondary "Internblog Events" calendar on
    first use, rather than at login (app/auth.py::upsert_user_from_google) -
    this means users who signed in before this feature existed get it
    automatically on their next test/OA/PPT post, with no re-auth needed."""
    if user.event_calendar_id:
        return user.event_calendar_id
    calendar_id = create_secondary_calendar(access_token, "Internblog Events")
    if calendar_id is None:
        return None
    user.event_calendar_id = calendar_id
    db.commit()
    return calendar_id


def push_calendar_event_for_user(db: Session, user: User, extraction: Extraction, row: Post) -> None:
    refresh_token = decrypt_token(user.calendar_refresh_token_encrypted)
    access_token = get_access_token(
        settings.google_oauth_client_id, settings.google_oauth_client_secret, refresh_token
    )
    if access_token is None:
        return
    if extraction.category in {c.value for c in DEADLINE_CALENDAR_CATEGORIES}:
        calendar_id = user.calendar_id
    elif extraction.category in {c.value for c in EVENT_CALENDAR_CATEGORIES}:
        calendar_id = get_or_create_event_calendar(db, user, access_token)
    else:
        return
    if calendar_id is None:
        return
    summary = f"{extraction.company or 'Unknown company'}" + (f" - {extraction.role}" if extraction.role else "")
    group = CALENDAR_EVENT_GROUP.get(PostCategory(extraction.category))
    event_id = (
        event_id_for_company(extraction.company, group)
        if extraction.company and group
        else event_id_for(extraction.id)
    )
    upsert_event(
        access_token, calendar_id, event_id,
        summary=summary, description=row.link,
        start_iso=extraction.deadline, end_iso=extraction.deadline_end,
    )
```

Update the one call site in `push_to_all_users` (currently `push_calendar_event_for_user(user, extraction, row)`, around line 225):

```python
        if is_calendar_category and user.calendar_sync_enabled and user.calendar_refresh_token_encrypted:
            try:
                push_calendar_event_for_user(db, user, extraction, row)
            except Exception:
                logger.exception("calendar push failed for user %s", user.email)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: all PASS.

Then run the full suite to confirm nothing else regressed:

Run: `pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: route test/OA/PPT calendar pushes to a separate events calendar"
```

---

### Task 8: Admin portal lists registered users

**Files:**
- Modify: `app/dashboard.py` (`_user_row`, `render_dashboard` signature + new card)
- Modify: `app/main.py:327-377` (`admin_dashboard`)
- Test: `tests/test_main_pages.py`

**Interfaces:**
- Consumes: `app.models.User` fields (`email`, `name`, `created_at`, `calendar_sync_enabled`, `calendar_id`, `event_calendar_id`, `telegram_chat_id`) — all exist today.
- Produces: `render_dashboard(status: dict, upcoming: list[dict], recent: list[dict], total_extractions: int, users: list[dict]) -> str` (signature change: gains a required `users` parameter).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_main_pages.py`:

```python
def test_admin_dashboard_lists_registered_users(client, _fresh_db):
    _login_as(client, _fresh_db, "owner@example.com")
    db = _fresh_db()
    db.add(User(google_sub="listed", email="listed-user@example.com", name="Listed User"))
    db.commit()

    response = client.get("/admin")
    assert response.status_code == 200
    assert "listed-user@example.com" in response.text
    assert "Listed User" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main_pages.py -v -k test_admin_dashboard_lists_registered_users`
Expected: FAIL — `listed-user@example.com` not present in `response.text` (the admin page currently shows no user list).

- [ ] **Step 3: Implement**

In `app/dashboard.py`, add `_user_row` after `_extraction_row`:

```python
def _user_row(u: dict) -> str:
    return (
        f"<tr>"
        f"<td>{escape(u['email'])}</td>"
        f"<td>{escape(u['name'] or '-')}</td>"
        f"<td>{escape(_fmt_ts(u['created_at']))}</td>"
        f"<td>{'Yes' if u['calendar_sync_enabled'] else 'No'}</td>"
        f"<td>{'Yes' if u['has_calendar'] else 'No'}</td>"
        f"<td>{'Yes' if u['has_event_calendar'] else 'No'}</td>"
        f"<td>{'Yes' if u['telegram_connected'] else 'No'}</td>"
        f"</tr>"
    )
```

Update `render_dashboard`'s signature and body — add `users: list[dict]` as a new parameter, build `user_rows`, and add a new card before the closing `</div>` of `.wrap`:

```python
def render_dashboard(
    status: dict, upcoming: list[dict], recent: list[dict], total_extractions: int, users: list[dict]
) -> str:
    session = status.get("session") or {}
    last_fetch = status.get("last_fetch") or {}
    counts = status.get("counts") or {}

    upcoming_rows = "\n".join(_extraction_row(r) for r in upcoming) or (
        '<tr><td colspan="5" style="text-align:center;color:#666">Nothing upcoming</td></tr>'
    )
    recent_rows = "\n".join(_extraction_row(r, show_posted=True) for r in recent) or (
        '<tr><td colspan="6" style="text-align:center;color:#666">No extractions yet</td></tr>'
    )
    user_rows = "\n".join(_user_row(u) for u in users) or (
        '<tr><td colspan="7" style="text-align:center;color:#666">No registered users</td></tr>'
    )
```

(keep the rest of the function body identical up through the `<h2>Database ...</h2>` table's closing `</div>`, then add a new card immediately before the final `</div>\n</body>\n</html>"""`):

```python
<h2>Database <span class="count">({total_extractions} extractions total, newest post first)</span></h2>
<div class="scroll">
<table>
<thead><tr><th>Category</th><th>Company</th><th>Role</th><th>Deadline</th><th>Link</th><th>Posted</th></tr></thead>
<tbody>
{recent_rows}
</tbody>
</table>
</div>

<h2>Registered users <span class="count">({len(users)})</span></h2>
<div class="scroll">
<table>
<thead><tr><th>Email</th><th>Name</th><th>Joined</th><th>Sync</th><th>Deadlines cal</th><th>Events cal</th><th>Telegram</th></tr></thead>
<tbody>
{user_rows}
</tbody>
</table>
</div>

<p style="color:#999;font-size:0.8rem;margin-top:2rem">Auto-refreshes every 60s. Calendar feed (token not shown here): /calendar/&lt;token&gt;.ics · Health JSON: /health</p>
</body>
</html>"""
```

In `app/main.py`, update `admin_dashboard()` (around line 327-377) to build and pass `users`:

```python
@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(user: User = Depends(get_current_user_or_redirect)) -> str:
    if not auth.is_admin(user):
        raise HTTPException(status_code=404)
    status = _compute_status()
    with SessionLocal() as db:
        total_extractions = db.scalar(select(func.count(Extraction.id))) or 0

        users = [
            {
                "email": u.email,
                "name": u.name,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "calendar_sync_enabled": u.calendar_sync_enabled,
                "has_calendar": u.calendar_id is not None,
                "has_event_calendar": u.event_calendar_id is not None,
                "telegram_connected": u.telegram_chat_id is not None,
            }
            for u in db.query(User).all()
        ]

        # ... (calendar_rows / upcoming / recent_rows_query / recent unchanged from Task 4)

    return render_dashboard(status, upcoming, recent, total_extractions, users)
```

(the `calendar_rows`, `upcoming`, `recent_rows`, `recent` blocks stay exactly as produced by Task 4 — only the `users` query/list and the final `render_dashboard(...)` call change here.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main_pages.py -v`
Expected: all PASS.

Then run the full suite one final time:

Run: `pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/dashboard.py app/main.py tests/test_main_pages.py
git commit -m "feat: list registered users on the admin dashboard"
```
