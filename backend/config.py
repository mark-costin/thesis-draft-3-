from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root (thesis-d3/), so .env is found no matter which folder you run from
ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # other keys in .env (e.g. Docker ones later) won't crash startup
    )

    # Required, no default: a missing .env fails at startup instead of
    # silently using a placeholder password. Connect as med_app, never postgres.
    database_url: str
    test_database_url: str | None = None
    redis_url: str = "redis://localhost:6379/0"


settings = Settings()