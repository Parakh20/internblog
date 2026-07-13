"""Application settings, loaded from environment variables or .env."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    blog_base_url: str = "https://campus.placements.iitb.ac.in/blog/internship"
    poll_interval_minutes: int = 5

    database_url: str = f"sqlite:///{PROJECT_ROOT}/data/internblog.db"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"
    extraction_enabled: bool = True

    storage_state_path: Path = PROJECT_ROOT / "storage_state.json"
    snapshot_dir: Path = PROJECT_ROOT / "data" / "snapshots"
    log_dir: Path = PROJECT_ROOT / "logs"

    api_host: str = "127.0.0.1"
    api_port: int = 8000


settings = Settings()
