from pathlib import Path

from cryptography.fernet import Fernet

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

    # Sign in with Google: the OAuth client id (Google Cloud Console, type "Web application").
    # Empty = Google sign-in is off (POST /auth/google answers 503). Not a secret: the browser
    # sees it too. The ID token's audience must equal it, so tokens made for other apps fail.
    google_client_id: str = ""

    # Encrypts uploaded lab documents on disk (Fernet). Required; losing it makes every stored
    # file unreadable, so keep a copy somewhere safe. Generate one with:
    #   uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    document_encryption_key: str
    # Folder for the encrypted files (a Docker volume on deployment)
    upload_dir: Path = ROOT_DIR / "backend" / "uploads"

    # OCR engine for uploaded lab documents (backend/ocr/engines.py, docs/ocr.md). "none": uploads stay
    # 'pending' and the patient types the values in. "pdf_text": reads PDFs that contain text (no
    # GPU). "paddleocr_vl": PaddleOCR-VL for photos and scans (GPU install step in docs/ocr.md).
    ocr_engine: str = "none"
    # Where PaddleOCR-VL runs: "gpu:0" or "cpu" (very slow)
    ocr_device: str = "gpu:0"

    # Risk models (Sprint 5). false until the four models are trained and registered in
    # model_versions and the care plan templates are approved; true makes the app refuse to
    # start if a model file was changed or a model or template is missing.
    risk_models_enabled: bool = False
    # Model files may only be loaded from this folder
    model_dir: Path = ROOT_DIR / "ml" / "artifacts"
    # Show care plan templates that are still drafts (not yet approved by the physician).
    # Only for demos and tests before approval; must stay false for real patients.
    show_draft_care_plans: bool = False

    @field_validator("document_encryption_key")
    @classmethod
    def check_fernet_key(cls, value: str) -> str:
        try:
            Fernet(value.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("DOCUMENT_ENCRYPTION_KEY must be a Fernet key (see .env.example)") from exc
        return value

    @field_validator("jwt_secret_key")
    @classmethod
    def reject_placeholder(cls, value: str) -> str:
        if value.startswith("CHANGE_ME"):
            raise ValueError("Set JWT_SECRET_KEY in .env to a long random value")
        return value


settings = Settings()