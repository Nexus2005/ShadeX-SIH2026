from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENVIRONMENT: str = "development"

    SUPABASE_URL: str = ""
    DATABASE_URL: str
    DB_SCHEMA: str = "public"

    SECRET_KEY: str
    SESSION_TTL_MINUTES: int = 720

    STORAGE_BACKEND: str = "local"
    STORAGE_BUCKET: str = "resilient-documents"
    STORAGE_ROOT: str = "var/storage"

    MAX_UPLOAD_SIZE_MB: int = 25

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_TIMEOUT_SECONDS: int = 30

    OCR_DPI: int = 220
    OCR_TEXT_MIN_CHARS: int = 30
    OCR_LOW_CONFIDENCE: float = 0.55

    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000"

    REPORT_ROOT: str = "var/reports"

    @field_validator("SECRET_KEY", mode="after")
    @classmethod
    def _secret_strength(cls, v: str) -> str:
        if not v or v == "change-me-resilient-ai-jwt-secret":
            # Allowed only in development; production must set a real secret.
            return v
        return v

    @property
    def debug(self) -> bool:
        return self.ENVIRONMENT == "development"

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.GEMINI_API_KEY)

    def root_path(self, rel: str) -> Path:
        base = Path(__file__).resolve().parent.parent  # backend/
        p = base / rel
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    if settings.ENVIRONMENT not in ("development",) and settings.SECRET_KEY.startswith("change-me"):
        raise RuntimeError("SECRET_KEY must be replaced outside development")
    return settings
