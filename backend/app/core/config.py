from pathlib import Path

from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings
from typing import Optional

_config_path = Path(__file__).resolve()
_env_candidates = (_config_path.parents[3] / ".env", _config_path.parents[2] / ".env")
_env_file = next((candidate for candidate in _env_candidates if candidate.is_file()), _env_candidates[0])


class Settings(BaseSettings):
    model_config = ConfigDict(
        env_file=_env_file,
        case_sensitive=True,
        extra="ignore",
    )

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./negotiation_arena.db"
    USE_SQLITE_FALLBACK: bool = True

    # Security
    SECRET_KEY: str = "dev-secret-key-change-in-production-32chars!!"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    ALGORITHM: str = "HS256"

    # Groq
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    USE_MOCK_AGENTS: bool = True

    # CORS
    FRONTEND_URL: str = "http://localhost:5173"

    # App
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    MAX_REQUEST_BODY_BYTES: int = Field(default=65536, ge=1024, le=1048576)
    REQUEST_BODY_TIMEOUT_SECONDS: int = Field(default=15, ge=1, le=120)
    MAX_NEGOTIATIONS_PER_USER: int = Field(default=100, ge=1, le=1000)
    MAX_ACTIVE_NEGOTIATIONS_PER_USER: int = Field(default=3, ge=1, le=10)
    AI_REQUEST_TIMEOUT_SECONDS: int = Field(default=25, ge=1, le=120)
    SSE_MAX_SECONDS: int = Field(default=300, ge=10, le=1800)
    SSE_HEARTBEAT_SECONDS: int = Field(default=15, ge=1, le=60)

settings = Settings()


def validate_production_secret(secret_key: str) -> None:
    normalized = secret_key.strip().lower()
    placeholders = (
        "dev-secret-key-change-in-production-32chars!!",
        "replace-with-a-unique-random-secret-at-least-32-characters",
        "replace-with-",
        "your-secret",
        "your_secret",
        "change-me",
        "changeme",
        "placeholder",
        "example-secret",
        "test-secret",
    )
    if len(secret_key.strip()) < 32 or any(
        normalized == placeholder or normalized.startswith(placeholder)
        for placeholder in placeholders
    ):
        raise RuntimeError("Set a unique, non-placeholder SECRET_KEY of at least 32 characters in production.")
