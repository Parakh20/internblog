# Public multi-user login, per-user Calendar/Telegram push, redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn internblog from a single-user personal tool into a public site: visitors sign in with Google, get the same shared internship postings pushed into their own Google Calendar and (optionally) their own Telegram chat, and the existing admin/status dashboard becomes visible only to `sharmaparakh05@gmail.com`.

**Architecture:** Server-side sessions (random token in an httponly cookie, backed by a new `sessions` table) plus a new `users` table storing each signed-in visitor's Google identity, encrypted Calendar refresh token, target calendar id, and optional Telegram chat id. `app/pipeline.py`'s single-target push becomes a loop over opted-in users. New server-rendered pages (`/login`, `/`, `/settings`, `/admin`) follow the existing plain-HTML-f-string style already used in `app/dashboard.py` — no template engine, no JS framework.

**Tech Stack:** FastAPI, SQLAlchemy, Authlib (`authlib.integrations.httpx_client.OAuth2Client`) for the OAuth code exchange, `cryptography.fernet` for refresh-token encryption at rest (already an installed transitive dependency — verified via `python3 -c "import cryptography"`).

## Global Constraints

- The whole site requires login except `/login`, `/auth/start`, `/auth/callback`, `/health`, and `/calendar/{token}.ics` (spec: "Auth flow").
- `/admin` returns 404 (not 403) for any non-owner user (spec: "Admin gating").
- Owner email defaults to `sharmaparakh05@gmail.com` via a new `owner_email` setting (spec: "New settings").
- Refresh tokens are never stored in plaintext; always encrypted with Fernet before hitting the DB (spec: "Auth flow" / crypto).
- No personalized filtering by branch/CGPA — every signed-in user gets the same shared events (spec: "Non-goals").
- The ICS feed (`/calendar/{token}.ics`) is untouched (spec: "Non-goals").
- One code path for calendar/Telegram push (per-user), not two — the old single-target `settings.google_calendar_*`/`settings.telegram_chat_id` path is retired once the migration script runs (spec: "Data model changes").

---

### Task 1: Refresh-token encryption helper

**Files:**
- Create: `app/crypto.py`
- Modify: `app/config.py` (add `secret_encryption_key` setting)
- Modify: `.env.example` (document `SECRET_ENCRYPTION_KEY`)
- Test: `tests/test_crypto.py`

**Interfaces:**
- Produces: `encrypt_token(plaintext: str) -> str`, `decrypt_token(ciphertext: str) -> str`, both in `app/crypto.py`, both reading `settings.secret_encryption_key` at call time (not at import time, so tests can monkeypatch `settings`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_crypto.py
import pytest

from app import crypto
from app.config import settings


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings, "secret_encryption_key", Fernet.generate_key().decode())


def test_encrypt_then_decrypt_round_trips():
    ciphertext = crypto.encrypt_token("my-refresh-token")
    assert crypto.decrypt_token(ciphertext) == "my-refresh-token"


def test_ciphertext_does_not_contain_plaintext():
    ciphertext = crypto.encrypt_token("my-refresh-token")
    assert "my-refresh-token" not in ciphertext


def test_decrypt_with_wrong_key_raises(monkeypatch):
    from cryptography.fernet import Fernet, InvalidToken

    ciphertext = crypto.encrypt_token("my-refresh-token")
    monkeypatch.setattr(settings, "secret_encryption_key", Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        crypto.decrypt_token(ciphertext)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_crypto.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.crypto'`

- [ ] **Step 3: Add the setting**

In `app/config.py`, add inside `class Settings` (near the other security-relevant settings, after `calendar_feed_token`):

```python
    # Fernet key (generate with `python3 -c "from cryptography.fernet import
    # Fernet; print(Fernet.generate_key().decode())"`) used to encrypt each
    # user's Google Calendar refresh token at rest.
    secret_encryption_key: str = ""
```

- [ ] **Step 4: Write the implementation**

```python
# app/crypto.py
"""Encrypt/decrypt secrets (Google Calendar refresh tokens) at rest, using
a Fernet key from settings. Refresh tokens are long-lived credentials for a
user's personal calendar - storing them in plaintext would be a real
exposure if the database ever leaked."""

from cryptography.fernet import Fernet

from app.config import settings


def _fernet() -> Fernet:
    return Fernet(settings.secret_encryption_key.encode())


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_crypto.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Document the new env var**

In `.env.example`, add after the `CALENDAR_FEED_TOKEN` block:

```bash
# Fernet key used to encrypt each signed-in user's Google Calendar refresh
# token at rest. Generate with:
#   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
SECRET_ENCRYPTION_KEY=
```

- [ ] **Step 7: Commit**

```bash
git add app/crypto.py app/config.py .env.example tests/test_crypto.py
git commit -m "feat: add Fernet-based encryption helper for stored refresh tokens"
```

---

### Task 2: `User` and `Session` models

**Files:**
- Modify: `app/models.py` (add `User`, `Session` classes)
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `Base`, `utcnow` from `app/models.py` (already defined).
- Produces: `User` (fields: `id`, `google_sub`, `email`, `name`, `picture_url`, `calendar_refresh_token_encrypted`, `calendar_id`, `calendar_sync_enabled`, `telegram_chat_id`, `created_at`) and `Session` (fields: `id` [str primary key], `user_id`, `created_at`, `expires_at`), both importable as `from app.models import Session as SessionRow, User` in later tasks (aliased to avoid clashing with SQLAlchemy's own `Session` import elsewhere).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Session as SessionRow, User


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_user_round_trips_all_fields():
    db = _db()
    user = User(
        google_sub="sub-123",
        email="someone@example.com",
        name="Someone",
        picture_url="https://example.com/pic.jpg",
        calendar_refresh_token_encrypted="ciphertext",
        calendar_id="cal-abc",
        calendar_sync_enabled=True,
        telegram_chat_id="12345",
    )
    db.add(user)
    db.commit()

    fetched = db.query(User).filter_by(google_sub="sub-123").one()
    assert fetched.email == "someone@example.com"
    assert fetched.calendar_sync_enabled is True
    assert fetched.telegram_chat_id == "12345"


def test_user_email_and_google_sub_are_unique():
    db = _db()
    db.add(User(google_sub="dup", email="a@example.com"))
    db.commit()
    db.add(User(google_sub="dup", email="b@example.com"))
    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db.commit()


def test_session_round_trips_and_links_to_user():
    db = _db()
    user = User(google_sub="sub-456", email="x@example.com")
    db.add(user)
    db.commit()

    session_row = SessionRow(
        id="tok-abc",
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(session_row)
    db.commit()

    fetched = db.query(SessionRow).filter_by(id="tok-abc").one()
    assert fetched.user_id == user.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'User' from 'app.models'`

- [ ] **Step 3: Write the implementation**

In `app/models.py`, add after the `FetchLog` class (end of file):

```python
class User(Base):
    """A visitor who signed in with Google. calendar_refresh_token_encrypted
    and calendar_id are set on first login (see app/auth.py), when we also
    create their dedicated "Internblog Deadlines" secondary calendar."""

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
    """Server-side session, looked up by the opaque token stored in the
    session cookie. Revocable (unlike a JWT) - logging out deletes the row,
    and any session can be killed independently of any other."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat: add User and Session models for multi-user login"
```

---

### Task 3: OAuth/session settings and Calendar helper for new-user setup

**Files:**
- Modify: `app/config.py` (add OAuth/session settings)
- Modify: `app/google_calendar.py` (add `create_secondary_calendar`)
- Modify: `.env.example`
- Test: `tests/test_google_calendar.py` (extend)

**Interfaces:**
- Produces: `settings.google_oauth_client_id`, `settings.google_oauth_client_secret`, `settings.oauth_redirect_base_url`, `settings.owner_email`, `settings.session_cookie_name`, `settings.session_ttl_days`; `create_secondary_calendar(access_token: str, summary: str = "Internblog Deadlines") -> str | None` in `app/google_calendar.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_google_calendar.py
import httpx

from app.google_calendar import create_secondary_calendar


def test_create_secondary_calendar_returns_new_calendar_id(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        assert url == "https://www.googleapis.com/calendar/v3/calendars"
        assert json == {"summary": "Internblog Deadlines"}
        return httpx.Response(200, json={"id": "new-cal-id"})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert create_secondary_calendar("access-token") == "new-cal-id"


def test_create_secondary_calendar_returns_none_on_error(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return httpx.Response(500, json={"error": "boom"})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert create_secondary_calendar("access-token") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_google_calendar.py -v`
Expected: FAIL with `ImportError: cannot import name 'create_secondary_calendar'`

- [ ] **Step 3: Add the settings**

In `app/config.py`, add inside `class Settings`, after the existing `google_calendar_*` block:

```python
    # OAuth client used for both "Sign in with Google" and Calendar API
    # access - same Cloud project as the original single-user setup, now
    # also handling public login. Scopes requested: openid email profile
    # https://www.googleapis.com/auth/calendar (see app/auth.py).
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    # Public base URL this app is served at, e.g. https://your-host.duckdns.org
    # - needed to build the OAuth redirect_uri without hardcoding a host.
    oauth_redirect_base_url: str = ""

    # Only this email sees /admin (the session/fetch-log/DB dashboard).
    owner_email: str = "sharmaparakh05@gmail.com"

    session_cookie_name: str = "internblog_session"
    session_ttl_days: int = 30
```

- [ ] **Step 4: Add `create_secondary_calendar`**

In `app/google_calendar.py`, add after `get_access_token`:

```python
CALENDARS_ENDPOINT = "https://www.googleapis.com/calendar/v3/calendars"


def create_secondary_calendar(access_token: str, summary: str = "Internblog Deadlines") -> str | None:
    """Create a dedicated secondary calendar for a newly signed-in user, so
    their internship deadlines don't mix into their primary calendar
    (lectures, personal events, etc). Called once, on first login."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = httpx.post(
            CALENDARS_ENDPOINT, headers=headers, json={"summary": summary}, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json()["id"]
    except httpx.HTTPError:
        logger.exception("failed to create secondary calendar %r", summary)
        return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_google_calendar.py -v`
Expected: PASS (all tests, including the 2 new ones)

- [ ] **Step 6: Document new env vars**

In `.env.example`, replace the comment above `GOOGLE_CALENDAR_CLIENT_ID` with:

```bash
# OAuth client for "Sign in with Google" + Calendar API access (same
# Cloud project as before, now used for public login too). Create an OAuth
# "Web application" client with an authorized redirect URI of
# <OAUTH_REDIRECT_BASE_URL>/auth/callback.
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
OAUTH_REDIRECT_BASE_URL=https://your-hostname.duckdns.org
OWNER_EMAIL=sharmaparakh05@gmail.com
```

and remove the now-superseded `GOOGLE_CALENDAR_CLIENT_ID`/`GOOGLE_CALENDAR_CLIENT_SECRET`/`GOOGLE_CALENDAR_REFRESH_TOKEN`/`GOOGLE_CALENDAR_ID` lines (kept in git history; Task 8's migration script is what actually retires the settings in code).

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/google_calendar.py .env.example tests/test_google_calendar.py
git commit -m "feat: add OAuth/session settings and per-user secondary calendar creation"
```

---

### Task 4: `app/auth.py` — OAuth exchange and session management

**Files:**
- Create: `app/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `settings` from `app/config.py`; `User`, `Session as SessionRow` from `app/models.py`; `create_secondary_calendar` from `app/google_calendar.py`.
- Produces: `build_authorization_url() -> tuple[str, str]` (url, state); `exchange_code_for_tokens(code: str) -> dict` (keys: `google_sub`, `email`, `name`, `picture`, `refresh_token`); `upsert_user_from_google(db, profile: dict) -> User`; `create_session(db, user_id: int) -> str`; `get_session_user(db, session_id: str | None) -> User | None`; `delete_session(db, session_id: str | None) -> None`; `is_admin(user: User) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth.py
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth
from app.config import settings
from app.models import Base, Session as SessionRow, User


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_upsert_user_from_google_creates_new_user_and_calendar(db, monkeypatch):
    monkeypatch.setattr(auth, "create_secondary_calendar", lambda access_token: "new-cal-id")
    profile = {
        "google_sub": "sub-1",
        "email": "a@example.com",
        "name": "A",
        "picture": "https://x/pic.jpg",
        "refresh_token": "rt-1",
        "access_token": "at-1",
    }
    user = auth.upsert_user_from_google(db, profile)

    assert user.google_sub == "sub-1"
    assert user.calendar_id == "new-cal-id"
    from app.crypto import decrypt_token

    assert decrypt_token(user.calendar_refresh_token_encrypted) == "rt-1"


def test_upsert_user_from_google_updates_existing_user_without_recreating_calendar(db, monkeypatch):
    created_calendars = []
    monkeypatch.setattr(
        auth, "create_secondary_calendar", lambda access_token: created_calendars.append(1) or "should-not-be-used"
    )
    existing = User(
        google_sub="sub-2", email="old@example.com", calendar_id="existing-cal",
        calendar_refresh_token_encrypted="ignored",
    )
    db.add(existing)
    db.commit()

    profile = {
        "google_sub": "sub-2", "email": "new@example.com", "name": "B",
        "picture": None, "refresh_token": None, "access_token": "at-2",
    }
    user = auth.upsert_user_from_google(db, profile)

    assert user.email == "new@example.com"
    assert user.calendar_id == "existing-cal"
    assert created_calendars == []


def test_create_and_get_session_round_trips(db):
    user = User(google_sub="sub-3", email="c@example.com")
    db.add(user)
    db.commit()

    session_id = auth.create_session(db, user.id)
    fetched = auth.get_session_user(db, session_id)

    assert fetched is not None
    assert fetched.id == user.id


def test_get_session_user_returns_none_for_missing_or_expired(db):
    assert auth.get_session_user(db, None) is None
    assert auth.get_session_user(db, "nonexistent") is None

    user = User(google_sub="sub-4", email="d@example.com")
    db.add(user)
    db.commit()
    db.add(SessionRow(id="expired", user_id=user.id, expires_at=datetime.now(timezone.utc) - timedelta(days=1)))
    db.commit()

    assert auth.get_session_user(db, "expired") is None


def test_delete_session_removes_row(db):
    user = User(google_sub="sub-5", email="e@example.com")
    db.add(user)
    db.commit()
    session_id = auth.create_session(db, user.id)

    auth.delete_session(db, session_id)

    assert auth.get_session_user(db, session_id) is None


def test_is_admin_matches_owner_email_only(monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    assert auth.is_admin(User(email="owner@example.com"))
    assert not auth.is_admin(User(email="someone-else@example.com"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth'`

- [ ] **Step 3: Write the implementation**

```python
# app/auth.py
"""Google OAuth login (identity + Calendar scope) and server-side session
management. Uses Authlib's plain OAuth2Client (not the Starlette
integration) since this app manages its own session cookie/table rather
than Starlette's SessionMiddleware."""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from authlib.integrations.httpx_client import OAuth2Client
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.crypto import encrypt_token
from app.google_calendar import create_secondary_calendar
from app.models import Session as SessionRow, User

logger = logging.getLogger(__name__)

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPE = "openid email profile https://www.googleapis.com/auth/calendar"


def _redirect_uri() -> str:
    return f"{settings.oauth_redirect_base_url}/auth/callback"


def build_authorization_url() -> tuple[str, str]:
    """Returns (authorization_url, state). The caller stores state in a
    short-lived cookie and must verify it on /auth/callback (CSRF guard)."""
    client = OAuth2Client(
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        redirect_uri=_redirect_uri(),
        scope=SCOPE,
    )
    return client.create_authorization_url(
        AUTHORIZATION_ENDPOINT,
        access_type="offline",  # required to get a refresh_token
        prompt="consent",       # required every time to guarantee a refresh_token is reissued
    )


def exchange_code_for_tokens(code: str) -> dict:
    """Exchange an authorization code for tokens, then fetch the OpenID
    Connect profile. refresh_token may be None if Google didn't issue one."""
    client = OAuth2Client(
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        redirect_uri=_redirect_uri(),
    )
    token = client.fetch_token(TOKEN_ENDPOINT, code=code)
    userinfo = client.get(USERINFO_ENDPOINT).json()
    return {
        "google_sub": userinfo["sub"],
        "email": userinfo["email"],
        "name": userinfo.get("name"),
        "picture": userinfo.get("picture"),
        "refresh_token": token.get("refresh_token"),
        "access_token": token["access_token"],
    }


def upsert_user_from_google(db: DBSession, profile: dict) -> User:
    """Create or update the User row for this Google identity. On first
    login (no calendar_id yet), creates their dedicated secondary calendar
    using the access token from this same consent."""
    user = db.query(User).filter_by(google_sub=profile["google_sub"]).one_or_none()
    if user is None:
        user = User(google_sub=profile["google_sub"], email=profile["email"])
        db.add(user)

    user.email = profile["email"]
    user.name = profile.get("name")
    user.picture_url = profile.get("picture")
    if profile.get("refresh_token"):
        user.calendar_refresh_token_encrypted = encrypt_token(profile["refresh_token"])
    if user.calendar_id is None:
        user.calendar_id = create_secondary_calendar(profile["access_token"])

    db.commit()
    return user


def create_session(db: DBSession, user_id: int) -> str:
    session_id = secrets.token_urlsafe(32)
    db.add(
        SessionRow(
            id=session_id,
            user_id=user_id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.session_ttl_days),
        )
    )
    db.commit()
    return session_id


def get_session_user(db: DBSession, session_id: str | None) -> User | None:
    if not session_id:
        return None
    row = db.query(SessionRow).filter_by(id=session_id).one_or_none()
    if row is None or row.expires_at < datetime.now(timezone.utc):
        return None
    return db.query(User).filter_by(id=row.user_id).one_or_none()


def delete_session(db: DBSession, session_id: str | None) -> None:
    if not session_id:
        return
    db.query(SessionRow).filter_by(id=session_id).delete()
    db.commit()


def is_admin(user: User) -> bool:
    return user.email == settings.owner_email
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Add the new dependency**

In `requirements.txt`, add:

```
authlib>=1.3
```

Run: `pip install authlib>=1.3`

- [ ] **Step 6: Commit**

```bash
git add app/auth.py requirements.txt tests/test_auth.py
git commit -m "feat: add Google OAuth exchange and server-side session management"
```

---

### Task 5: Wire login/callback/logout routes and the auth gate into `main.py`

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_main_auth.py`

**Interfaces:**
- Consumes: everything from `app/auth.py` (Task 4).
- Produces: `get_current_user_or_redirect(request: Request) -> User` FastAPI dependency in `app/main.py`, used by every gated route added in this and later tasks. Routes: `GET /auth/start` (begins the Google OAuth redirect — Task 7 adds the actual `/login` card page that links here), `GET /auth/callback`, `GET /logout`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_main_auth.py
import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.db import SessionLocal, init_db


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path}/test.db")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(settings.database_url, future=True)
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False, future=True))
    monkeypatch.setattr("app.db.engine", engine)
    init_db()


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def test_auth_start_redirects_to_google(client, monkeypatch):
    monkeypatch.setattr(auth, "build_authorization_url", lambda: ("https://accounts.google.com/fake", "state123"))
    response = client.get("/auth/start", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://accounts.google.com/fake"
    assert "internblog_oauth_state" in response.cookies


def test_callback_rejects_mismatched_state(client):
    response = client.get(
        "/auth/callback?code=abc&state=wrong",
        cookies={"internblog_oauth_state": "expected"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_callback_creates_session_and_redirects_home(client, monkeypatch):
    monkeypatch.setattr(auth, "exchange_code_for_tokens", lambda code: {
        "google_sub": "sub-x", "email": "x@example.com", "name": "X",
        "picture": None, "refresh_token": "rt", "access_token": "at",
    })
    monkeypatch.setattr(auth, "create_secondary_calendar", lambda access_token: "cal-x")

    response = client.get(
        "/auth/callback?code=abc&state=expected",
        cookies={"internblog_oauth_state": "expected"},
        follow_redirects=False,
    )
    assert response.status_code == 307
    assert response.headers["location"] == "/"
    assert main.settings.session_cookie_name in response.cookies


def test_logout_clears_session(client, monkeypatch):
    monkeypatch.setattr(auth, "exchange_code_for_tokens", lambda code: {
        "google_sub": "sub-y", "email": "y@example.com", "name": "Y",
        "picture": None, "refresh_token": "rt", "access_token": "at",
    })
    monkeypatch.setattr(auth, "create_secondary_calendar", lambda access_token: "cal-y")
    login_response = client.get(
        "/auth/callback?code=abc&state=expected",
        cookies={"internblog_oauth_state": "expected"},
        follow_redirects=False,
    )
    session_cookie = login_response.cookies[main.settings.session_cookie_name]

    logout_response = client.get(
        "/logout", cookies={main.settings.session_cookie_name: session_cookie}, follow_redirects=False
    )
    assert logout_response.status_code == 307
    assert logout_response.headers["location"] == "/login"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main_auth.py -v`
Expected: FAIL — `/auth/start` doesn't exist yet (404)

- [ ] **Step 3: Write the implementation**

In `app/main.py`, add these imports near the top (alongside the existing ones):

```python
import secrets

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse

from app import auth
from app.models import User
```

Add after the `app = FastAPI(...)` line and before `_compute_status`:

```python
OAUTH_STATE_COOKIE = "internblog_oauth_state"


def get_current_user_or_redirect(request: Request) -> User:
    session_id = request.cookies.get(settings.session_cookie_name)
    with SessionLocal() as db:
        user = auth.get_session_user(db, session_id)
    if user is None:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return user


@app.get("/auth/start")
def auth_start() -> Response:
    """Begins the Google OAuth redirect. The /login page (added in Task 7)
    links here rather than redirecting to Google directly, so a visitor
    sees an explanatory card first."""
    url, state = auth.build_authorization_url()
    response = RedirectResponse(url, status_code=307)
    response.set_cookie(OAUTH_STATE_COOKIE, state, httponly=True, secure=True, samesite="lax", max_age=600)
    return response


@app.get("/auth/callback")
def auth_callback(request: Request, code: str, state: str) -> Response:
    expected_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not expected_state or not secrets.compare_digest(expected_state, state):
        raise HTTPException(status_code=400, detail="invalid oauth state")

    profile = auth.exchange_code_for_tokens(code)
    with SessionLocal() as db:
        user = auth.upsert_user_from_google(db, profile)
        session_id = auth.create_session(db, user.id)

    response = RedirectResponse("/", status_code=307)
    response.delete_cookie(OAUTH_STATE_COOKIE)
    response.set_cookie(
        settings.session_cookie_name, session_id, httponly=True, secure=True, samesite="lax",
        max_age=settings.session_ttl_days * 86400,
    )
    return response


@app.get("/logout")
def logout(request: Request) -> Response:
    session_id = request.cookies.get(settings.session_cookie_name)
    with SessionLocal() as db:
        auth.delete_session(db, session_id)
    response = RedirectResponse("/login", status_code=307)
    response.delete_cookie(settings.session_cookie_name)
    return response
```

Note: `get_current_user_or_redirect` is not wired to any route yet in this
task, and there is no `/login` route yet either — `/` still uses the old
unauthenticated `dashboard()` view, and `logout`'s redirect target
(`/login`) doesn't exist until Task 7 adds it. That's fine: this task's
own tests only exercise `/auth/start`, `/auth/callback`, and `/logout`,
none of which require `/login` to exist yet (a 404 on the eventual
redirect target doesn't fail a test that only checks the `Location`
header value). Task 7 adds `/login` and wires `get_current_user_or_redirect`
into `/`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main_auth.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_main_auth.py
git commit -m "feat: add /login, /auth/callback, /logout routes and session cookie handling"
```

---

### Task 6: Multi-user pipeline push (Calendar + Telegram)

**Files:**
- Modify: `app/pipeline.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `User` from `app/models.py`; `create_secondary_calendar` not needed here (only used at login); `decrypt_token` from `app/crypto.py`.
- Produces: `push_to_all_users(db: Session, extraction: Extraction, row: Post) -> None` replacing the old single-target calls inside `run_extraction`; `push_calendar_event_for_user(user: User, extraction: Extraction, row: Post) -> None`; `send_or_edit_telegram_for_user(db: Session, user: User, extraction: Extraction, message: str) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_pipeline.py
from app.models import User


def test_calendar_push_only_reaches_sync_enabled_users_with_a_refresh_token(db, monkeypatch):
    pushed = []
    monkeypatch.setattr(pipeline, "push_calendar_event_for_user", lambda user, extraction, row: pushed.append(user.email))

    synced = User(google_sub="s1", email="synced@example.com", calendar_sync_enabled=True, calendar_refresh_token_encrypted="ct")
    not_synced = User(google_sub="s2", email="off@example.com", calendar_sync_enabled=False, calendar_refresh_token_encrypted="ct")
    no_token = User(google_sub="s3", email="notoken@example.com", calendar_sync_enabled=True, calendar_refresh_token_encrypted=None)
    db.add_all([synced, not_synced, no_token])
    db.commit()

    extraction = Extraction(company="Acme", category="new_listing", deadline="2027-01-01T00:00:00+05:30")
    from app.models import Post

    post = Post(wp_id=1, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    pipeline.push_to_all_users(db, extraction, post)

    assert pushed == ["synced@example.com"]


def test_telegram_push_only_reaches_users_with_a_chat_id(db, monkeypatch):
    sent_to = []
    monkeypatch.setattr(
        pipeline, "send_or_edit_telegram_for_user",
        lambda db, user, extraction, message: sent_to.append(user.email),
    )
    monkeypatch.setattr(pipeline, "push_calendar_event_for_user", lambda *a: None)

    with_chat = User(google_sub="s4", email="chat@example.com", telegram_chat_id="111")
    without_chat = User(google_sub="s5", email="nochat@example.com", telegram_chat_id=None)
    db.add_all([with_chat, without_chat])
    db.commit()

    extraction = Extraction(company="Acme", category="new_listing", deadline="2027-01-01T00:00:00+05:30")
    from app.models import Post

    post = Post(wp_id=2, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    pipeline.push_to_all_users(db, extraction, post)

    assert sent_to == ["chat@example.com"]


def test_one_users_push_failure_does_not_block_another_users_push(db, monkeypatch):
    def flaky_calendar_push(user, extraction, row):
        if user.email == "broken@example.com":
            raise RuntimeError("revoked token")

    pushed_ok = []
    monkeypatch.setattr(pipeline, "push_calendar_event_for_user", lambda user, extraction, row: (
        flaky_calendar_push(user, extraction, row) or pushed_ok.append(user.email)
    ))

    broken = User(google_sub="s6", email="broken@example.com", calendar_sync_enabled=True, calendar_refresh_token_encrypted="ct")
    healthy = User(google_sub="s7", email="healthy@example.com", calendar_sync_enabled=True, calendar_refresh_token_encrypted="ct")
    db.add_all([broken, healthy])
    db.commit()

    extraction = Extraction(company="Acme", category="new_listing", deadline="2027-01-01T00:00:00+05:30")
    from app.models import Post

    post = Post(wp_id=3, slug="a", title="t", link="l", date_gmt="2026-01-01T00:00:00", modified_gmt="2026-01-01T00:00:00", content_hash="h", raw_html="")
    pipeline.push_to_all_users(db, extraction, post)

    assert pushed_ok == ["healthy@example.com"]


def test_send_or_edit_telegram_for_user_scopes_group_key_by_user(db, monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "send_telegram_message", lambda *a: (sent.append(a) or 42))
    monkeypatch.setattr(pipeline, "edit_telegram_message", lambda *a: (_ for _ in ()).throw(AssertionError("should not edit")))

    user = User(google_sub="s8", email="u@example.com", telegram_chat_id="999")
    db.add(user)
    db.commit()
    extraction = Extraction(company="Acme", category="new_listing")

    pipeline.send_or_edit_telegram_for_user(db, user, extraction, "hi")

    stored = db.query(TelegramNotification).one()
    assert str(user.id) in stored.group_key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL with `AttributeError: module 'app.pipeline' has no attribute 'push_to_all_users'`

- [ ] **Step 3: Write the implementation**

In `app/pipeline.py`, add the import:

```python
from app.crypto import decrypt_token
from app.google_calendar import (
    event_id_for,
    event_id_for_company,
    get_access_token,
    upsert_event,
)
from app.models import Extraction, FetchLog, Post, TelegramNotification, User
```

(replace the existing narrower `from app.google_calendar import ...` and `from app.models import ...` lines with these).

Replace `push_calendar_event` and the notify/calendar-push block inside
`run_extraction`, and replace `send_or_edit_telegram`, with:

```python
def send_or_edit_telegram_for_user(db: Session, user: User, extraction: Extraction, message: str) -> None:
    """Same edit-in-place behavior as before, but the group key is scoped
    per user so one user's deadline-extension edit never touches another
    user's message."""
    group = NOTIFY_EVENT_GROUP.get(PostCategory(extraction.category))
    group_key = (
        f"{user.id}|{extraction.company.strip().lower()}|{group}"
        if extraction.company and group
        else None
    )

    existing = (
        db.query(TelegramNotification).filter_by(group_key=group_key).one_or_none()
        if group_key
        else None
    )
    if existing is not None and edit_telegram_message(
        settings.telegram_bot_token, user.telegram_chat_id, existing.message_id, message
    ):
        return

    message_id = send_telegram_message(settings.telegram_bot_token, user.telegram_chat_id, message)
    if message_id is None or group_key is None:
        return
    if existing is not None:
        existing.message_id = message_id
    else:
        db.add(TelegramNotification(group_key=group_key, message_id=message_id))
    db.commit()


def push_calendar_event_for_user(user: User, extraction: Extraction, row: Post) -> None:
    refresh_token = decrypt_token(user.calendar_refresh_token_encrypted)
    access_token = get_access_token(
        settings.google_oauth_client_id, settings.google_oauth_client_secret, refresh_token
    )
    if access_token is None:
        return
    summary = f"{extraction.company or 'Unknown company'}" + (f" - {extraction.role}" if extraction.role else "")
    group = CALENDAR_EVENT_GROUP.get(PostCategory(extraction.category))
    event_id = (
        event_id_for_company(extraction.company, group)
        if extraction.company and group
        else event_id_for(extraction.id)
    )
    upsert_event(
        access_token, user.calendar_id, event_id,
        summary=summary, description=row.link,
        start_iso=extraction.deadline, end_iso=extraction.deadline_end,
    )


def push_to_all_users(db: Session, extraction: Extraction, row: Post) -> None:
    """Loop over every signed-in user and push this extraction to whichever
    channels they've opted into. A failure for one user (revoked token, bad
    chat id) is logged and skipped - it never blocks another user's push or
    aborts the fetch cycle."""
    message = None
    for user in db.query(User).all():
        if user.calendar_sync_enabled and user.calendar_refresh_token_encrypted:
            try:
                push_calendar_event_for_user(user, extraction, row)
            except Exception:
                logger.exception("calendar push failed for user %s", user.email)
        if user.telegram_chat_id:
            if message is None:
                message = format_notification_message(
                    category=PostCategory(extraction.category), company=extraction.company,
                    role=extraction.role, deadline=extraction.deadline, stipend=extraction.stipend,
                )
            try:
                send_or_edit_telegram_for_user(db, user, extraction, message)
            except Exception:
                logger.exception("telegram push failed for user %s", user.email)
```

In `run_extraction`, replace the two `if` blocks that call
`send_or_edit_telegram`/`push_calendar_event` (the block starting `if (
    parsed.category in NOTIFY_CATEGORIES` through the end of the function)
with:

```python
    is_stale = bool(parsed.deadline) and not _is_upcoming(parsed.deadline)
    if not is_stale and (parsed.category in NOTIFY_CATEGORIES or parsed.category in CALENDAR_CATEGORIES):
        push_to_all_users(db, extraction, row)
```

(the `is_stale` line already exists just above this block in the current
file — do not duplicate it, replace from that point down).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS (all existing tests plus the 4 new ones — note the 3
pre-existing tests calling `send_or_edit_telegram` directly still pass
unchanged since that function is untouched by this task... **except** it no
longer has any caller in `run_extraction`. Confirm this by running the full
suite in the next step.)

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS. If any pre-existing test in `test_pipeline.py` fails
because it patches functions no longer called by `run_extraction`
(`send_or_edit_telegram` is now only called directly by tests, not by
production code), leave those tests as regression coverage for the
still-exported function — they should still pass since they call
`send_or_edit_telegram` directly, not through `run_extraction`.

- [ ] **Step 6: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: push calendar events and Telegram notifications per-user instead of to a single hardcoded target"
```

---

### Task 7: Admin gating for `/admin`, new public `/` calendar view, and `/settings`

**Files:**
- Create: `app/site.py`
- Modify: `app/main.py`
- Test: `tests/test_site.py`
- Test: `tests/test_main_pages.py`

**Interfaces:**
- Consumes: `get_current_user_or_redirect` (Task 5); `is_admin` (Task 4); `render_dashboard` (existing, unchanged, now only reachable via `/admin`).
- Produces: `render_login_page() -> str`, `render_calendar_view(user: User, upcoming: list[dict], is_admin: bool) -> str` in `app/site.py`. Routes `GET /` (calendar view), `GET /admin` (existing dashboard, gated), `POST /settings` in `app/main.py`.

- [ ] **Step 1: Write the failing test for the page renderers**

```python
# tests/test_site.py
from app.models import User
from app.site import render_calendar_view, render_login_page


def test_login_page_has_sign_in_button():
    html = render_login_page()
    assert "Sign in with Google" in html
    assert "/auth/start" in html


def test_calendar_view_shows_user_email_and_upcoming_events():
    user = User(email="someone@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = render_calendar_view(
        user,
        upcoming=[{"category": "new_listing", "company": "Acme", "role": "SWE Intern", "deadline": "2027-01-01T00:00:00+05:30", "link": "https://x"}],
        is_admin=False,
    )
    assert "someone@example.com" in html
    assert "Acme" in html
    assert "Admin" not in html


def test_calendar_view_shows_admin_link_for_admin_user():
    user = User(email="sharmaparakh05@gmail.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = render_calendar_view(user, upcoming=[], is_admin=True)
    assert "/admin" in html


def test_calendar_view_escapes_html_in_company_name():
    user = User(email="u@example.com", telegram_chat_id=None, calendar_sync_enabled=True)
    html = render_calendar_view(
        user,
        upcoming=[{"category": "new_listing", "company": "<script>alert(1)</script>", "role": None, "deadline": None, "link": "https://x"}],
        is_admin=False,
    )
    assert "<script>" not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_site.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.site'`

- [ ] **Step 3: Write the page renderers**

```python
# app/site.py
"""Public-facing pages: the sign-in screen and the logged-in calendar/
upcoming-events view. Same plain-HTML-f-string approach as
app/dashboard.py - no template engine, no JS framework."""

from html import escape

from app.models import User
from app.timeutil import IST, parse_ist

_STYLE = """
  :root { --accent: #4f46e5; }
  body { font-family: -apple-system, sans-serif; margin: 0; color: #1a1a1a; background: #f7f7fb; }
  .nav { display: flex; align-items: center; justify-content: space-between; padding: 1rem 2rem; background: #fff; border-bottom: 1px solid #e5e5ef; }
  .nav a { color: var(--accent); text-decoration: none; font-weight: 600; margin-left: 1rem; }
  .wrap { max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
  .card { background: #fff; border-radius: 12px; padding: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 1.5rem; }
  .chip { display: inline-block; padding: 0.2rem 0.6rem; border-radius: 999px; background: var(--accent); color: #fff; font-size: 0.75rem; margin-right: 0.5rem; }
  .login-wrap { display: flex; align-items: center; justify-content: center; height: 100vh; }
  .login-card { text-align: center; padding: 3rem; }
  .btn { display: inline-block; background: var(--accent); color: #fff; padding: 0.75rem 1.5rem; border-radius: 8px; text-decoration: none; font-weight: 600; }
  input[type=text] { padding: 0.5rem; border: 1px solid #ddd; border-radius: 6px; width: 220px; }
  .upcoming-row { padding: 0.75rem 0; border-bottom: 1px solid #eee; }
"""


def render_login_page() -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>internblog</title><style>{_STYLE}</style></head>
<body>
<div class="login-wrap"><div class="login-card">
  <h1>internblog</h1>
  <p>Sign in to sync internship deadlines to your Google Calendar, and
  optionally get Telegram alerts.</p>
  <a class="btn" href="/auth/start">Sign in with Google</a>
</div></div>
</body></html>"""


def _upcoming_row(event: dict) -> str:
    dt = parse_ist(event["deadline"]) if event.get("deadline") else None
    when = dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST") if dt else "-"
    role = f" &middot; {escape(event['role'])}" if event.get("role") else ""
    return (
        f'<div class="upcoming-row">'
        f'<span class="chip">{escape(event["category"])}</span>'
        f'<strong>{escape(event["company"] or "Unknown company")}</strong>{role}'
        f'<div>{escape(when)} &middot; <a href="{escape(event["link"])}">post</a></div>'
        f'</div>'
    )


def render_calendar_view(user: User, upcoming: list[dict], is_admin: bool) -> str:
    admin_link = '<a href="/admin">Admin</a>' if is_admin else ""
    rows = "\n".join(_upcoming_row(e) for e in upcoming) or "<p>Nothing upcoming.</p>"
    sync_status = "syncing to your Google Calendar" if user.calendar_sync_enabled else "sync paused"
    telegram_value = escape(user.telegram_chat_id or "")

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>internblog</title><style>{_STYLE}</style></head>
<body>
<div class="nav">
  <strong>internblog</strong>
  <div>{escape(user.email)} {admin_link} <a href="/logout">Sign out</a></div>
</div>
<div class="wrap">
  <div class="card">
    <h2>Upcoming</h2>
    {rows}
  </div>
  <div class="card">
    <h2>Settings</h2>
    <p>Your events are {escape(sync_status)} ({escape(user.calendar_id or "not created yet")}).</p>
    <form method="post" action="/settings">
      <label>Telegram chat ID (optional): <input type="text" name="telegram_chat_id" value="{telegram_value}"></label>
      <label><input type="checkbox" name="calendar_sync_enabled" {"checked" if user.calendar_sync_enabled else ""}> Sync to my Google Calendar</label>
      <button type="submit">Save</button>
    </form>
  </div>
</div>
</body></html>"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_site.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the failing test for the routes**

```python
# tests/test_main_pages.py
import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.db import init_db
from app.models import User


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.config import settings

    engine = create_engine(f"sqlite:///{tmp_path}/test.db", future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(main, "SessionLocal", Session)
    monkeypatch.setattr("app.db.engine", engine)
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    init_db()
    return Session


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def _login_as(client, session_factory, email):
    db = session_factory()
    user = User(google_sub=email, email=email)
    db.add(user)
    db.commit()
    session_id = auth.create_session(db, user.id)
    client.cookies.set(main.settings.session_cookie_name, session_id)
    return user


def test_root_requires_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/login"


def test_root_shows_calendar_view_when_logged_in(client, _fresh_db):
    _login_as(client, _fresh_db, "user@example.com")
    response = client.get("/")
    assert response.status_code == 200
    assert "user@example.com" in response.text


def test_admin_route_404s_for_non_owner(client, _fresh_db):
    _login_as(client, _fresh_db, "not-owner@example.com")
    response = client.get("/admin")
    assert response.status_code == 404


def test_admin_route_200s_for_owner(client, _fresh_db):
    _login_as(client, _fresh_db, "owner@example.com")
    response = client.get("/admin")
    assert response.status_code == 200


def test_settings_updates_telegram_chat_id_and_sync_toggle(client, _fresh_db):
    _login_as(client, _fresh_db, "user2@example.com")
    response = client.post("/settings", data={"telegram_chat_id": "555"}, follow_redirects=False)
    assert response.status_code == 307

    db = _fresh_db()
    updated = db.query(User).filter_by(email="user2@example.com").one()
    assert updated.telegram_chat_id == "555"
    assert updated.calendar_sync_enabled is False  # unchecked checkbox omits the field entirely
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_main_pages.py -v`
Expected: FAIL — `/` still renders the old unauthenticated dashboard, `/admin` doesn't exist, `/settings` doesn't exist

- [ ] **Step 7: Wire the routes**

In `app/main.py`:
1. Add import: `from app.site import render_calendar_view, render_login_page`
2. Rename the existing `@app.get("/", response_class=HTMLResponse) def dashboard()` route's decorator path from `"/"` to `"/admin"`, and add the gate as its first line:

```python
@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(user: User = Depends(get_current_user_or_redirect)) -> str:
    if not auth.is_admin(user):
        raise HTTPException(status_code=404)
    status = _compute_status()
    # ... (rest of the existing dashboard() body, unchanged)
```

3. Add the new `/` route (replacing the removed one):

```python
@app.get("/", response_class=HTMLResponse)
def home(user: User = Depends(get_current_user_or_redirect)) -> str:
    with SessionLocal() as db:
        calendar_rows = db.execute(
            select(Extraction, Post)
            .join(Post, Extraction.post_id == Post.id)
            .where(Extraction.category.in_([c.value for c in CALENDAR_CATEGORIES]))
            .where(Extraction.deadline.is_not(None))
        ).all()
        upcoming = sorted(
            (
                {
                    "category": extraction.category, "company": extraction.company,
                    "role": extraction.role, "deadline": extraction.deadline, "link": post.link,
                }
                for extraction, post in calendar_rows
                if _is_upcoming(extraction.deadline)
            ),
            key=lambda r: r["deadline"],
        )
    return render_calendar_view(user, upcoming, is_admin=auth.is_admin(user))


@app.post("/settings")
def save_settings(
    request: Request,
    user: User = Depends(get_current_user_or_redirect),
    telegram_chat_id: str = Form(""),
    calendar_sync_enabled: bool = Form(False),
) -> Response:
    with SessionLocal() as db:
        db_user = db.query(User).filter_by(id=user.id).one()
        db_user.telegram_chat_id = telegram_chat_id.strip() or None
        db_user.calendar_sync_enabled = calendar_sync_enabled
        db.commit()
    return RedirectResponse("/", status_code=307)
```

4. Add `from fastapi import ..., Form` to the existing FastAPI import line.
5. `get_current_user_or_redirect` (Task 5) currently raises `HTTPException(status_code=307, ...)` to redirect — FastAPI's `HTTPException` does emit the `Location` header correctly for 3xx via `headers=`, but replace that raise with a cleaner redirect using a small dependency wrapper. Change `get_current_user_or_redirect` to:

```python
def get_current_user_or_redirect(request: Request) -> User:
    session_id = request.cookies.get(settings.session_cookie_name)
    with SessionLocal() as db:
        user = auth.get_session_user(db, session_id)
    if user is None:
        raise RedirectToLogin()
    return user


class RedirectToLogin(Exception):
    pass


@app.exception_handler(RedirectToLogin)
def _redirect_to_login(request: Request, exc: RedirectToLogin) -> Response:
    return RedirectResponse("/login", status_code=307)
```

(place the `RedirectToLogin` class and its exception handler right after
the `app = FastAPI(...)` line, before `get_current_user_or_redirect` uses
it).

6. Add the `/login` route, rendering the explanatory card added in this
task's Step 3 (`render_login_page`), whose button links to `/auth/start`
(added in Task 5):

```python
@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    return render_login_page()
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_main_pages.py tests/test_main_auth.py -v`
Expected: PASS (all tests)

- [ ] **Step 9: Run the full test suite**

Run: `pytest -v`
Expected: PASS across every test file.

- [ ] **Step 10: Commit**

```bash
git add app/main.py app/site.py tests/test_site.py tests/test_main_pages.py
git commit -m "feat: gate /admin to the owner, add public calendar view at / and a /settings endpoint"
```

---

### Task 8: Migration script and cleanup of the old single-user settings

**Files:**
- Create: `scripts/migrate_owner_to_users_table.py`
- Modify: `app/config.py` (remove retired settings)
- Modify: `app/pipeline.py` (remove now-dead single-target code if any remains)
- Modify: `.env.example`
- Modify: `docs/decisions.md`
- Modify: `docs/deployment.md`

**Interfaces:**
- Consumes: `SessionLocal`, `init_db` from `app/db.py`; `User` from `app/models.py`; `encrypt_token` from `app/crypto.py`; existing (about-to-be-removed) `settings.google_calendar_refresh_token`, `settings.google_calendar_id`, `settings.telegram_chat_id`.
- Produces: a one-off script with no importable interface consumed elsewhere.

- [ ] **Step 1: Write the migration script**

```python
# scripts/migrate_owner_to_users_table.py
"""One-off migration: move the original single-user Google Calendar
refresh token / calendar id / Telegram chat id (previously hardcoded in
.env) into a users table row for the owner, before those settings are
removed from app/config.py. Run this once during the deploy that ships
multi-user login, before deleting the old env vars.

Usage: python scripts/google_calendar_auth.py must have already been run
at some point (that's where GOOGLE_CALENDAR_REFRESH_TOKEN originally came
from) - this script just re-homes the values already in .env into the DB.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.crypto import encrypt_token
from app.db import SessionLocal, init_db
from app.models import User

OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "sharmaparakh05@gmail.com")
OWNER_GOOGLE_SUB = os.environ.get("OWNER_GOOGLE_SUB", "")  # optional, filled in on first real login anyway
REFRESH_TOKEN = os.environ.get("GOOGLE_CALENDAR_REFRESH_TOKEN", "")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def main() -> None:
    if not REFRESH_TOKEN or not CALENDAR_ID:
        print("GOOGLE_CALENDAR_REFRESH_TOKEN / GOOGLE_CALENDAR_ID not set, nothing to migrate.")
        return

    init_db()
    with SessionLocal() as db:
        user = db.query(User).filter_by(email=OWNER_EMAIL).one_or_none()
        if user is None:
            user = User(google_sub=OWNER_GOOGLE_SUB or f"pending-{OWNER_EMAIL}", email=OWNER_EMAIL)
            db.add(user)
        user.calendar_refresh_token_encrypted = encrypt_token(REFRESH_TOKEN)
        user.calendar_id = CALENDAR_ID
        user.calendar_sync_enabled = True
        if TELEGRAM_CHAT_ID:
            user.telegram_chat_id = TELEGRAM_CHAT_ID
        db.commit()
        print(f"Migrated owner row for {OWNER_EMAIL} (id={user.id}).")
        print(
            "Note: google_sub is a placeholder until this user's first real "
            "sign-in via /login, which will overwrite it with their actual sub."
        )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it locally to verify it's syntactically correct (dry run without real env vars is fine)**

Run: `python scripts/migrate_owner_to_users_table.py`
Expected output: `GOOGLE_CALENDAR_REFRESH_TOKEN / GOOGLE_CALENDAR_ID not set, nothing to migrate.` (since no real `.env` values are loaded in this dry run — confirms the script imports cleanly and the early-exit path works)

- [ ] **Step 3: Remove the retired settings**

In `app/config.py`, delete the `google_calendar_refresh_token`, `google_calendar_id`, `telegram_chat_id`, and `google_calendar_enabled` property, and the old `google_calendar_client_id`/`google_calendar_client_secret` (superseded by `google_oauth_client_id`/`google_oauth_client_secret` from Task 3). Check `grep -rn "settings.google_calendar_\|settings.telegram_chat_id" app/ scripts/` first to confirm no remaining call sites outside `app/pipeline.py` (already updated in Task 6) and this migration script (which reads the raw env var directly via `os.environ`, not through `settings`, so it's unaffected by removing the `Settings` fields).

- [ ] **Step 4: Update `.env.example`**

Remove the old `GOOGLE_CALENDAR_CLIENT_ID`, `GOOGLE_CALENDAR_CLIENT_SECRET`,
`GOOGLE_CALENDAR_REFRESH_TOKEN`, `GOOGLE_CALENDAR_ID`, and `TELEGRAM_CHAT_ID`
lines (Task 3 already added the replacement `GOOGLE_OAUTH_CLIENT_ID`/
`GOOGLE_OAUTH_CLIENT_SECRET`/`OAUTH_REDIRECT_BASE_URL`/`OWNER_EMAIL` block —
this step just deletes the now-dead lines). Keep `TELEGRAM_BOT_TOKEN` (still
a single global bot token).

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: PASS. If any test still references a removed setting
(`settings.google_calendar_refresh_token` etc.), update that test to use
the `User`-based per-user fields instead — by this point in the plan no
production code should reference the removed settings, so any failure here
means a leftover test fixture, not a code path.

- [ ] **Step 6: Add decision log entries**

Append to `docs/decisions.md`:

```markdown
| 2026-07-18 | Site converted from single-user to public multi-user: Google OAuth login (Authlib) required for the whole site, server-side sessions in a new `sessions` table, per-user `users` table storing an encrypted Calendar refresh token and optional Telegram chat id | User wants to open the tool to other people, each syncing the same shared internship postings to their own calendar/Telegram rather than the original hardcoded single target |
| 2026-07-18 | Admin/status dashboard moved from `/` to `/admin`, gated to `sharmaparakh05@gmail.com` by email, returning 404 (not 403) for anyone else | Public visitors shouldn't see session/fetch-log internals; 404 avoids confirming the route exists to non-owners |
| 2026-07-18 | Google Calendar refresh tokens encrypted at rest with Fernet (`app/crypto.py`), keyed by a new `SECRET_ENCRYPTION_KEY` | Multi-user means many long-lived personal-calendar credentials now live in the DB instead of one value in `.env` - plaintext storage would be a real exposure if the DB leaked |
```

- [ ] **Step 7: Update deployment docs**

In `docs/deployment.md`, add a new numbered section after "## 7. Wire up notifications":

```markdown
## 8. Public login setup (multi-user)

1. In the Google Cloud project already used for Calendar API access, add
   an OAuth 2.0 **Web application** client (not "Desktop app" like the
   original single-user setup) with an authorized redirect URI of
   `https://your-hostname.duckdns.org/auth/callback`.
2. Move the OAuth consent screen from "Testing" to "Production" (or add
   test users) - only accounts explicitly allowed under "Testing" can sign
   in otherwise, and the whole point is to let anyone sign in.
3. Set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
   `OAUTH_REDIRECT_BASE_URL`, `OWNER_EMAIL`, and `SECRET_ENCRYPTION_KEY` in
   `.env`.
4. Run `python scripts/migrate_owner_to_users_table.py` once, using the
   original `GOOGLE_CALENDAR_REFRESH_TOKEN`/`GOOGLE_CALENDAR_ID`/
   `TELEGRAM_CHAT_ID` values from before this migration, to carry the
   owner's existing setup into the new `users` table.
5. Remove the old `GOOGLE_CALENDAR_CLIENT_ID`/`_SECRET`/
   `GOOGLE_CALENDAR_REFRESH_TOKEN`/`GOOGLE_CALENDAR_ID`/`TELEGRAM_CHAT_ID`
   values from `.env` - they're no longer read by the app.
6. `docker compose up -d --build` to pick up the new dependency
   (`authlib`) and code.
```

- [ ] **Step 8: Commit**

```bash
git add scripts/migrate_owner_to_users_table.py app/config.py .env.example docs/decisions.md docs/deployment.md
git commit -m "feat: add owner migration script, retire single-user settings, update deployment docs"
```

---

## Post-plan manual steps (not code, cannot be scripted by this plan)

- Create the OAuth "Web application" client in Google Cloud Console and move the consent screen to Production (Task 8, Step 7, items 1-2).
- Generate a real `SECRET_ENCRYPTION_KEY` and set all new env vars on the deployed server.
- Run the migration script on the server with the real `.env` values loaded, before removing the old ones.
