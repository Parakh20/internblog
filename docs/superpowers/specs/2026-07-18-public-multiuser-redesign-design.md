# Public multi-user site: Google login, per-user Calendar push, Telegram opt-in, redesign

## Context

internblog is currently a single-user personal tool: one Telegram chat ID
and one Google Calendar refresh token, both hardcoded in `.env`, receive
every extracted internship posting. The dashboard at `/` is an
unauthenticated status page (session health, fetch logs, raw extraction
table).

Goal: turn this into a public site anyone can visit, sign in to with their
own Google account, and have the same shared internship-posting data pushed
into *their own* Google Calendar, with an optional Telegram chat ID for
notifications. The whole site requires login. The existing admin/status
dashboard becomes visible only to the owner's account
(`sharmaparakh05@gmail.com`). The public-facing pages get a visual redesign
(clean modern SaaS look) and gain a calendar view as the primary page.

## Non-goals

- No personalized filtering of postings by branch/CGPA — every signed-in
  user gets the same shared events pushed to them.
- No new Telegram bot commands/deep-linking — chat ID entry stays manual
  (paste into a settings field), same discovery flow as today
  (`getUpdates`).
- The existing ICS feed (`/calendar/<token>.ics`) is unaffected and stays
  as-is; it is not part of this redesign and not linked from the new public
  pages.
- No changes to the scraping/extraction pipeline's categorization logic.

## Data model changes

New tables in `app/models.py`:

```python
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    picture_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    calendar_refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    calendar_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calendar_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # random token
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

`TelegramNotification` and calendar event-id grouping stay per-`(company,
group)` but are now scoped per user (see Pipeline changes below) — no schema
change needed there beyond what already exists, since the grouping key
logic is reused unmodified per user.

Migration: a one-off script (`scripts/migrate_owner_to_users_table.py`)
creates the `users` row for `sharmaparakh05@gmail.com` from the existing
`GOOGLE_CALENDAR_REFRESH_TOKEN` / `GOOGLE_CALENDAR_ID` /
`TELEGRAM_CHAT_ID` settings, encrypting the refresh token before storing it.
After this runs once, `GOOGLE_CALENDAR_REFRESH_TOKEN`,
`GOOGLE_CALENDAR_ID`, and `TELEGRAM_CHAT_ID` are removed from `.env`/
`config.py` — there is only one code path (the per-user one) afterward.

## New settings (`app/config.py`)

```python
google_oauth_client_id: str = ""
google_oauth_client_secret: str = ""
oauth_redirect_base_url: str = ""      # e.g. https://your-hostname.duckdns.org
secret_encryption_key: str = ""        # Fernet key, `Fernet.generate_key()`
owner_email: str = "sharmaparakh05@gmail.com"
session_cookie_name: str = "internblog_session"
session_ttl_days: int = 30
```

`google_calendar_client_id`/`_secret` are renamed/reused as
`google_oauth_client_id`/`_secret` — same Cloud project, now also used for
login (scopes: `openid email profile
https://www.googleapis.com/auth/calendar`).

## Auth flow

Library: **Authlib** (`authlib.integrations.starlette_client.OAuth`), added
as a new dependency. Handles PKCE, `state` CSRF verification, and token
exchange without hand-rolling any of that.

- `GET /login` — renders a minimal centered card: "Sign in with Google"
  button + one line explaining what's requested (calendar access, to sync
  deadlines) and why. Redirects into Google's consent screen requesting
  `openid email profile https://www.googleapis.com/auth/calendar` in one
  combined consent.
- `GET /auth/callback` — exchanges the code for tokens; upserts the `User`
  row by `google_sub`; on first login, calls the Calendar API to create a
  dedicated "Internblog Deadlines" secondary calendar (same call the
  existing admin setup already made manually) and stores its `calendar_id`;
  encrypts and stores the refresh token; creates a `Session` row;
  sets an `httponly`, `secure`, `samesite=lax` cookie holding the session
  id; redirects to `/`.
- `GET /logout` — deletes the `Session` row, clears the cookie, redirects to
  `/login`.
- `get_current_user` dependency (`app/auth.py`) — reads the cookie, loads
  and validates the `Session` (checks `expires_at`), loads the `User`;
  raises a redirect-to-`/login` response if missing/expired. Applied to
  every route except `/login`, `/auth/callback`, `/health`, and the
  existing `/calendar/<token>.ics`.

Refresh-token encryption: `app/crypto.py` wraps `cryptography.fernet.Fernet`
with `encrypt_token(plaintext) -> str` / `decrypt_token(ciphertext) -> str`,
keyed by `settings.secret_encryption_key`. `google_calendar.py`'s
`get_access_token` call sites decrypt just before use, never holding
plaintext beyond that call.

## Admin gating

- `is_admin = current_user.email == settings.owner_email`.
- `GET /admin` — renders the existing `render_dashboard` output (session
  health, fetch logs, DB extraction table) unchanged, gated: 404 (not 403)
  for any non-admin user, so the route's existence isn't confirmed to
  strangers.
- `GET /` — becomes the new calendar/upcoming-events view for every
  logged-in user (admin included; the admin also gets a small "Admin
  dashboard" link in the nav, visible only to them).

## Pipeline changes (`app/pipeline.py`)

`run_extraction`'s notify/calendar-push block changes from single-target to
a loop over opted-in users:

```python
def push_to_all_users(db: Session, extraction: Extraction, row: Post) -> None:
    is_stale = ...  # unchanged
    users = db.query(User).all()
    for user in users:
        if user.calendar_sync_enabled and user.calendar_refresh_token_encrypted:
            push_calendar_event_for_user(user, extraction, row)
        if user.telegram_chat_id:
            send_or_edit_telegram_for_user(db, user, extraction, message)
```

- `push_calendar_event_for_user` mirrors today's `push_calendar_event`, but
  takes `user.calendar_id` and a decrypted `user.calendar_refresh_token`
  instead of reading `settings.google_calendar_*`.
- `send_or_edit_telegram_for_user` mirrors today's
  `send_or_edit_telegram`, but the `TelegramNotification.group_key` becomes
  `f"{user.id}|{company}|{group}"` so each user's own edit-in-place tracking
  is independent (one user's deadline-extension edit shouldn't touch
  another user's message).
- `TelegramNotification.group_key` column stays `String(64)` — the added
  `user.id` prefix fits comfortably within a SHA1-length-based hash if
  needed, or the existing plain `company|group` string form for short
  keys; either way, uniqueness is still enforced by the existing unique
  index.
- `settings.telegram_bot_token` remains a single global bot token (one bot,
  many chat IDs) — only `chat_id` is per-user.
- Per-user failures (a revoked refresh token, a bad chat ID) are logged and
  skipped; they never abort the loop for other users or the fetch cycle
  itself.

## New public pages (clean modern SaaS style, server-rendered HTML/CSS, no JS framework)

Consistent with the existing codebase's approach (`app/dashboard.py`:
plain f-string HTML, inline `<style>`, no templating engine, no JS
framework) — the redesign keeps that approach rather than introducing one.

- **`/login`**: centered card, logo/wordmark, "Sign in with Google" button,
  one-line scope explanation.
- **`/` (logged in)**: top nav (wordmark, user avatar/email, "Admin"
  link if `is_admin`, "Sign out"); a server-rendered month calendar grid
  (`<table>`-based, no JS calendar library) with deadline chips color-coded
  by category, generated from the same `upcoming` query
  `main.py` already runs; below it, an "Upcoming" card list (same data,
  list form); a settings panel (Telegram chat ID input + save button,
  calendar-sync on/off toggle, a status line showing sync is active and
  which calendar it targets).
- **`/settings`** (POST target for the settings panel): validates the
  pasted Telegram chat ID is non-empty and numeric-ish, saves it to
  `current_user.telegram_chat_id`, toggles `calendar_sync_enabled`;
  rate-limited per session (simple in-memory token bucket keyed by
  session id — this app runs a single process today, so no shared cache
  needed) to prevent spamming saves.
- **`/admin`**: existing dashboard content, moved here unchanged, gated to
  `is_admin`.
- Visual language: light theme, single accent color (indigo), card-based
  sections, subtle shadows/radius, generous whitespace — matches the
  approved "clean modern SaaS" direction.

## New dependencies

- `authlib` — OAuth/OIDC client.
- `cryptography` — Fernet encryption for stored refresh tokens.

## Testing

- `tests/test_auth.py`: session creation/expiry, `get_current_user`
  dependency behavior (missing cookie, expired session), admin-gating logic
  (`is_admin` check on `/admin` returns 404 for non-owner).
- `tests/test_pipeline.py` (extend existing): multi-user push loop —
  opted-in users receive calendar/Telegram pushes, opted-out users don't,
  one user's failure doesn't block another's push.
- `tests/test_crypto.py`: encrypt/decrypt round-trip for refresh tokens.
- Auth callback and real Google endpoints are not hit in tests — mock
  Authlib's token exchange and the Calendar API calls, consistent with how
  `google_calendar.py`/`notifications.py` are already tested via
  `httpx`-mocking today.

## Deployment notes

- New env vars: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
  `OAUTH_REDIRECT_BASE_URL`, `SECRET_ENCRYPTION_KEY`, `OWNER_EMAIL`
  (defaults to `sharmaparakh05@gmail.com`).
- The Google Cloud OAuth consent screen must be moved from "Testing" to
  "Production" (or have test users added) since arbitrary Google accounts
  will now sign in — this is an external configuration step, not code, but
  needs doing before this goes live per `docs/deployment.md`.
- Run `scripts/migrate_owner_to_users_table.py` once during deploy, before
  removing the old single-user env vars.
