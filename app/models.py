"""SQLAlchemy models for posts, extractions, and fetch logs."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wp_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text)
    link: Mapped[str] = mapped_column(Text)
    date_gmt: Mapped[str] = mapped_column(String(32))
    modified_gmt: Mapped[str] = mapped_column(String(32))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    raw_html: Mapped[str] = mapped_column(Text)
    removed: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    extractions: Mapped[list["Extraction"]] = relationship(back_populates="post")


class Extraction(Base):
    __tablename__ = "extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), index=True)
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deadline: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deadline_end: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cgpa_cutoff: Mapped[str | None] = mapped_column(String(64), nullable=True)
    eligible_branches: Mapped[str | None] = mapped_column(Text, nullable=True)
    stipend: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    application_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(32), default="other", index=True)
    raw_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    post: Mapped["Post"] = relationship(back_populates="extractions")


class TelegramNotification(Base):
    """Tracks the last message sent per (company, event type) group, so a
    follow-up post (e.g. a deadline_extension after a new_listing) edits
    that message in place instead of sending a duplicate - mirrors the
    calendar event grouping in app/google_calendar.py."""

    __tablename__ = "telegram_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    message_id: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FetchLog(Base):
    __tablename__ = "fetch_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(32))
    session_alive: Mapped[bool] = mapped_column(Boolean)
    posts_count: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    modified_count: Mapped[int] = mapped_column(Integer, default=0)
    removed_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    event_calendar_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calendar_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Single-use code embedded in the /start deep link (t.me/<bot>?start=<code>)
    # used to correlate an inbound Telegram /start message back to this user
    # without them ever seeing or copying a raw chat_id. Cleared once used.
    telegram_link_code: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AllowedEmail(Base):
    """Admin-managed sign-in allowlist (placement office policy requires the
    app stay restricted to a small explicit group rather than being
    publicly signupable). Checked at /auth/callback before a User row is
    ever created - see app/auth.py::is_email_allowed. Managed from /admin,
    not .env, so add/remove takes effect immediately without a redeploy."""

    __tablename__ = "allowed_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    """Record of admin-initiated actions (allowlist add/remove, manual
    cycle trigger), shown on /admin - see app/audit.py::record. Distinct
    from the application log file (app/logging_setup.py), which captures
    everything the app does, not just deliberate admin actions."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_email: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")


class Session(Base):
    """Server-side session, looked up by the opaque token stored in the
    session cookie. Revocable (unlike a JWT) - logging out deletes the row,
    and any session can be killed independently of any other."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
