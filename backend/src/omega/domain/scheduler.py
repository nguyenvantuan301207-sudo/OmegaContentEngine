"""OMEGA-010 Smart Scheduler domain model.

Defines schedule target types, workload categories, actions, reservation states,
policy lifecycle, DST strategies, canonical idempotency, priority scoring,
capacity helpers, and Pydantic schemas.

This is the domain layer — zero infrastructure dependencies.
"""

from __future__ import annotations

import enum
import hashlib
import struct
import zoneinfo
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Enums ──


class ScheduleTargetType(enum.StrEnum):
    """What is being scheduled."""

    TASK_EXECUTION = "TASK_EXECUTION"
    RENDER_JOB = "RENDER_JOB"
    RETRY_OPERATION = "RETRY_OPERATION"
    PUBLISH_INTENT = "PUBLISH_INTENT"
    MISSION_DEFERRED = "MISSION_DEFERRED"


class ScheduleWorkloadCategory(enum.StrEnum):
    """Categorical workload classification for capacity and policy resolution."""

    HEAVY_RENDER = "HEAVY_RENDER"
    AI_GENERATION = "AI_GENERATION"
    LIGHT_API = "LIGHT_API"
    EXTERNAL_PUBLISH = "EXTERNAL_PUBLISH"
    MAINTENANCE = "MAINTENANCE"


class ScheduleAction(enum.StrEnum):
    """Deterministic actions emitted by the Schedule Evaluation Engine."""

    RUN_NOW = "RUN_NOW"
    SCHEDULE = "SCHEDULE"
    DEFER = "DEFER"
    WAITING_DEPENDENCY = "WAITING_DEPENDENCY"
    WAITING_CAPACITY = "WAITING_CAPACITY"
    WAITING_GUARDIAN = "WAITING_GUARDIAN"
    WAITING_NETWORK = "WAITING_NETWORK"
    BLOCKED_SCHEDULE = "BLOCKED_SCHEDULE"


class ReservationState(enum.StrEnum):
    """Authoritative lifecycle states for a ScheduleReservation."""

    ACTIVE = "ACTIVE"
    DISPATCHING = "DISPATCHING"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class SchedulePolicyStatus(enum.StrEnum):
    """Lifecycle status of a versioned SchedulePolicy."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class CapacityScope(enum.StrEnum):
    """Scope for capacity enforcement."""

    GLOBAL = "GLOBAL"
    CHANNEL = "CHANNEL"
    WORKLOAD_CATEGORY = "WORKLOAD_CATEGORY"


class ScheduleRejectReason(enum.StrEnum):
    """Reasons for rejecting a schedule request."""

    OUTSIDE_WINDOW = "OUTSIDE_WINDOW"
    BLACKOUT_PERIOD = "BLACKOUT_PERIOD"
    MIN_GAP_VIOLATION = "MIN_GAP_VIOLATION"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    DEADLINE_UNFEASIBLE = "DEADLINE_UNFEASIBLE"
    GUARDIAN_HELD = "GUARDIAN_HELD"
    NETWORK_HELD = "NETWORK_HELD"
    CHANNEL_PAUSED = "CHANNEL_PAUSED"
    MAX_RETRIES_EXCEEDED = "MAX_RETRIES_EXCEEDED"
    AMBIGUOUS_LOCAL_TIME = "AMBIGUOUS_LOCAL_TIME"
    NONEXISTENT_LOCAL_TIME = "NONEXISTENT_LOCAL_TIME"
    NO_ACTIVE_POLICY = "NO_ACTIVE_POLICY"
    STARVATION_WARNING = "STARVATION_WARNING"


class DispatchOutboxStatus(enum.StrEnum):
    """Transactional outbox delivery status."""

    PENDING = "PENDING"
    SENT = "SENT"
    RETRY = "RETRY"
    DEAD_LETTER = "DEAD_LETTER"


class DispatchFenceResult(enum.StrEnum):
    """Result of pre-dispatch fence validation."""

    PROCEED = "PROCEED"
    STALE_RELEASED = "STALE_RELEASED"
    MISSION_INELIGIBLE = "MISSION_INELIGIBLE"
    TASK_INELIGIBLE = "TASK_INELIGIBLE"


# ── DST Strategy Enums ──


class AmbiguousLocalTimeStrategy(enum.StrEnum):
    """Strategy for resolving ambiguous local times during DST fall-back."""

    FIRST_OCCURRENCE = "FIRST_OCCURRENCE"
    SECOND_OCCURRENCE = "SECOND_OCCURRENCE"
    REJECT = "REJECT"


class NonexistentLocalTimeStrategy(enum.StrEnum):
    """Strategy for resolving nonexistent local times during DST spring-forward."""

    SHIFT_FORWARD = "SHIFT_FORWARD"
    REJECT = "REJECT"


# ── DST Exceptions ──


class AmbiguousLocalTimeError(ValueError):
    """Raised when ambiguous local time is encountered with REJECT strategy."""

    def __init__(self, local_dt: datetime, tz_name: str) -> None:
        self.local_dt = local_dt
        self.tz_name = tz_name
        super().__init__(
            f"Ambiguous local time {local_dt.isoformat()} in timezone {tz_name}. "
            f"Policy strategy is REJECT."
        )


class NonexistentLocalTimeError(ValueError):
    """Raised when nonexistent local time is encountered with REJECT strategy."""

    def __init__(self, local_dt: datetime, tz_name: str) -> None:
        self.local_dt = local_dt
        self.tz_name = tz_name
        super().__init__(
            f"Nonexistent local time {local_dt.isoformat()} in timezone {tz_name}. "
            f"Policy strategy is REJECT."
        )


class ScheduleConflictError(Exception):
    """Raised when a schedule slot allocation conflicts with existing reservations."""


class StaleScheduleError(Exception):
    """Raised when a schedule decision or reservation is based on stale data."""


# ── DST Resolution ──


def resolve_local_to_utc(
    local_dt: datetime,
    tz_name: str,
    ambiguous_strategy: str = AmbiguousLocalTimeStrategy.FIRST_OCCURRENCE,
    nonexistent_strategy: str = NonexistentLocalTimeStrategy.SHIFT_FORWARD,
) -> tuple[datetime, dict[str, Any] | None]:
    """Convert a naive local datetime to UTC with explicit DST policy resolution.

    Returns (utc_datetime, dst_evidence_or_None).
    dst_evidence is populated only when a DST edge case was encountered.
    """
    if local_dt.tzinfo is not None:
        # Already timezone-aware: just convert to UTC
        return local_dt.astimezone(zoneinfo.ZoneInfo("UTC")), None

    tz = zoneinfo.ZoneInfo(tz_name)
    utc_tz = zoneinfo.ZoneInfo("UTC")

    fold0 = local_dt.replace(fold=0, tzinfo=tz)
    fold1 = local_dt.replace(fold=1, tzinfo=tz)

    has_fold_diff = fold0.utcoffset() != fold1.utcoffset()

    if has_fold_diff:
        # Check roundtrips to distinguish ambiguous vs nonexistent (PEP 495)
        u0 = fold0.astimezone(utc_tz)
        u1 = fold1.astimezone(utc_tz)
        r0 = u0.astimezone(tz).replace(tzinfo=None)
        r1 = u1.astimezone(tz).replace(tzinfo=None)

        is_ambiguous = (r0 == local_dt) and (r1 == local_dt)
        is_nonexistent = (r0 != local_dt) and (r1 != local_dt)
    else:
        is_ambiguous = False
        is_nonexistent = False

    if is_ambiguous:
        if ambiguous_strategy == AmbiguousLocalTimeStrategy.REJECT:
            raise AmbiguousLocalTimeError(local_dt, tz_name)
        chosen_fold = 0 if ambiguous_strategy == AmbiguousLocalTimeStrategy.FIRST_OCCURRENCE else 1
        localized = local_dt.replace(fold=chosen_fold, tzinfo=tz)
        utc_result = localized.astimezone(utc_tz)
        dst_evidence = {
            "applied_strategy": ambiguous_strategy,
            "original_local_time": local_dt.isoformat(),
            "timezone": tz_name,
            "resolved_utc": utc_result.isoformat(),
            "was_ambiguous": True,
            "was_nonexistent": False,
        }
        return utc_result, dst_evidence

    if is_nonexistent:
        if nonexistent_strategy == NonexistentLocalTimeStrategy.REJECT:
            raise NonexistentLocalTimeError(local_dt, tz_name)
        # SHIFT_FORWARD: shift forward to first valid local wall-clock time
        localized = local_dt.replace(fold=1, tzinfo=tz)
        utc_result = localized.astimezone(utc_tz)
        shifted_local = utc_result.astimezone(tz)
        dst_evidence = {
            "applied_strategy": nonexistent_strategy,
            "original_local_time": local_dt.isoformat(),
            "timezone": tz_name,
            "resolved_utc": utc_result.isoformat(),
            "shifted_local_time": shifted_local.replace(tzinfo=None).isoformat(),
            "was_ambiguous": False,
            "was_nonexistent": True,
        }
        return utc_result, dst_evidence

    # Normal case: no DST edge
    localized = local_dt.replace(tzinfo=tz)
    utc_result = localized.astimezone(utc_tz)
    return utc_result, None


# ── Time Window Helpers ──


class TimeWindowUTC(BaseModel):
    """A UTC-normalized time window."""

    model_config = ConfigDict(frozen=True)

    start_time: datetime
    end_time: datetime

    @field_validator("start_time", "end_time")
    @classmethod
    def must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware UTC")
        return v.astimezone(zoneinfo.ZoneInfo("UTC"))


# ── Advisory Lock Key ──


def schedule_advisory_lock_key(channel_id: UUID | None, workload_category: str) -> int:
    """Derive a deterministic int64 advisory lock key for slot serialization.

    Uses SHA-256 of the composite key, extracting first 8 bytes as signed int64.
    """
    raw = f"sched:{str(channel_id) if channel_id else 'GLOBAL'}:{workload_category.upper()}"
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return struct.unpack(">q", digest[:8])[0]


# ── Policy Config ──


class DSTConfig(BaseModel):
    """DST resolution strategy configuration within a SchedulePolicy."""

    model_config = ConfigDict(frozen=True)

    ambiguous_local_time_strategy: AmbiguousLocalTimeStrategy = (
        AmbiguousLocalTimeStrategy.FIRST_OCCURRENCE
    )
    nonexistent_local_time_strategy: NonexistentLocalTimeStrategy = (
        NonexistentLocalTimeStrategy.SHIFT_FORWARD
    )


class FairnessConfig(BaseModel):
    """Fairness and anti-starvation configuration within a SchedulePolicy."""

    model_config = ConfigDict(frozen=True)

    age_bonus_per_interval: int = Field(default=5, ge=0)
    age_bonus_interval_seconds: int = Field(default=3600, ge=60)
    age_bonus_cap: int = Field(default=150, ge=0)
    starvation_threshold_seconds: int = Field(default=86400, ge=0)
    per_channel_cycle_cap: int = Field(default=3, ge=1)


class SchedulePolicyConfig(BaseModel):
    """Versioned, immutable policy configuration for scheduling behavior."""

    model_config = ConfigDict(frozen=True)

    workload_category: ScheduleWorkloadCategory
    min_lead_time_seconds: int = Field(default=300, ge=0)
    max_schedule_horizon_days: int = Field(default=30, ge=1, le=365)
    min_gap_between_channel_items_seconds: int = Field(default=7200, ge=0)
    max_scheduled_items_per_day: int = Field(default=5, ge=1, le=100)
    global_concurrency_limit: int = Field(default=10, ge=1, le=1000)
    channel_concurrency_limit: int = Field(default=1, ge=1, le=100)
    retry_backoff_base_seconds: int = Field(default=60, ge=1)
    retry_backoff_factor: float = Field(default=2.0, ge=1.0, le=10.0)
    retry_max_backoff_seconds: int = Field(default=3600, ge=1)
    max_deferrals: int = Field(default=10, ge=0)
    max_urgency_bonus: int = Field(default=50, ge=0)
    dispatching_timeout_seconds: int = Field(default=300, ge=30)
    blackout_windows: list[dict[str, str]] = Field(default_factory=list)
    dst_config: DSTConfig = Field(default_factory=DSTConfig)
    fairness: FairnessConfig = Field(default_factory=FairnessConfig)


def compute_policy_checksum(config: SchedulePolicyConfig) -> str:
    """Compute deterministic SHA-256 checksum of normalized policy config."""
    # Use model_dump with sorted keys for deterministic serialization
    import json

    normalized = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ── Canonical Idempotency ──


def _canon_ts(dt: datetime | None) -> str:
    """Canonicalize a datetime to UTC ISO 8601 string truncated to seconds."""
    if dt is None:
        return "NONE"
    return dt.astimezone(zoneinfo.ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_schedule_idempotency_key(
    *,
    target_type: str,
    target_id: UUID,
    mission_id: UUID,
    task_id: UUID | None = None,
    workload_category: str,
    channel_id: UUID | None = None,
    policy_id: UUID,
    policy_version: str,
    policy_checksum: str,
    channel_dna_revision_id: UUID | None = None,
    guardian_epoch: int,
    earliest_start_at: datetime | None = None,
    latest_start_at: datetime | None = None,
    deadline_at: datetime | None = None,
    estimated_duration_seconds: int = 0,
    priority: int = 1,
    caller_key: str = "",
) -> str:
    """Compute deterministic SHA-256 idempotency key for schedule evaluation."""
    parts = [
        target_type.upper(),
        str(target_id).lower(),
        str(mission_id).lower(),
        str(task_id).lower() if task_id else "NONE",
        workload_category.upper(),
        str(channel_id).lower() if channel_id else "NONE",
        str(policy_id).lower(),
        str(policy_version),
        policy_checksum.lower(),
        str(channel_dna_revision_id).lower() if channel_dna_revision_id else "NONE",
        str(guardian_epoch),
        _canon_ts(earliest_start_at),
        _canon_ts(latest_start_at),
        _canon_ts(deadline_at),
        str(estimated_duration_seconds),
        str(priority),
        caller_key,
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Priority Scoring ──


def compute_priority_score(
    mission_priority: int,
    deadline_at: datetime | None,
    created_at: datetime,
    now: datetime,
    fairness: FairnessConfig,
    max_urgency_bonus: int = 50,
    category_weight: int = 0,
) -> float:
    """Compute deterministic priority score for scheduling.

    PriorityScore = (mission.priority × 100) + DeadlineUrgencyScore
                    + AgeBonusScore + CategoryWeight
    """
    base = mission_priority * 100

    # Deadline urgency: linearly escalates as time-to-deadline approaches 0
    urgency = 0.0
    if deadline_at is not None:
        horizon = timedelta(days=7).total_seconds()
        time_to_deadline = max(0.0, (deadline_at - now).total_seconds())
        if time_to_deadline < horizon:
            urgency = ((horizon - time_to_deadline) / horizon) * max_urgency_bonus

    # Age bonus: points per interval of waiting time, capped
    waiting_seconds = max(0.0, (now - created_at).total_seconds())
    intervals = int(waiting_seconds / fairness.age_bonus_interval_seconds)
    age_bonus = min(fairness.age_bonus_cap, intervals * fairness.age_bonus_per_interval)

    return float(base) + urgency + float(age_bonus) + float(category_weight)


# ── Channel Window Computation ──


def compute_channel_windows_utc(
    target_timezone: str,
    preferred_days: list[str],
    preferred_time_windows: list[str],
    start_date: datetime,
    horizon_days: int = 14,
    dst_config: DSTConfig | None = None,
) -> list[tuple[datetime, datetime, dict[str, Any] | None]]:
    """Compute UTC-normalized candidate windows from channel preferences.

    Returns list of (start_utc, end_utc, dst_evidence_or_None) tuples.
    """
    if dst_config is None:
        dst_config = DSTConfig()

    tz = zoneinfo.ZoneInfo(target_timezone)
    utc_tz = zoneinfo.ZoneInfo("UTC")

    # Normalize start_date to UTC
    if start_date.tzinfo is None:
        start_utc = start_date.replace(tzinfo=utc_tz)
    else:
        start_utc = start_date.astimezone(utc_tz)

    # Convert to local date for iteration
    local_start = start_utc.astimezone(tz)

    day_map = {
        "MONDAY": 0,
        "TUESDAY": 1,
        "WEDNESDAY": 2,
        "THURSDAY": 3,
        "FRIDAY": 4,
        "SATURDAY": 5,
        "SUNDAY": 6,
    }
    preferred_weekdays = {day_map[d.upper()] for d in preferred_days if d.upper() in day_map}

    windows: list[tuple[datetime, datetime, dict[str, Any] | None]] = []

    for day_offset in range(horizon_days):
        current_date = (local_start + timedelta(days=day_offset)).date()
        if current_date.weekday() not in preferred_weekdays:
            continue

        for window_str in preferred_time_windows:
            parts = window_str.strip().split("-")
            if len(parts) != 2:
                continue
            start_parts = parts[0].strip().split(":")
            end_parts = parts[1].strip().split(":")
            if len(start_parts) != 2 or len(end_parts) != 2:
                continue

            start_hour, start_min = int(start_parts[0]), int(start_parts[1])
            end_hour, end_min = int(end_parts[0]), int(end_parts[1])

            local_start_dt = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                start_hour,
                start_min,
            )
            local_end_dt = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                end_hour,
                end_min,
            )

            try:
                start_utc_resolved, start_evidence = resolve_local_to_utc(
                    local_start_dt,
                    target_timezone,
                    ambiguous_strategy=dst_config.ambiguous_local_time_strategy,
                    nonexistent_strategy=dst_config.nonexistent_local_time_strategy,
                )
                end_utc_resolved, end_evidence = resolve_local_to_utc(
                    local_end_dt,
                    target_timezone,
                    ambiguous_strategy=dst_config.ambiguous_local_time_strategy,
                    nonexistent_strategy=dst_config.nonexistent_local_time_strategy,
                )
            except (AmbiguousLocalTimeError, NonexistentLocalTimeError):
                # Strategy is REJECT: skip this window
                continue

            # Use start evidence if available, otherwise end evidence
            evidence = start_evidence or end_evidence

            if end_utc_resolved > start_utc_resolved:
                windows.append((start_utc_resolved, end_utc_resolved, evidence))

    return windows


# ── Capacity Check ──


def check_temporal_overlap(
    candidate_start: datetime,
    candidate_end: datetime,
    existing_start: datetime,
    existing_end: datetime,
) -> bool:
    """Check if two time intervals overlap."""
    return existing_start < candidate_end and existing_end > candidate_start


# ── Pydantic Request/Response Schemas ──


class ScheduleRequest(BaseModel):
    """Request to evaluate and optionally reserve a schedule slot."""

    model_config = ConfigDict(frozen=True)

    mission_id: UUID
    task_id: UUID | None = None
    target_type: ScheduleTargetType
    target_id: UUID
    workload_category: ScheduleWorkloadCategory
    channel_id: UUID | None = None
    channel_dna_revision_id: UUID | None = None
    earliest_start_at: datetime | None = None
    latest_start_at: datetime | None = None
    deadline_at: datetime | None = None
    estimated_duration_seconds: int = Field(default=0, ge=0)
    priority: int = Field(default=1, ge=1, le=100)
    caller_key: str = ""
    guardian_epoch: int = Field(default=1, ge=1)


class ScheduleDecisionResponse(BaseModel):
    """Authoritative scheduling decision response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    idempotency_key: str
    action: ScheduleAction
    scheduled_start_at: datetime | None = None
    scheduled_end_at: datetime | None = None
    reason: str
    policy_id: UUID
    policy_version: str
    policy_checksum: str
    channel_dna_revision_id: UUID | None = None
    reservation_id: UUID | None = None
    guardian_epoch: int
    diagnostic_context: dict[str, Any] | None = None
    evaluated_at: datetime
    expires_at: datetime | None = None


class ScheduleReservationResponse(BaseModel):
    """Reservation slot information."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel_id: UUID | None = None
    target_type: str
    target_id: UUID
    workload_category: str
    scheduled_start_at: datetime
    scheduled_end_at: datetime
    state: ReservationState
    priority_score: float
    guardian_epoch: int
    version: int
    created_at: datetime
    updated_at: datetime


class SchedulePolicyResponse(BaseModel):
    """Schedule policy information."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workload_category: str
    version: str
    status: SchedulePolicyStatus
    policy_config: dict[str, Any]
    checksum: str
    effective_at: datetime | None = None
    retired_at: datetime | None = None
    created_at: datetime


class SchedulePolicyCreate(BaseModel):
    """Request to create a new schedule policy."""

    workload_category: ScheduleWorkloadCategory
    version: str = Field(min_length=1, max_length=50)
    policy_config: dict[str, Any]
    activate: bool = False


class SchedulePolicyActivate(BaseModel):
    """Request to activate a DRAFT policy."""

    pass


class ChannelTimelineResponse(BaseModel):
    """Timeline view of scheduled reservations for a channel."""

    channel_id: UUID
    timezone: str
    reservations: list[ScheduleReservationResponse]
    capacity_used_today: int = 0
    capacity_limit_today: int = 0
