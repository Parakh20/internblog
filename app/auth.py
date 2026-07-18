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
SCOPE = "openid email profile https://www.googleapis.com/auth/calendar.app.created"


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
        # Falls back to matching by email so a pre-existing row (e.g. the
        # owner row created by scripts/migrate_owner_to_users_table.py with
        # a placeholder google_sub) is adopted on first real login instead
        # of colliding with the unique email constraint on insert.
        user = db.query(User).filter_by(email=profile["email"]).one_or_none()
    if user is None:
        user = User(google_sub=profile["google_sub"], email=profile["email"])
        db.add(user)
    else:
        user.google_sub = profile["google_sub"]

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
    if row is None:
        return None
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        # SQLite (used in tests, and possible dev fallback) doesn't
        # preserve tzinfo on DateTime(timezone=True) columns - the value
        # round-trips naive. It was always stored as UTC (see
        # create_session), so treat a naive read as UTC.
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return None
    return db.query(User).filter_by(id=row.user_id).one_or_none()


def delete_session(db: DBSession, session_id: str | None) -> None:
    if not session_id:
        return
    db.query(SessionRow).filter_by(id=session_id).delete()
    db.commit()


def is_admin(user: User) -> bool:
    return user.email == settings.owner_email
