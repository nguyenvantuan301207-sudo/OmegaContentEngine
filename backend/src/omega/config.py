"""OMEGA application configuration.

Uses Pydantic Settings to load and validate environment variables.
All secrets come from environment — nothing is hard-coded.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings


# ── P20-A Recurring Scheduler Hard Bounds ──
# HARD_SCHEMA_SAFETY_BOUND: The database migration 025 enforces chk_interval_minimum >= 60.
SCHEMA_MINIMUM_INTERVAL_SECONDS: int = 60
# HARD_SCHEMA_SAFETY_BOUND: The database migration 025 enforces chk_max_catch_up_range <= 10.
SCHEMA_MAX_CATCH_UP_OCCURRENCES_CAP: int = 10

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

    # ── Production Worker Lease & Sweep (P19-LR2) ──
    production_lease_sweep_enabled: bool = False

    # ── Production Dispatch Reliability & Queue Stall Recovery (P19-LR3) ──
    production_dispatch_timeout_seconds: int = 300
    production_dispatch_max_generations: int = 3
    production_dispatch_recovery_enabled: bool = False

    # Campaign lazy-admission runtime (P19-CB2). One gate controls growth only;
    # reconciliation always retains terminal/cancellation convergence.
    campaign_orchestration_enabled: bool = False
    campaign_default_concurrency: int = 1
    campaign_max_concurrency: int = 10
    campaign_channel_max_active_missions: int = 1
    campaign_reconciliation_interval_seconds: int = 10
    campaign_reconciliation_batch_size: int = 25
    campaign_materialization_max_attempts: int = 3

    # ── OMEGA-011 Publisher & Vault ──
    omega_secret_encryption_key: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:8000/api/v1/publisher/accounts/youtube/callback"
    media_storage_root: str = "storage/media"
    publisher_private_canary_mode: bool = True

    # ── TTS Narration ──
    tts_provider: str = "local"
    local_tts_engine: str = "kokoro"
    local_tts_profile: str = "quality"
    local_tts_device: str = "auto"
    local_tts_language: str = "en-US"
    local_tts_voice: str = "af_heart"
    local_tts_speed: float = 1.0
    local_tts_model_dir: str = "/app/models/tts/kokoro"

    # ── P20-A Recurring Scheduler Hard Bounds & Configurable Defaults ──
    # HARD_SCHEMA_SAFETY_BOUND: The database migration 025 enforces chk_interval_minimum >= 60.
    SCHEMA_MINIMUM_INTERVAL_SECONDS: int = 60
    # HARD_SCHEMA_SAFETY_BOUND: The database migration 025 enforces chk_max_catch_up_range <= 10.
    SCHEMA_MAX_CATCH_UP_OCCURRENCES_CAP: int = 10

    # CONFIGURABLE_RUNTIME_DEFAULT settings
    recurring_scheduler_enabled: bool = False
    scheduler_poll_interval_seconds: int = 15
    scheduler_sweep_batch_size: int = 50
    scheduler_minimum_interval_seconds: int = 60
    scheduler_default_max_catch_up: int = 3
    scheduler_pending_timeout_seconds: int = 120
    scheduler_dispatch_timeout_seconds: int = 60
    scheduler_max_dispatch_attempts: int = 3
    scheduler_wait_timeout_seconds: int = 300
    scheduler_max_payload_bytes: int = 65536

    @field_validator("scheduler_minimum_interval_seconds")
    @classmethod
    def validate_scheduler_minimum_interval(cls, v: int) -> int:
        if v < 60:
            raise ValueError(
                f"scheduler_minimum_interval_seconds ({v}) cannot be less than HARD_SCHEMA_SAFETY_BOUND (60)"
            )
        return v

    @field_validator("scheduler_default_max_catch_up")
    @classmethod
    def validate_scheduler_default_max_catch_up(cls, v: int) -> int:
        if v < 1 or v > 10:
            raise ValueError(
                f"scheduler_default_max_catch_up ({v}) must be between 1 and HARD_SCHEMA_SAFETY_BOUND (10)"
            )
        return v

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
