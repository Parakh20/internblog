"""Application settings, loaded from environment variables or .env."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    blog_base_url: str = "https://campus.placements.iitb.ac.in/blog/internship"
    poll_interval_minutes: int = 2

    database_url: str = f"sqlite:///{PROJECT_ROOT}/data/internblog.db"

    # Primary extraction provider: Groq's own free tier (published rate limits,
    # not secondhand capacity like OpenRouter's ":free" models).
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: str = Field(default="", validation_alias=AliasChoices("GROQ_API_KEY"))
    groq_model: str = "openai/gpt-oss-120b"

    # Fallback chain: OpenRouter's free models, tried in order if Groq is
    # unavailable or unset. These get pulled/saturated periodically upstream.
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY", "OPENROUTER_API_KEY"),
    )
    llm_models: str = (
        "openai/gpt-oss-20b:free,"
        "qwen/qwen3-next-80b-a3b-instruct:free,"
        "nousresearch/hermes-3-llama-3.1-405b:free,"
        "meta-llama/llama-3.3-70b-instruct:free,"
        "google/gemma-4-31b-it:free"
    )
    extraction_enabled: bool = True

    @property
    def llm_model_list(self) -> list[str]:
        return [m.strip() for m in self.llm_models.split(",") if m.strip()]

    # Telegram push notification for each new job posting extracted. The
    # chat id is per-user now (see User.telegram_chat_id in app/models.py);
    # only the bot token is still a single global setting.
    telegram_bot_token: str = ""
    # Public @username of the bot, used to build the /start deep link shown
    # on the settings page (t.me/<username>?start=<code>).
    telegram_bot_username: str = ""
    # Shared secret Telegram echoes back in the X-Telegram-Bot-Api-Secret-Token
    # header on every webhook call (set via setWebhook's secret_token param),
    # so /telegram/webhook can reject requests that don't actually come from
    # Telegram before trusting any chat_id in the body.
    telegram_webhook_secret: str = ""

    # ICS calendar feed of application deadlines, served at
    # /calendar/{calendar_feed_token}.ics. The token keeps the feed
    # unguessable since it's public (Google Calendar must be able to fetch it).
    calendar_feed_token: str = ""

    # Fernet key (generate with `python3 -c "from cryptography.fernet import
    # Fernet; print(Fernet.generate_key().decode())"`) used to encrypt each
    # user's Google Calendar refresh token at rest.
    secret_encryption_key: str = ""

    # Direct push into each user's personal Google Calendar via the Calendar
    # API, so deadline/test/PPT events show up on mobile too - the official
    # Google Calendar mobile app does not surface URL-subscribed ("Other
    # calendars") feeds like the desktop web app does, so
    # /calendar/{token}.ics alone doesn't reach phones. See docs/decisions.md.
    # Per-user refresh token/calendar id now live on the User model
    # (app/models.py), populated via app/auth.py on first sign-in.

    # OAuth client used for both "Sign in with Google" and Calendar API
    # access - same Cloud project as the original single-user setup, now
    # also handling public login. Scopes requested: openid email profile
    # https://www.googleapis.com/auth/calendar.app.created (see app/auth.py).
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    # Public base URL this app is served at, e.g. https://your-host.duckdns.org
    # - needed to build the OAuth redirect_uri without hardcoding a host.
    oauth_redirect_base_url: str = ""

    # Only this email sees /admin (the session/fetch-log/DB dashboard). Also
    # shown as the contact address on /privacy, /terms and the access-denied
    # page. Empty means nobody is admin.
    owner_email: str = ""

    session_cookie_name: str = "internblog_session"
    # Effectively never expires (100 years) - the user asked for sign-in to
    # persist indefinitely rather than requiring a re-login every 30 days.
    # Kept finite (not nullable) so the existing expires_at column/check in
    # app/auth.py::get_session_user needs no schema change.
    session_ttl_days: int = 36500

    storage_state_path: Path = PROJECT_ROOT / "storage_state.json"
    snapshot_dir: Path = PROJECT_ROOT / "data" / "snapshots"
    log_dir: Path = PROJECT_ROOT / "logs"

    api_host: str = "127.0.0.1"
    api_port: int = 8000


settings = Settings()
