"""Unit tests for OMEGA-010 Scheduler Domain and Priority Engine.

Tests priority scoring, fairness, age bonus, deadline urgency,
DST resolution strategies, idempotency key generation, and time window normalization.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from omega.application.scheduler.priority_engine import PriorityEngine
from omega.domain.scheduler import (
    AmbiguousLocalTimeError,
    AmbiguousLocalTimeStrategy,
    FairnessConfig,
    NonexistentLocalTimeError,
    NonexistentLocalTimeStrategy,
    check_temporal_overlap,
    compute_schedule_idempotency_key,
    resolve_local_to_utc,
    schedule_advisory_lock_key,
)

# ── 1. Priority Engine Tests ──


def test_priority_score_base_calculation():
    """Verify base priority score calculation from mission priority."""
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    created_at = now

    breakdown = PriorityEngine.calculate_priority(
        mission_priority=3,
        deadline_at=None,
        created_at=created_at,
        now=now,
    )
    assert breakdown.base_score == 300.0
    assert breakdown.urgency_score == 0.0
    assert breakdown.age_bonus_score == 0.0
    assert breakdown.total_score == 300.0
    assert not breakdown.is_starving


def test_priority_score_deadline_urgency():
    """Verify deadline urgency scales linearly as deadline approaches."""
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    created_at = now
    # Deadline 3.5 days away (halfway into 7-day horizon)
    deadline_halfway = now + timedelta(days=3.5)

    breakdown_half = PriorityEngine.calculate_priority(
        mission_priority=1,
        deadline_at=deadline_halfway,
        created_at=created_at,
        now=now,
        max_urgency_bonus=50,
    )
    # Should be ~25.0 urgency bonus
    assert 24.0 <= breakdown_half.urgency_score <= 26.0
    assert breakdown_half.total_score == 100.0 + breakdown_half.urgency_score

    # Expired deadline gets maximum urgency bonus
    breakdown_expired = PriorityEngine.calculate_priority(
        mission_priority=1,
        deadline_at=now - timedelta(hours=1),
        created_at=created_at,
        now=now,
        max_urgency_bonus=50,
    )
    assert breakdown_expired.urgency_score == 50.0
    assert breakdown_expired.total_score == 150.0


def test_priority_score_age_bonus_and_cap():
    """Verify age bonus accumulates per interval and is bounded by cap."""
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    # Task waited 10 hours
    created_at = now - timedelta(hours=10)
    fairness = FairnessConfig(
        age_bonus_per_interval=5,
        age_bonus_interval_seconds=3600,
        age_bonus_cap=30,
        starvation_threshold_seconds=86400,
    )

    breakdown = PriorityEngine.calculate_priority(
        mission_priority=1,
        deadline_at=None,
        created_at=created_at,
        now=now,
        fairness=fairness,
    )
    # 10 intervals * 5 = 50, but capped at 30
    assert breakdown.age_bonus_score == 30.0
    assert breakdown.total_score == 130.0
    assert not breakdown.is_starving


def test_priority_score_starvation_detection():
    """Verify starvation flag triggers when waiting exceeds threshold."""
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    created_at = now - timedelta(hours=25)  # 25 hours > 24 hours threshold
    fairness = FairnessConfig(
        starvation_threshold_seconds=86400,
    )

    breakdown = PriorityEngine.calculate_priority(
        mission_priority=1,
        deadline_at=None,
        created_at=created_at,
        now=now,
        fairness=fairness,
    )
    assert breakdown.is_starving is True


# ── 2. DST Strategy Resolution Tests ──


def test_dst_ambiguous_first_occurrence_new_york():
    """Verify ambiguous fall-back local time with FIRST_OCCURRENCE strategy (earlier UTC)."""
    # 2026-11-01 01:30 in America/New_York is repeated twice during fall-back
    local_dt = datetime(2026, 11, 1, 1, 30, 0)
    utc_dt, evidence = resolve_local_to_utc(
        local_dt,
        "America/New_York",
        ambiguous_strategy=AmbiguousLocalTimeStrategy.FIRST_OCCURRENCE,
    )
    assert evidence is not None
    assert evidence["was_ambiguous"] is True
    assert evidence["applied_strategy"] == "FIRST_OCCURRENCE"
    # EDT is UTC-4 -> 01:30 EDT is 05:30 UTC
    assert utc_dt.hour == 5
    assert utc_dt.minute == 30


def test_dst_ambiguous_second_occurrence_new_york():
    """Verify ambiguous fall-back local time with SECOND_OCCURRENCE strategy (later UTC)."""
    local_dt = datetime(2026, 11, 1, 1, 30, 0)
    utc_dt, evidence = resolve_local_to_utc(
        local_dt,
        "America/New_York",
        ambiguous_strategy=AmbiguousLocalTimeStrategy.SECOND_OCCURRENCE,
    )
    assert evidence is not None
    assert evidence["was_ambiguous"] is True
    assert evidence["applied_strategy"] == "SECOND_OCCURRENCE"
    # EST is UTC-5 -> 01:30 EST is 06:30 UTC
    assert utc_dt.hour == 6
    assert utc_dt.minute == 30


def test_dst_ambiguous_reject_strategy():
    """Verify ambiguous local time with REJECT strategy raises AmbiguousLocalTimeError."""
    local_dt = datetime(2026, 11, 1, 1, 30, 0)
    with pytest.raises(AmbiguousLocalTimeError):
        resolve_local_to_utc(
            local_dt,
            "America/New_York",
            ambiguous_strategy=AmbiguousLocalTimeStrategy.REJECT,
        )


def test_dst_nonexistent_shift_forward_new_york():
    """Verify nonexistent spring-forward local time with SHIFT_FORWARD strategy."""
    # 2026-03-08 02:30 in America/New_York is skipped (springs from 02:00 to 03:00)
    local_dt = datetime(2026, 3, 8, 2, 30, 0)
    utc_dt, evidence = resolve_local_to_utc(
        local_dt,
        "America/New_York",
        nonexistent_strategy=NonexistentLocalTimeStrategy.SHIFT_FORWARD,
    )
    assert evidence is not None
    assert evidence["was_nonexistent"] is True
    assert evidence["applied_strategy"] == "SHIFT_FORWARD"
    assert utc_dt is not None


def test_dst_nonexistent_reject_strategy():
    """Verify nonexistent local time with REJECT strategy raises NonexistentLocalTimeError."""
    local_dt = datetime(2026, 3, 8, 2, 30, 0)
    with pytest.raises(NonexistentLocalTimeError):
        resolve_local_to_utc(
            local_dt,
            "America/New_York",
            nonexistent_strategy=NonexistentLocalTimeStrategy.REJECT,
        )


def test_non_dst_timezone_ho_chi_minh():
    """Verify normal timezone without DST (Asia/Ho_Chi_Minh) converts cleanly with no DST evidence."""
    local_dt = datetime(2026, 9, 1, 14, 0, 0)
    utc_dt, evidence = resolve_local_to_utc(local_dt, "Asia/Ho_Chi_Minh")
    assert evidence is None
    # UTC+7 -> 14:00 is 07:00 UTC
    assert utc_dt.hour == 7
    assert utc_dt.minute == 0


# ── 3. Canonical Idempotency Key Tests ──


def test_idempotency_key_deterministic_and_sensitive():
    """Verify idempotency key is deterministic for same inputs and changes on parameter alterations."""
    target_id = uuid4()
    mission_id = uuid4()
    policy_id = uuid4()
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)

    key1 = compute_schedule_idempotency_key(
        target_type="TASK_EXECUTION",
        target_id=target_id,
        mission_id=mission_id,
        workload_category="EXTERNAL_PUBLISH",
        policy_id=policy_id,
        policy_version="1.0.0",
        policy_checksum="abc123checksum",
        guardian_epoch=1,
        deadline_at=now + timedelta(days=1),
        priority=1,
        caller_key="caller-1",
    )

    key2 = compute_schedule_idempotency_key(
        target_type="TASK_EXECUTION",
        target_id=target_id,
        mission_id=mission_id,
        workload_category="EXTERNAL_PUBLISH",
        policy_id=policy_id,
        policy_version="1.0.0",
        policy_checksum="abc123checksum",
        guardian_epoch=1,
        deadline_at=now + timedelta(days=1),
        priority=1,
        caller_key="caller-1",
    )
    assert key1 == key2

    # Changed deadline alters key
    key3 = compute_schedule_idempotency_key(
        target_type="TASK_EXECUTION",
        target_id=target_id,
        mission_id=mission_id,
        workload_category="EXTERNAL_PUBLISH",
        policy_id=policy_id,
        policy_version="1.0.0",
        policy_checksum="abc123checksum",
        guardian_epoch=1,
        deadline_at=now + timedelta(days=2),  # changed
        priority=1,
        caller_key="caller-1",
    )
    assert key1 != key3

    # Changed guardian epoch alters key
    key4 = compute_schedule_idempotency_key(
        target_type="TASK_EXECUTION",
        target_id=target_id,
        mission_id=mission_id,
        workload_category="EXTERNAL_PUBLISH",
        policy_id=policy_id,
        policy_version="1.0.0",
        policy_checksum="abc123checksum",
        guardian_epoch=2,  # changed
        deadline_at=now + timedelta(days=1),
        priority=1,
        caller_key="caller-1",
    )
    assert key1 != key4


# ── 4. Temporal Overlap & Advisory Lock Tests ──


def test_temporal_overlap_helper():
    """Verify temporal overlap logic correctly identifies intersecting intervals."""
    t0 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 9, 1, 11, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    t3 = datetime(2026, 9, 1, 13, 0, 0, tzinfo=UTC)

    # Overlapping intervals
    assert check_temporal_overlap(t0, t2, t1, t3) is True
    # Contained interval
    assert check_temporal_overlap(t0, t3, t1, t2) is True
    # Non-overlapping intervals (adjacent)
    assert check_temporal_overlap(t0, t1, t1, t2) is False
    # Non-overlapping intervals (disjoint)
    assert check_temporal_overlap(t0, t1, t2, t3) is False


def test_schedule_advisory_lock_key_deterministic():
    """Verify advisory lock key is a deterministic signed 64-bit integer."""
    channel_id = uuid4()
    key1 = schedule_advisory_lock_key(channel_id, "EXTERNAL_PUBLISH")
    key2 = schedule_advisory_lock_key(channel_id, "EXTERNAL_PUBLISH")
    assert key1 == key2
    assert isinstance(key1, int)
    # Must fit in signed 64-bit int
    assert -(2**63) <= key1 < 2**63

    # Global lock key
    global_key = schedule_advisory_lock_key(None, "EXTERNAL_PUBLISH")
    assert isinstance(global_key, int)
    assert global_key != key1
