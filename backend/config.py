from pathlib import Path

from pydantic import Field, field_validator
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
    # false: rate limits and idempotency keys are kept in memory (one dev process, no Redis needed).
    # true: kept in Redis at REDIS_URL (Docker and deployment).
    redis_enabled: bool = False

    # Browser origins allowed to call the API (the Reflex frontend). JSON list in .env, e.g.
    # CORS_ORIGINS=["http://localhost:3000"]. Set to the real domain on deployment.
    cors_origins: list[str] = ["http://localhost:3000"]

    # Version of the privacy notice (RA 10173) shown on the registration screen.
    # Raise it whenever the notice text changes; registration only accepts the current one.
    privacy_notice_version: str = "1.0"

    # Signs the login tokens (JWT). Required, at least 32 characters, never the placeholder.
    # Generate one with:  uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
    jwt_secret_key: str = Field(min_length=32)
    access_token_expire_minutes: int = Field(default=30, ge=5, le=1440)

    @field_validator("jwt_secret_key")
    @classmethod
    def reject_placeholder(cls, value: str) -> str:
        if value.startswith("CHANGE_ME"):
            raise ValueError("Set JWT_SECRET_KEY in .env to a long random value")
        return value


settings = Settings()