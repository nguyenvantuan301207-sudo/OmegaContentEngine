"""Unit tests for Recurring Schedule domain, recurrence engine, and configuration bounds."""

from __future__ import annotations

import zoneinfo
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from omega.config import (
    SCHEMA_MAX_CATCH_UP_OCCURRENCES_CAP,
    SCHEMA_MINIMUM_INTERVAL_SECONDS,
    Settings,
)
from omega.domain.recurring_schedule import (
    CampaignAdmissionPayload,
    CatchUpPolicy,
    CronSpec,
    DSTAmbiguousStrategy,
    DSTNonexistentStrategy,
    RecurringOccurrenceStatus,
    RecurringScheduleCreate,
    RecurringScheduleStatus,
    RecurringScheduleTargetType,
    RecurringScheduleUpdate,
    StandaloneMissionPayload,
    calculate_next_occurrence,
    partition_catch_up_occurrences,
    resolve_local_wall_to_utc,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Hard Schema Bounds vs Configurable Runtime Defaults
# ─────────────────────────────────────────────────────────────────────────────

def test_hard_schema_bounds_constants() -> None:
    """Validate that hard schema safety bounds are explicitly defined."""
    assert SCHEMA_MINIMUM_INTERVAL_SECONDS == 60
    assert SCHEMA_MAX_CATCH_UP_OCCURRENCES_CAP == 10


def test_settings_valid_defaults() -> None:
    """Validate that default settings respect hard schema bounds."""
    settings = Settings()
    assert settings.recurring_scheduler_enabled is False
    assert settings.scheduler_minimum_interval_seconds >= SCHEMA_MINIMUM_INTERVAL_SECONDS
    assert settings.scheduler_default_max_catch_up <= SCHEMA_MAX_CATCH_UP_OCCURRENCES_CAP


def test_settings_reject_interval_below_schema_bound() -> None:
    """Runtime config must never be looser than the DB schema check constraint."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(scheduler_minimum_interval_seconds=30)
    assert "cannot be less than HARD_SCHEMA_SAFETY_BOUND" in str(exc_info.value)


def test_settings_reject_catch_up_above_schema_bound() -> None:
    """Runtime config must never allow more catch-up occurrences than DB cap."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(scheduler_default_max_catch_up=15)
    assert "HARD_SCHEMA_SAFETY_BOUND" in str(exc_info.value)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Typed Payloads & Security
# ─────────────────────────────────────────────────────────────────────────────

def test_campaign_admission_payload_strictly_empty() -> None:
    """CAMPAIGN_ADMISSION target payload must be strictly empty dict."""
    payload = CampaignAdmissionPayload()
    assert payload.model_dump() == {}

    with pytest.raises(ValidationError):
        CampaignAdmissionPayload.model_validate({"extra_param": "forbidden"})


def test_standalone_mission_payload_validation() -> None:
    """STANDALONE_MISSION target payload validates supported fields and forbids extra."""
    valid_data = {
        "title": "Weekly Market Recap",
        "objective": "Comprehensive analysis of tech sector",
        "description": "Tech analysis",
        "autonomy_level": "SUPERVISED",
        "priority": 1,
    }
    payload = StandaloneMissionPayload(**valid_data)
    assert payload.title == "Weekly Market Recap"
    assert payload.objective == "Comprehensive analysis of tech sector"

    # Reject unknown fields (extra="forbid")
    with pytest.raises(ValidationError):
        StandaloneMissionPayload(**{**valid_data, "arbitrary_executable": True})

    # Reject credential / secret leak attempts
    with pytest.raises(ValidationError):
        StandaloneMissionPayload(**{**valid_data, "api_key": "secret_123"})


# ─────────────────────────────────────────────────────────────────────────────
# 3. Deterministic Cron Parser (CronSpec)
# ─────────────────────────────────────────────────────────────────────────────

def test_cronspec_standard_patterns() -> None:
    """Test 5-field cron parsing for standard expressions."""
    cron = CronSpec("0 12 * * *")  # Every day at 12:00
    assert 0 in cron.minutes
    assert 12 in cron.hours
    assert len(cron.days_of_month) == 31
    assert len(cron.months) == 12
    assert len(cron.days_of_week) == 7

    cron_step = CronSpec("*/15 8-18 * * 1-5")  # Every 15 min between 8-18 on weekdays
    assert cron_step.minutes == {0, 15, 30, 45}
    assert cron_step.hours == set(range(8, 19))
    assert cron_step.days_of_week == {1, 2, 3, 4, 5}


def test_cronspec_invalid_patterns() -> None:
    """CronSpec must reject malformed expressions."""
    with pytest.raises(ValueError, match="exactly 5 fields"):
        CronSpec("* * * *")

    with pytest.raises(ValueError, match="exactly 5 fields"):
        CronSpec("* * * * * *")

    with pytest.raises(ValueError):
        CronSpec("60 * * * *")  # minute 60 is invalid

    with pytest.raises(ValueError):
        CronSpec("* 25 * * *")  # hour 25 is invalid


def test_cronspec_matches() -> None:
    """Test cron time matching."""
    cron = CronSpec("30 9 15 3 *")
    matching_dt = datetime(2026, 3, 15, 9, 30, tzinfo=timezone.utc)
    non_matching_dt = datetime(2026, 3, 15, 9, 31, tzinfo=timezone.utc)

    assert cron.matches(matching_dt) is True
    assert cron.matches(non_matching_dt) is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. Pure Recurrence Calculation
# ─────────────────────────────────────────────────────────────────────────────

def test_calculate_next_interval() -> None:
    """Interval recurrence computes next time deterministically."""
    ref_time = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    next_time = calculate_next_occurrence(
        reference_time_utc=ref_time,
        cron_expression=None,
        interval_seconds=300,
        timezone_str="UTC",
    )
    assert next_time == ref_time + timedelta(seconds=300)


def test_calculate_next_interval_with_start_boundary() -> None:
    """Start boundary in future delays initial occurrence."""
    ref_time = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    start_at = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    next_time = calculate_next_occurrence(
        reference_time_utc=ref_time,
        cron_expression=None,
        interval_seconds=300,
        timezone_str="UTC",
        start_time_utc=start_at,
    )
    assert next_time == start_at


def test_calculate_next_interval_with_end_boundary() -> None:
    """End boundary limits further occurrences."""
    ref_time = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    end_at = datetime(2026, 6, 1, 10, 2, 0, tzinfo=timezone.utc)

    # 300s interval would produce 10:05:00, which exceeds end_at 10:02:00
    next_time = calculate_next_occurrence(
        reference_time_utc=ref_time,
        cron_expression=None,
        interval_seconds=300,
        timezone_str="UTC",
        end_time_utc=end_at,
    )
    assert next_time is None


def test_calculate_next_cron() -> None:
    """Cron recurrence computes next matching wall-clock tick."""
    ref_time = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    # Cron for every day at 10:30 UTC
    next_time = calculate_next_occurrence(
        reference_time_utc=ref_time,
        cron_expression="30 10 * * *",
        interval_seconds=None,
        timezone_str="UTC",
    )
    assert next_time == datetime(2026, 6, 1, 10, 30, 0, tzinfo=timezone.utc)


def test_calculate_next_exactly_one_recurrence_mode() -> None:
    """Must provide either cron or interval, never neither or both."""
    ref_time = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="Either cron_expression or interval_seconds must be provided"):
        calculate_next_occurrence(
            reference_time_utc=ref_time,
            cron_expression=None,
            interval_seconds=None,
            timezone_str="UTC",
        )

    with pytest.raises(ValueError, match="Cannot specify both cron_expression and interval_seconds"):
        calculate_next_occurrence(
            reference_time_utc=ref_time,
            cron_expression="* * * * *",
            interval_seconds=120,
            timezone_str="UTC",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Timezone & DST Semantics (PEP 495 Fold Awareness)
# ─────────────────────────────────────────────────────────────────────────────

def test_dst_ambiguous_fall_back_strategies() -> None:
    """Test ambiguous 1:30 AM local time during autumn fall-back in America/New_York (2026-11-01)."""
    local_naive = datetime(2026, 11, 1, 1, 30, 0)

    # Strategy FIRST -> fold=0 (earlier UTC time, 05:30 UTC)
    utc_first = resolve_local_wall_to_utc(
        dt_naive=local_naive,
        tz_name="America/New_York",
        ambiguous_strategy=DSTAmbiguousStrategy.FIRST,
        nonexistent_strategy=DSTNonexistentStrategy.NEXT_VALID,
    )
    assert utc_first == datetime(2026, 11, 1, 5, 30, 0, tzinfo=timezone.utc)

    # Strategy SECOND -> fold=1 (later UTC time, 06:30 UTC)
    utc_second = resolve_local_wall_to_utc(
        dt_naive=local_naive,
        tz_name="America/New_York",
        ambiguous_strategy=DSTAmbiguousStrategy.SECOND,
        nonexistent_strategy=DSTNonexistentStrategy.NEXT_VALID,
    )
    assert utc_second == datetime(2026, 11, 1, 6, 30, 0, tzinfo=timezone.utc)
    assert utc_second > utc_first

    # Strategy REJECT -> raises ValueError
    with pytest.raises(ValueError, match="Ambiguous local"):
        resolve_local_wall_to_utc(
            dt_naive=local_naive,
            tz_name="America/New_York",
            ambiguous_strategy=DSTAmbiguousStrategy.REJECT,
            nonexistent_strategy=DSTNonexistentStrategy.NEXT_VALID,
        )


def test_dst_nonexistent_spring_forward_strategies() -> None:
    """Test nonexistent 2:30 AM local time during spring-forward in America/New_York (2026-03-08)."""
    local_naive = datetime(2026, 3, 8, 2, 30, 0)

    # Strategy NEXT_VALID -> adjusts forward past the gap (3:00 AM local = 07:00 UTC)
    utc_valid = resolve_local_wall_to_utc(
        dt_naive=local_naive,
        tz_name="America/New_York",
        ambiguous_strategy=DSTAmbiguousStrategy.FIRST,
        nonexistent_strategy=DSTNonexistentStrategy.NEXT_VALID,
    )
    assert utc_valid is not None
    assert utc_valid == datetime(2026, 3, 8, 7, 0, 0, tzinfo=timezone.utc)

    # Strategy SKIP -> returns None
    utc_skip = resolve_local_wall_to_utc(
        dt_naive=local_naive,
        tz_name="America/New_York",
        ambiguous_strategy=DSTAmbiguousStrategy.FIRST,
        nonexistent_strategy=DSTNonexistentStrategy.SKIP,
    )
    assert utc_skip is None

    # Strategy REJECT -> raises ValueError
    with pytest.raises(ValueError, match="Nonexistent local"):
        resolve_local_wall_to_utc(
            dt_naive=local_naive,
            tz_name="America/New_York",
            ambiguous_strategy=DSTAmbiguousStrategy.FIRST,
            nonexistent_strategy=DSTNonexistentStrategy.REJECT,
        )


def test_unusual_timezone_transitions() -> None:
    """Test unusual timezone with 30-minute DST offset (Australia/Lord_Howe: +10:30 to +11:00)."""
    ref_time = datetime(2026, 10, 4, 1, 0, 0, tzinfo=timezone.utc)
    next_time = calculate_next_occurrence(
        reference_time_utc=ref_time,
        cron_expression="0 12 * * *",
        interval_seconds=None,
        timezone_str="Australia/Lord_Howe",
    )
    assert next_time is not None
    assert next_time.tzinfo == timezone.utc

    # Verify Asia/Kolkata (UTC+05:30, non-DST)
    ref_kolkata = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    next_kolkata = calculate_next_occurrence(
        reference_time_utc=ref_kolkata,
        cron_expression="30 9 * * *",  # 9:30 AM IST = 4:00 AM UTC
        interval_seconds=None,
        timezone_str="Asia/Kolkata",
    )
    assert next_kolkata == datetime(2026, 1, 1, 4, 0, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Catch-up Partitioning
# ─────────────────────────────────────────────────────────────────────────────

def test_partition_catch_up_skip_missed() -> None:
    """SKIP_MISSED policy sends all missed occurrences to to_skip."""
    t0 = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    missed = [t0 + timedelta(minutes=i) for i in range(5)]

    to_dispatch, to_skip = partition_catch_up_occurrences(
        missed_occurrences=missed,
        policy=CatchUpPolicy.SKIP_MISSED,
        max_catch_up=5,
    )
    assert to_dispatch == []
    assert to_skip == missed


def test_partition_catch_up_run_latest_only() -> None:
    """RUN_LATEST_ONLY dispatches only the latest missed occurrence."""
    t0 = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    missed = [t0 + timedelta(minutes=i) for i in range(5)]

    to_dispatch, to_skip = partition_catch_up_occurrences(
        missed_occurrences=missed,
        policy=CatchUpPolicy.RUN_LATEST_ONLY,
        max_catch_up=5,
    )
    assert to_dispatch == [missed[-1]]
    assert to_skip == missed[:-1]


def test_partition_catch_up_run_all_bounded() -> None:
    """RUN_ALL_BOUNDED dispatches up to max_catch_up (most recent), skipping the older backlog."""
    t0 = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    missed = [t0 + timedelta(minutes=i) for i in range(10)]

    to_dispatch, to_skip = partition_catch_up_occurrences(
        missed_occurrences=missed,
        policy=CatchUpPolicy.RUN_ALL_BOUNDED,
        max_catch_up=3,
    )
    # Latest 3 dispatched, remaining 7 skipped
    assert len(to_dispatch) == 3
    assert to_dispatch == missed[-3:]
    assert len(to_skip) == 7
    assert to_skip == missed[:-3]


def test_partition_catch_up_empty() -> None:
    """Empty missed occurrences returns empty lists."""
    to_dispatch, to_skip = partition_catch_up_occurrences(
        missed_occurrences=[],
        policy=CatchUpPolicy.RUN_ALL_BOUNDED,
        max_catch_up=5,
    )
    assert to_dispatch == []
    assert to_skip == []


# ─────────────────────────────────────────────────────────────────────────────
# 7. Create / Update Domain Models
# ─────────────────────────────────────────────────────────────────────────────

def test_recurring_schedule_create_defaults() -> None:
    """Creation model defaults to sensible, safe settings."""
    model = RecurringScheduleCreate(
        name="Test Schedule",
        start_time=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        target_type=RecurringScheduleTargetType.STANDALONE_MISSION,
        target_id=uuid4(),
        interval_seconds=300,
        payload_template={"title": "Test Mission", "objective": "Test"},
    )
    assert model.catch_up_policy == CatchUpPolicy.SKIP_MISSED
    assert model.max_catch_up_occurrences == 3
    assert model.timezone == "UTC"
    assert model.dst_ambiguous_strategy == DSTAmbiguousStrategy.FIRST
    assert model.dst_nonexistent_strategy == DSTNonexistentStrategy.NEXT_VALID


def test_recurring_schedule_create_validates_bounds() -> None:
    """Creation model validates interval >= 60 and max_catch_up between 1 and 10."""
    valid_id = uuid4()
    t_start = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ValidationError):
        RecurringScheduleCreate(
            name="Test",
            start_time=t_start,
            target_type=RecurringScheduleTargetType.STANDALONE_MISSION,
            target_id=valid_id,
            interval_seconds=30,  # Below 60
            payload_template={"title": "Test", "objective": "Test"},
        )

    with pytest.raises(ValidationError):
        RecurringScheduleCreate(
            name="Test",
            start_time=t_start,
            target_type=RecurringScheduleTargetType.STANDALONE_MISSION,
            target_id=valid_id,
            interval_seconds=120,
            max_catch_up_occurrences=20,  # Above 10
            payload_template={"title": "Test", "objective": "Test"},
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. Campaign Admission Reason Taxonomy & Outcome Classification
# ─────────────────────────────────────────────────────────────────────────────

def test_campaign_admission_reason_taxonomy_classification() -> None:
    """Validate canonical taxonomy classification for all campaign admission reasons."""
    from omega.application.scheduler.recurring_sweep_service import RecurringSweepService

    # 1. SUCCESS: ADMITTED, ALREADY_BOUND -> DISPATCHED
    assert RecurringSweepService.classify_campaign_admission_outcome("ADMITTED") == (
        RecurringOccurrenceStatus.DISPATCHED,
        None,
    )
    assert RecurringSweepService.classify_campaign_admission_outcome("ALREADY_BOUND") == (
        RecurringOccurrenceStatus.DISPATCHED,
        None,
    )

    # 2. TRANSIENT_WAIT: CAPACITY_FULL -> WAITING
    assert RecurringSweepService.classify_campaign_admission_outcome("CAPACITY_FULL") == (
        RecurringOccurrenceStatus.WAITING,
        None,
    )

    # 3. TERMINAL_SKIP: NO_PENDING_ITEMS, CAMPAIGN_NOT_RUNNING_* -> SKIPPED
    assert RecurringSweepService.classify_campaign_admission_outcome("NO_PENDING_ITEMS") == (
        RecurringOccurrenceStatus.SKIPPED,
        "NO_PENDING_ITEMS",
    )
    assert RecurringSweepService.classify_campaign_admission_outcome("CAMPAIGN_NOT_RUNNING_PAUSED") == (
        RecurringOccurrenceStatus.SKIPPED,
        "CAMPAIGN_NOT_RUNNING_PAUSED",
    )
    assert RecurringSweepService.classify_campaign_admission_outcome("CAMPAIGN_NOT_RUNNING_FAILED") == (
        RecurringOccurrenceStatus.SKIPPED,
        "CAMPAIGN_NOT_RUNNING_FAILED",
    )
    assert RecurringSweepService.classify_campaign_admission_outcome("CAMPAIGN_NOT_RUNNING_SUCCEEDED") == (
        RecurringOccurrenceStatus.SKIPPED,
        "CAMPAIGN_NOT_RUNNING_SUCCEEDED",
    )
    assert RecurringSweepService.classify_campaign_admission_outcome("CAMPAIGN_NOT_RUNNING_CANCELLED") == (
        RecurringOccurrenceStatus.SKIPPED,
        "CAMPAIGN_NOT_RUNNING_CANCELLED",
    )

    # 4. TERMINAL_FAILURE: CAMPAIGN_NOT_FOUND, MATERIALIZATION_FAILED, INVALID_* -> FAILED
    assert RecurringSweepService.classify_campaign_admission_outcome("CAMPAIGN_NOT_FOUND") == (
        RecurringOccurrenceStatus.FAILED,
        "CAMPAIGN_NOT_FOUND",
    )
    assert RecurringSweepService.classify_campaign_admission_outcome("MATERIALIZATION_FAILED") == (
        RecurringOccurrenceStatus.FAILED,
        "MATERIALIZATION_FAILED",
    )
    assert RecurringSweepService.classify_campaign_admission_outcome("INVALID_ORCHESTRATION_MODE_LEGACY_UPFRONT") == (
        RecurringOccurrenceStatus.FAILED,
        "INVALID_ORCHESTRATION_MODE_LEGACY_UPFRONT",
    )
    assert RecurringSweepService.classify_campaign_admission_outcome("INVALID_STATUS_ARCHIVED") == (
        RecurringOccurrenceStatus.FAILED,
        "INVALID_STATUS_ARCHIVED",
    )

    # 5. BUG / UNEXPECTED: unknown reasons fail closed as FAILED, never silently skipped
    status, err = RecurringSweepService.classify_campaign_admission_outcome("UNKNOWN_INFRA_ERROR")
    assert status == RecurringOccurrenceStatus.FAILED
    assert err == "Unexpected campaign admission reason: UNKNOWN_INFRA_ERROR"

