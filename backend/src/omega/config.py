"""OMEGA application configuration.

Uses Pydantic Settings to load and validate environment variables.
All secrets come from environment — nothing is hard-coded.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

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

    # ── Database Connection Pool & Query Bounds (H2) ──
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: float = 30.0
    db_pool_recycle: int = 1800
    db_statement_timeout_ms: int = 30000
    db_lock_timeout_ms: int = 10000

    # ── Provenance & Release Metadata (H5) ──
    source_commit: str = "unknown"
    image_tag: str = "unknown"
    build_timestamp: str = "unknown"

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

    # ── P20-A Recurring Scheduler Hard Bounds (S2: Consolidated to module authority) ──
    @property
    def SCHEMA_MINIMUM_INTERVAL_SECONDS(self) -> int:
        return SCHEMA_MINIMUM_INTERVAL_SECONDS

    @property
    def SCHEMA_MAX_CATCH_UP_OCCURRENCES_CAP(self) -> int:
        return SCHEMA_MAX_CATCH_UP_OCCURRENCES_CAP

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

    # ── P20-B Deterministic Pipeline Analytics Foundation ──
    analytics_api_enabled: bool = False
    analytics_rollup_enabled: bool = False
    analytics_rollup_lookback_days: int = 2
    analytics_rollup_interval_seconds: int = 3600

    # ── P20-C Observability & Production Operations ──
    # Configurable probe timeouts & cache thresholds (CONFIGURABLE_UNTUNED)
    observability_db_collector_cache_ttl_seconds: int = 5
    db_health_timeout_seconds: float = 1.0
    redis_health_timeout_seconds: float = 1.0
    beat_health_threshold_seconds: float = 30.0

    # Production access control: Fail-closed in production if None
    metrics_auth_token: str | None = None
    operator_auth_token: str | None = None

    @field_validator("observability_db_collector_cache_ttl_seconds")
    @classmethod
    def validate_observability_db_collector_cache_ttl(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(
                f"observability_db_collector_cache_ttl_seconds ({v}) must be greater than 0"
            )
        return v

    @field_validator("analytics_rollup_lookback_days")
    @classmethod
    def validate_analytics_rollup_lookback_days(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"analytics_rollup_lookback_days ({v}) must be greater than 0")
        return v

    @field_validator("analytics_rollup_interval_seconds")
    @classmethod
    def validate_analytics_rollup_interval_seconds(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"analytics_rollup_interval_seconds ({v}) must be greater than 0")
        return v

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

    def model_post_init(self, __context: Any) -> None:
        """Resolve immutable build metadata and provenance (H5)."""
        meta_file = Path("/app/build_metadata.json")
        if meta_file.exists():
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                if self.source_commit == "unknown" and "git_commit" in data:
                    self.source_commit = str(data["git_commit"])
                if self.image_tag == "unknown" and "image_tag" in data:
                    self.image_tag = str(data["image_tag"])
                if self.build_timestamp == "unknown" and "build_timestamp" in data:
                    self.build_timestamp = str(data["build_timestamp"])
            except Exception:
                pass

        env_commit = os.getenv("OMEGA_SOURCE_COMMIT") or os.getenv("GIT_COMMIT")
        if env_commit:
            self.source_commit = env_commit
        env_tag = os.getenv("OMEGA_IMAGE_TAG") or os.getenv("IMAGE_TAG")
        if env_tag:
            self.image_tag = env_tag
        env_time = os.getenv("OMEGA_BUILD_TIMESTAMP") or os.getenv("BUILD_TIMESTAMP")
        if env_time:
            self.build_timestamp = env_time

    model_config = {"env_file": ".env", "extra": "ignore"}


def get_settings() -> Settings:
    """Create and return a Settings instance."""
    return Settings()
