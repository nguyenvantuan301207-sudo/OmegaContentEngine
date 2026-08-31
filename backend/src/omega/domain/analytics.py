"""Domain models, enums, schemas, and deterministic helpers for OMEGA-012 Analytics Engine.

This is the domain layer — zero infrastructure/database dependencies.
"""

from __future__ import annotations

import enum
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


# ── Canonical Enums ────────────────────────────────────────────────────────────


class MetricQuality(enum.StrEnum):
    """Quality and availability state of an individual metric value."""

    AVAILABLE = "AVAILABLE"
    ZERO_CONFIRMED = "ZERO_CONFIRMED"
    NOT_READY = "NOT_READY"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    SUPPRESSED = "SUPPRESSED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    STALE = "STALE"
    REVISED = "REVISED"
    UNKNOWN_MISSING = "UNKNOWN_MISSING"


class MetricClassification(enum.StrEnum):
    """Authoritative semantic classification of metrics."""

    PROVIDER_FACT = "PROVIDER_FACT"
    DETERMINISTIC_DERIVED = "DETERMINISTIC_DERIVED"
    HEURISTIC_FEATURE = "HEURISTIC_FEATURE"


class WindowType(enum.StrEnum):
    """Standardized temporal evaluation window types."""

    FIRST_1H = "FIRST_1H"
    FIRST_6H = "FIRST_6H"
    FIRST_24H = "FIRST_24H"
    FIRST_72H = "FIRST_72H"
    FIRST_7D = "FIRST_7D"
    FIRST_14D = "FIRST_14D"
    FIRST_30D = "FIRST_30D"
    LIFETIME = "LIFETIME"
    CALENDAR_DAY = "CALENDAR_DAY"


class WindowState(enum.StrEnum):
    """Lifecycle progression of an evaluation window."""

    PENDING = "PENDING"
    PROVISIONAL = "PROVISIONAL"
    FINALIZED = "FINALIZED"
    REVISED = "REVISED"


class AnalyticsAssetStatus(enum.StrEnum):
    """Lifecycle status of a tracked video asset in Analytics."""

    ACTIVE = "ACTIVE"
    MATURE = "MATURE"
    ARCHIVED = "ARCHIVED"
    PROVIDER_DELETED = "PROVIDER_DELETED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class AnalyticsLifecyclePhase(enum.StrEnum):
    """Polling cadence phase determined by asset age."""

    ACTIVE_HOURLY = "ACTIVE_HOURLY"
    MATURE_DAILY = "MATURE_DAILY"
    STABLE_WEEKLY = "STABLE_WEEKLY"
    ARCHIVED_MONTHLY = "ARCHIVED_MONTHLY"
    PAUSED = "PAUSED"


class AnalyticsPollJobStatus(enum.StrEnum):
    """Fenced state of an analytics background poll job."""

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class AvailabilityClass(enum.StrEnum):
    """Categorical availability rules for provider capabilities."""

    UNIVERSAL = "UNIVERSAL"
    DIMENSION_RESTRICTED = "DIMENSION_RESTRICTED"
    PRIVACY_GATED = "PRIVACY_GATED"
    PROVIDER_GATED = "PROVIDER_GATED"
    CHANNEL_ONLY = "CHANNEL_ONLY"
    DEPRECATED = "DEPRECATED"


class AnalyticsErrorCategory(enum.StrEnum):
    """Structured error taxonomy with retryability semantics."""

    AUTH_REVOKED = "AUTH_REVOKED"
    AUTH_SCOPE_MISSING = "AUTH_SCOPE_MISSING"
    NETWORK_BLOCKED = "NETWORK_BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    PROVIDER_TRANSIENT = "PROVIDER_TRANSIENT"
    PROVIDER_PERMANENT = "PROVIDER_PERMANENT"
    VIDEO_NOT_FOUND = "VIDEO_NOT_FOUND"
    VIDEO_UNAVAILABLE = "VIDEO_UNAVAILABLE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    NORMALIZATION_ERROR = "NORMALIZATION_ERROR"
    STALE_DATA = "STALE_DATA"
    FENCING_REJECTED = "FENCING_REJECTED"


# ── Timezone & Temporal Engine Helpers ──────────────────────────────────────────


def compute_pacific_calendar_day_utc_range(target_date: date) -> tuple[datetime, datetime]:
    """Calculate exact UTC wall-clock instants for a Pacific local calendar date.

    Handles DST transitions (23h Spring-Forward, 25h Fall-Back, 24h Normal).
    """
    start_pacific = datetime(
        target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=PACIFIC_TZ
    )
    next_day = target_date + timedelta(days=1)
    end_pacific = datetime(next_day.year, next_day.month, next_day.day, 0, 0, 0, tzinfo=PACIFIC_TZ)
    return start_pacific.astimezone(UTC), end_pacific.astimezone(UTC)


def compute_relative_window_utc_range(
    published_at: datetime, window_type: WindowType
) -> tuple[datetime, datetime]:
    """Calculate exact elapsed UTC duration range from published_at instant.

    Relative windows are strictly independent of provider calendar timezones.
    """
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    else:
        published_at = published_at.astimezone(UTC)

    duration_map: dict[WindowType, timedelta] = {
        WindowType.FIRST_1H: timedelta(hours=1),
        WindowType.FIRST_6H: timedelta(hours=6),
        WindowType.FIRST_24H: timedelta(hours=24),
        WindowType.FIRST_72H: timedelta(hours=72),
        WindowType.FIRST_7D: timedelta(days=7),
        WindowType.FIRST_14D: timedelta(days=14),
        WindowType.FIRST_30D: timedelta(days=30),
    }

    if window_type in duration_map:
        return published_at, published_at + duration_map[window_type]
    elif window_type == WindowType.LIFETIME:
        return published_at, datetime.now(UTC)
    else:
        raise ValueError(f"Cannot compute relative window for calendar type {window_type}")


def compute_quota_reset_instants(now_utc: datetime) -> tuple[datetime, datetime]:
    """Calculate exact UTC wall-clock instants for last and next Pacific midnight resets.

    Google API quota resets at midnight Pacific Time (America/Los_Angeles).
    """
    now_utc = now_utc.replace(tzinfo=UTC) if now_utc.tzinfo is None else now_utc.astimezone(UTC)

    now_pacific = now_utc.astimezone(PACIFIC_TZ)
    today_pacific = now_pacific.date()

    last_reset_pacific = datetime(
        today_pacific.year, today_pacific.month, today_pacific.day, 0, 0, 0, tzinfo=PACIFIC_TZ
    )
    last_reset_utc = last_reset_pacific.astimezone(UTC)

    next_reset_pacific = last_reset_pacific + timedelta(days=1)
    next_reset_utc = next_reset_pacific.astimezone(UTC)

    # Edge boundary check
    if now_utc < last_reset_utc:
        next_reset_utc = last_reset_utc
        last_reset_utc = (last_reset_pacific - timedelta(days=1)).astimezone(UTC)

    return last_reset_utc, next_reset_utc


# ── Deterministic Identity Helpers ──────────────────────────────────────────────


def compute_payload_checksum(raw_content: bytes | str) -> str:
    """Compute SHA-256 checksum of raw payload content."""
    data = raw_content.encode("utf-8") if isinstance(raw_content, str) else raw_content
    return hashlib.sha256(data).hexdigest()


def compute_logical_query_key(
    provider: str,
    platform_account_id: UUID,
    provider_video_id: str | None,
    api_source: str,
    canonical_params_hash: str,
    provider_window_or_date: str,
    dimensions_hash: str,
) -> str:
    """Compute deterministic key representing the query intent."""
    raw = (
        f"{provider}:{platform_account_id}:{provider_video_id or ''}:{api_source}:"
        f"{canonical_params_hash}:{provider_window_or_date}:{dimensions_hash}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_poll_execution_key(asset_id: UUID, execution_context: str) -> str:
    """Compute deterministic key representing the intended fetch cycle."""
    raw = f"{asset_id}:{execution_context}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_snapshot_dedupe_key(poll_execution_key: str, payload_checksum: str) -> str:
    """Compute physical snapshot deduplication key."""
    raw = f"{poll_execution_key}:{payload_checksum}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_observation_dedupe_key(
    snapshot_id: UUID,
    window_id: UUID,
    metric_name: str,
    dimensions_hash: str,
    normalization_version: int,
) -> str:
    """Compute deterministic identity for a normalized observation to prevent duplicate ingestion."""
    raw = f"{snapshot_id}:{window_id}:{metric_name}:{dimensions_hash}:{normalization_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_computation_dedupe_key(
    window_id: UUID,
    metric_name: str,
    formula_identifier: str,
    formula_version: str,
    source_checksum: str,
) -> str:
    """Compute deterministic identity for a derived or heuristic metric calculation."""
    raw = f"{window_id}:{metric_name}:{formula_identifier}:{formula_version}:{source_checksum}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Domain Schemas & Contracts ──────────────────────────────────────────────────


class ProviderMetricCapability(BaseModel):
    """Specification of provider metric availability and gating."""

    model_config = ConfigDict(frozen=True)

    provider: str
    canonical_metric_name: str
    provider_metric_name: str
    api_source: str
    supported_report_types: list[str] = Field(default_factory=list)
    compatible_dimensions: list[str] = Field(default_factory=list)
    required_scopes: list[str] = Field(default_factory=list)
    availability_class: AvailabilityClass = AvailabilityClass.UNIVERSAL
    unit: str = "COUNT"
    aggregation_semantics: str = "SUM"
    capability_version: int = 1


class LearningObservation(BaseModel):
    """Immutable, finalized observation contract exported for OMEGA-013 Learning."""

    model_config = ConfigDict(frozen=True)

    observation_schema_version: int = 1

    # Identity & Lineage
    observation_id: UUID
    channel_id: UUID
    publish_intent_id: UUID
    provider_video_id: str
    media_artifact_id: UUID
    channel_dna_revision_id: UUID | None
    published_at_utc: datetime

    # Window
    window_type: WindowType
    window_state: WindowState
    window_start_utc: datetime
    window_end_utc: datetime

    # Facts & Deterministic Derived Metrics (Heuristics excluded by default)
    metrics: dict[str, float | int | None]
    metric_qualities: dict[str, MetricQuality]
    classifications: dict[str, MetricClassification]

    # Data Quality
    is_fully_finalized: bool
    quality_flags: list[str] = Field(default_factory=list)


EMPTY_DIMENSIONS_HASH: str = ""

CANONICAL_COMPUTED_FORMULAS: dict[str, tuple[str, str]] = {
    "view_velocity_per_hour": ("FORMULA_VIEW_VELOCITY_V1", "1"),
    "like_ratio_percent": ("FORMULA_LIKE_RATIO_V1", "1"),
    "comment_ratio_percent": ("FORMULA_COMMENT_RATIO_V1", "1"),
    "engagement_rate_percent": ("FORMULA_ENGAGEMENT_RATE_V1", "1"),
    "net_subscribers": ("FORMULA_NET_SUBSCRIBERS_V1", "1"),
}


class AnalyticsExportContractError(Exception):
    """Raised when an analytics export contract violation occurs."""


class ExportCandidate(BaseModel):
    """Candidate window identified for downstream learning ingestion."""

    model_config = ConfigDict(frozen=True)

    window_id: UUID
    asset_id: UUID
    window_type: WindowType
    window_state: WindowState
    updated_at: datetime


def compute_learning_observation_checksum(obs: LearningObservation) -> str:
    """Compute deterministic SHA-256 checksum over semantic Analytics state only.

    Excludes non-semantic database timestamps, execution times, or worker IDs.
    Keys are sorted, enums converted to string values, and quality flags sorted unique.
    """
    canonical_dict = {
        "observation_id": str(obs.observation_id),
        "channel_id": str(obs.channel_id),
        "publish_intent_id": str(obs.publish_intent_id),
        "provider_video_id": obs.provider_video_id,
        "media_artifact_id": str(obs.media_artifact_id),
        "channel_dna_revision_id": str(obs.channel_dna_revision_id)
        if obs.channel_dna_revision_id
        else None,
        "published_at_utc": obs.published_at_utc.isoformat(),
        "window_type": obs.window_type.value,
        "window_state": obs.window_state.value,
        "window_start_utc": obs.window_start_utc.isoformat(),
        "window_end_utc": obs.window_end_utc.isoformat(),
        "metrics": dict(sorted(obs.metrics.items())),
        "metric_qualities": {k: v.value for k, v in sorted(obs.metric_qualities.items())},
        "classifications": {k: v.value for k, v in sorted(obs.classifications.items())},
        "is_fully_finalized": obs.is_fully_finalized,
        "quality_flags": sorted(list(set(obs.quality_flags))),
    }
    raw_bytes = json.dumps(canonical_dict, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw_bytes).hexdigest()
