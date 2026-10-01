"""Domain model and pure recurrence calculation engine for OMEGA P20-A.

Zero database dependencies.
Enforces typed payloads, deterministic cron/interval calculations, PEP 495 fold-aware
DST conversions, catch-up policies, and schedule state invariants.
"""

from __future__ import annotations

import calendar
import enum
import zoneinfo
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── StrEnum Definitions ──


class RecurringScheduleStatus(enum.StrEnum):
    """Authoritative lifecycle state of a RecurringSchedule."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ARCHIVED = "ARCHIVED"


class RecurringScheduleTargetType(enum.StrEnum):
    """Downstream execution authority target."""

    CAMPAIGN_ADMISSION = "CAMPAIGN_ADMISSION"
    STANDALONE_MISSION = "STANDALONE_MISSION"


class CatchUpPolicy(enum.StrEnum):
    """Policy governing overdue occurrences after downtime."""

    SKIP_MISSED = "SKIP_MISSED"
    RUN_LATEST_ONLY = "RUN_LATEST_ONLY"
    RUN_ALL_BOUNDED = "RUN_ALL_BOUNDED"


class DSTAmbiguousStrategy(enum.StrEnum):
    """Resolution strategy for repeated local wall-clock times (fall-back overlap)."""

    FIRST = "FIRST"      # First chronological instant (fold=0 in standard/daylight transition)
    SECOND = "SECOND"    # Second chronological instant (fold=1)
    REJECT = "REJECT"    # Fail evaluation


class DSTNonexistentStrategy(enum.StrEnum):
    """Resolution strategy for nonexistent local wall-clock times (spring-forward gap)."""

    NEXT_VALID = "NEXT_VALID"  # Advance clock to next valid instant past gap
    SKIP = "SKIP"              # Omit this occurrence
    REJECT = "REJECT"          # Fail evaluation


class RecurringOccurrenceStatus(enum.StrEnum):
    """State machine for a single logical occurrence."""

    PENDING = "PENDING"
    WAITING = "WAITING"
    DISPATCHING = "DISPATCHING"
    DISPATCHED = "DISPATCHED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# ── Typed Payload Contracts ──


class CampaignAdmissionPayload(BaseModel):
    """Campaign admission target requires zero parameters; pulls from active campaign items."""

    model_config = ConfigDict(extra="forbid")


class StandaloneMissionPayload(BaseModel):
    """Strictly maps only allowable MissionCreate parameters for standalone schedules."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=1)
    description: str | None = None
    autonomy_level: str = Field(default="SUPERVISED")
    priority: int = Field(default=1, ge=1, le=10)

    @field_validator("autonomy_level")
    @classmethod
    def validate_autonomy(cls, v: str) -> str:
        allowed = {"ASSISTED", "SUPERVISED", "AUTONOMOUS"}
        if v not in allowed:
            raise ValueError(f"autonomy_level must be one of {allowed}, got '{v}'")
        return v


def validate_target_payload(target_type: RecurringScheduleTargetType | str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate payload against target-specific typed contract and prohibit secrets."""
    t_type = RecurringScheduleTargetType(str(target_type))
    if t_type == RecurringScheduleTargetType.CAMPAIGN_ADMISSION:
        validated = CampaignAdmissionPayload.model_validate(payload)
        return validated.model_dump(mode="json")
    elif t_type == RecurringScheduleTargetType.STANDALONE_MISSION:
        validated = StandaloneMissionPayload.model_validate(payload)
        return validated.model_dump(mode="json")
    raise ValueError(f"Unsupported target type: {target_type}")


# ── PEP 495 Fold-Aware DST Resolver ──


class DSTAmbiguousError(ValueError):
    """Raised when an ambiguous wall time is encountered with strategy REJECT."""


class DSTNonexistentError(ValueError):
    """Raised when a nonexistent wall time is encountered with strategy REJECT."""


def resolve_local_wall_to_utc(
    dt_naive: datetime,
    tz_name: str,
    ambiguous_strategy: DSTAmbiguousStrategy = DSTAmbiguousStrategy.FIRST,
    nonexistent_strategy: DSTNonexistentStrategy = DSTNonexistentStrategy.NEXT_VALID,
) -> datetime | None:
    """Convert naive local wall-clock datetime to UTC respecting PEP 495 fold and strategies.

    Returns UTC datetime, or None if nonexistent_strategy is SKIP and dt_naive falls in gap.
    """
    tz = zoneinfo.ZoneInfo(tz_name)
    utc = timezone.utc

    fold0 = dt_naive.replace(tzinfo=tz, fold=0)
    fold1 = dt_naive.replace(tzinfo=tz, fold=1)

    utc0 = fold0.astimezone(utc)
    utc1 = fold1.astimezone(utc)

    back0 = utc0.astimezone(tz).replace(tzinfo=None)
    back1 = utc1.astimezone(tz).replace(tzinfo=None)

    # Ambiguous: both folds round-trip back to dt_naive, but map to different UTC instants
    if back0 == dt_naive and back1 == dt_naive:
        if utc0 != utc1:
            if ambiguous_strategy == DSTAmbiguousStrategy.REJECT:
                raise DSTAmbiguousError(f"Ambiguous local time {dt_naive} in timezone {tz_name}")
            first_utc = min(utc0, utc1)
            second_utc = max(utc0, utc1)
            return first_utc if ambiguous_strategy == DSTAmbiguousStrategy.FIRST else second_utc
        return utc0

    # Nonexistent (gap / spring-forward): neither fold round-trips back to dt_naive
    if back0 != dt_naive and back1 != dt_naive:
        if nonexistent_strategy == DSTNonexistentStrategy.REJECT:
            raise DSTNonexistentError(f"Nonexistent local time {dt_naive} in timezone {tz_name}")
        elif nonexistent_strategy == DSTNonexistentStrategy.SKIP:
            return None
        elif nonexistent_strategy == DSTNonexistentStrategy.NEXT_VALID:
            # Step forward minute-by-minute until reaching valid wall clock
            cand = dt_naive + timedelta(minutes=1)
            while True:
                c_utc = cand.replace(tzinfo=tz, fold=0).astimezone(utc)
                if c_utc.astimezone(tz).replace(tzinfo=None) == cand:
                    return c_utc
                cand += timedelta(minutes=1)

    return utc0


# ── Pure Deterministic Cron Parser (5 fields) ──


def _parse_cron_field(field_str: str, min_val: int, max_val: int, is_dow: bool = False) -> set[int]:
    """Parse a single cron field specification into a set of allowed integer values."""
    field_str = field_str.strip()
    if field_str == "*":
        return set(range(min_val, max_val + 1))

    results: set[int] = set()
    parts = field_str.split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            range_part, step_str = part.split("/", 1)
            step = int(step_str)
            if step <= 0:
                raise ValueError(f"Invalid cron step: {step_str}")
        else:
            range_part = part

        if range_part == "*":
            start, end = min_val, max_val
        elif "-" in range_part:
            start_str, end_str = range_part.split("-", 1)
            start, end = int(start_str), int(end_str)
        else:
            start = end = int(range_part)

        if not is_dow and (start < min_val or end > max_val):
            raise ValueError(f"Cron value {range_part} out of range [{min_val}, {max_val}]")

        if is_dow:
            if (start < 0 or start > 7) or (end < 0 or end > 7):
                raise ValueError(f"Cron day of week {range_part} out of range [0, 7]")
            # In cron: 0=Sun, 6=Sat, and 7=Sun
            if start == 7:
                start = 0
            if end == 7:
                end = 0

        if start > end and not is_dow:
            raise ValueError(f"Invalid cron range: {range_part}")

        if is_dow and start > end:
            # Wrap-around day of week (e.g. Fri-Mon: 5-1)
            for v in range(start, 7, step):
                results.add(v)
            for v in range(0, end + 1, step):
                results.add(v)
        else:
            for v in range(start, end + 1, step):
                if min_val <= v <= max_val or (is_dow and v == 0):
                    results.add(0 if is_dow and v == 7 else v)

    return results


class CronSpec:
    """Compiled 5-field cron specification."""

    def __init__(self, expression: str) -> None:
        self.raw_expression = expression.strip()
        tokens = self.raw_expression.split()
        if len(tokens) != 5:
            raise ValueError(
                f"Cron expression must contain exactly 5 fields, got {len(tokens)}: '{expression}'"
            )
        self.minutes = _parse_cron_field(tokens[0], 0, 59)
        self.hours = _parse_cron_field(tokens[1], 0, 23)
        self.days_of_month = _parse_cron_field(tokens[2], 1, 31)
        self.months = _parse_cron_field(tokens[3], 1, 12)
        self.days_of_week = _parse_cron_field(tokens[4], 0, 6, is_dow=True)
        self.dom_any = tokens[2] == "*"
        self.dow_any = tokens[4] == "*"

    def matches(self, dt: datetime) -> bool:
        """Check if local datetime matches cron pattern."""
        if dt.minute not in self.minutes:
            return False
        if dt.hour not in self.hours:
            return False
        if dt.month not in self.months:
            return False

        # In standard cron: if both DOM and DOW are specified (neither is *), match if EITHER matches.
        # If either is *, match must satisfy the non-* field.
        dom_match = dt.day in self.days_of_month
        # Python weekday: Mon=0 .. Sun=6. Cron weekday: Sun=0, Mon=1 .. Sat=6.
        cron_dow = (dt.weekday() + 1) % 7
        dow_match = cron_dow in self.days_of_week

        if not self.dom_any and not self.dow_any:
            return dom_match or dow_match
        if not self.dom_any:
            return dom_match
        if not self.dow_any:
            return dow_match
        return True


# ── Recurrence Next Run Calculation Engine ──


def calculate_next_occurrence(
    *,
    cron_expression: str | None,
    interval_seconds: int | None,
    timezone_str: str,
    reference_time_utc: datetime,
    dst_ambiguous: DSTAmbiguousStrategy = DSTAmbiguousStrategy.FIRST,
    dst_nonexistent: DSTNonexistentStrategy = DSTNonexistentStrategy.NEXT_VALID,
    start_time_utc: datetime | None = None,
    end_time_utc: datetime | None = None,
) -> datetime | None:
    """Calculate the next authoritative UTC occurrence time.

    Reference time must be timezone-aware (normalized to UTC).
    Returns None if schedule has expired past end_time_utc.
    """
    if reference_time_utc.tzinfo is None:
        raise ValueError("reference_time_utc must be timezone-aware")

    if cron_expression is not None and interval_seconds is not None:
        raise ValueError("Cannot specify both cron_expression and interval_seconds")
    if cron_expression is None and interval_seconds is None:
        raise ValueError("Either cron_expression or interval_seconds must be provided")

    ref_utc = reference_time_utc.astimezone(timezone.utc)
    effective_start_utc = ref_utc
    if start_time_utc is not None:
        s_utc = start_time_utc.astimezone(timezone.utc)
        if s_utc > effective_start_utc:
            effective_start_utc = s_utc - timedelta(seconds=1)

    # 1. Interval recurrence
    if interval_seconds is not None:
        if interval_seconds < 60:
            raise ValueError(f"interval_seconds must be >= 60, got {interval_seconds}")
        if start_time_utc is not None and ref_utc < start_time_utc.astimezone(timezone.utc):
            next_dt = start_time_utc.astimezone(timezone.utc)
        else:
            next_dt = ref_utc + timedelta(seconds=interval_seconds)
        if end_time_utc is not None and next_dt > end_time_utc.astimezone(timezone.utc):
            return None
        return next_dt

    # 2. Cron recurrence
    if cron_expression is None:
        raise ValueError("Either cron_expression or interval_seconds must be provided")

    cron = CronSpec(cron_expression)
    tz = zoneinfo.ZoneInfo(timezone_str)

    # Convert effective start to local wall-clock
    local_start = effective_start_utc.astimezone(tz)
    # Search forward minute-by-minute starting from the next minute boundary
    candidate_naive = (local_start.replace(second=0, microsecond=0) + timedelta(minutes=1)).replace(tzinfo=None)

    # Search up to 5 years (approx 2,628,000 minutes) to avoid infinite loop
    max_search_minutes = 366 * 24 * 60
    minutes_searched = 0

    while minutes_searched < max_search_minutes:
        if cron.matches(candidate_naive):
            utc_cand = resolve_local_wall_to_utc(
                candidate_naive,
                timezone_str,
                ambiguous_strategy=dst_ambiguous,
                nonexistent_strategy=dst_nonexistent,
            )
            if utc_cand is not None and utc_cand > ref_utc:
                if start_time_utc is not None and utc_cand < start_time_utc.astimezone(timezone.utc):
                    candidate_naive += timedelta(minutes=1)
                    minutes_searched += 1
                    continue
                if end_time_utc is not None and utc_cand > end_time_utc.astimezone(timezone.utc):
                    return None
                return utc_cand

        candidate_naive += timedelta(minutes=1)
        minutes_searched += 1

    return None


# ── Catch-Up Evaluator ──


def partition_catch_up_occurrences(
    *,
    missed_occurrences: list[datetime],
    policy: CatchUpPolicy,
    max_catch_up: int,
) -> tuple[list[datetime], list[datetime]]:
    """Partition overdue missed occurrences into (to_dispatch, to_skip).

    Guarantees that to_dispatch never exceeds max_catch_up.
    """
    if not missed_occurrences:
        return [], []

    sorted_missed = sorted(missed_occurrences)

    if policy == CatchUpPolicy.SKIP_MISSED:
        # Skip all missed runs during downtime
        return [], sorted_missed

    if policy == CatchUpPolicy.RUN_LATEST_ONLY:
        # Run only the most recent missed occurrence, skip the rest
        to_dispatch = [sorted_missed[-1]]
        to_skip = sorted_missed[:-1]
        return to_dispatch, to_skip

    if policy == CatchUpPolicy.RUN_ALL_BOUNDED:
        # Bound execution to at most max_catch_up
        bound = max(1, min(max_catch_up, 10))  # Capped by HARD_MAX_CATCH_UP_CAP
        if len(sorted_missed) <= bound:
            return sorted_missed, []
        to_skip = sorted_missed[:-bound]
        to_dispatch = sorted_missed[-bound:]
        return to_dispatch, to_skip

    raise ValueError(f"Unknown catch up policy: {policy}")


# ── Pydantic Request/Response Models ──


class RecurringScheduleCreate(BaseModel):
    """Creation payload for a new recurring schedule."""

    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    start_time: datetime
    end_time: datetime | None = None
    cron_expression: str | None = None
    interval_seconds: int | None = None
    timezone: str = Field(default="UTC")
    dst_ambiguous_strategy: DSTAmbiguousStrategy = Field(default=DSTAmbiguousStrategy.FIRST)
    dst_nonexistent_strategy: DSTNonexistentStrategy = Field(default=DSTNonexistentStrategy.NEXT_VALID)
    catch_up_policy: CatchUpPolicy = Field(default=CatchUpPolicy.SKIP_MISSED)
    max_catch_up_occurrences: int = Field(default=3, ge=1, le=10)
    target_type: RecurringScheduleTargetType
    target_id: UUID
    payload_template: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timezone")
    @classmethod
    def validate_tz(cls, v: str) -> str:
        try:
            zoneinfo.ZoneInfo(v)
        except Exception as exc:
            raise ValueError(f"Invalid timezone: {v}") from exc
        return v

    @field_validator("interval_seconds")
    @classmethod
    def validate_interval(cls, v: int | None) -> int | None:
        if v is not None and v < 60:
            raise ValueError(f"interval_seconds must be >= 60, got {v}")
        return v


class RecurringScheduleUpdate(BaseModel):
    """Update payload for modifying a recurring schedule."""

    name: str | None = Field(default=None, max_length=128)
    description: str | None = None
    end_time: datetime | None = None
    cron_expression: str | None = None
    interval_seconds: int | None = None
    timezone: str | None = None
    dst_ambiguous_strategy: DSTAmbiguousStrategy | None = None
    dst_nonexistent_strategy: DSTNonexistentStrategy | None = None
    catch_up_policy: CatchUpPolicy | None = None
    max_catch_up_occurrences: int | None = Field(default=None, ge=1, le=10)
    target_type: RecurringScheduleTargetType | None = None
    target_id: UUID | None = None
    payload_template: dict[str, Any] | None = None


class RecurringScheduleVersionResponse(BaseModel):
    """Representation of an immutable schedule definition version."""

    id: UUID
    schedule_id: UUID
    version_number: int
    cron_expression: str | None
    interval_seconds: int | None
    timezone: str
    dst_ambiguous_strategy: str
    dst_nonexistent_strategy: str
    catch_up_policy: str
    max_catch_up_occurrences: int
    target_type: str
    target_id: UUID
    payload_template: dict[str, Any]
    created_at: datetime


class RecurringScheduleResponse(BaseModel):
    """Full representation of a recurring schedule."""

    id: UUID
    name: str
    description: str | None
    status: str
    current_version_id: UUID | None
    start_time: datetime
    end_time: datetime | None
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    current_version: RecurringScheduleVersionResponse | None = None


class RecurringOccurrenceResponse(BaseModel):
    """Representation of an occurrence ledger row."""

    id: UUID
    schedule_id: UUID
    schedule_version_id: UUID
    occurrence_at: datetime
    status: str
    idempotency_key: str
    downstream_target_type: str
    downstream_target_id: UUID | None
    attempt_count: int
    last_attempt_at: datetime | None
    wait_deadline_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class TimelinePreviewRequest(BaseModel):
    """Request for previewing next N occurrences without creating a schedule."""

    cron_expression: str | None = None
    interval_seconds: int | None = None
    timezone: str = Field(default="UTC")
    dst_ambiguous_strategy: DSTAmbiguousStrategy = Field(default=DSTAmbiguousStrategy.FIRST)
    dst_nonexistent_strategy: DSTNonexistentStrategy = Field(default=DSTNonexistentStrategy.NEXT_VALID)
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: datetime | None = None
    count: int = Field(default=5, ge=1, le=50)


class TimelinePreviewResponse(BaseModel):
    """Response containing calculated occurrence preview timestamps."""

    occurrences_utc: list[datetime]
    count: int
