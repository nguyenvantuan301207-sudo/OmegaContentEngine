"""OMEGA application configuration.

Uses Pydantic Settings to load and validate environment variables.
All secrets come from environment — nothing is hard-coded.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── Application ──
    app_name: str = "omega"
    app_version: str = "0.1.0"
    environment: str = "development"
    log_level: str = "INFO"
    service_name: str = "omega-api"
    auto_migrate: bool = True

    # ── Database (async — FastAPI) ──
    database_url: str = "postgresql+asyncpg://omega:omega_dev@omega-postgres:5432/omega"

    # ── Database (sync — Celery worker) ──
    database_url_sync: str = "postgresql+psycopg2://omega:omega_dev@omega-postgres:5432/omega"

    # ── Redis ──
    redis_url: str = "redis://omega-redis:6379/0"

    # ── CORS ──
    cors_origins: list[str] | str = ["http://localhost:3000"]

    # ── Worker health ──
    worker_health_timeout: float = 3.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: object) -> list[str]:
        """Parse CORS origins from comma-separated string, JSON string, or list."""
        if isinstance(v, str):
            v_trimmed = v.strip()
            if v_trimmed.startswith("[") and v_trimmed.endswith("]"):
                import json

                try:
                    parsed = json.loads(v_trimmed)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except json.JSONDecodeError:
                    pass
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        if isinstance(v, (list, tuple, set)):
            return [str(item).strip() for item in v if str(item).strip()]
        return ["http://localhost:3000"]

    model_config = {"env_file": ".env", "extra": "ignore"}


def get_settings() -> Settings:
    """Create and return a Settings instance."""
    return Settings()
