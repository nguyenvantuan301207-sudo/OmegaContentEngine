"""Integration tests for OMEGA-010 Scheduler Application Services.

Tests PolicyService, SlotAllocator, EvaluationEngine, DispatchFence,
OutboxRelayService, and SchedulerSweepService against PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.scheduler import (
    DispatchFence,
    OutboxRelayService,
    ScheduleEvaluationEngine,
    SchedulePolicyService,
    SchedulerSweepService,
    SlotAllocator,
)
from omega.domain.scheduler import (
    DispatchFenceResult,
    DispatchOutboxStatus,
    ReservationState,
    ScheduleAction,
    SchedulePolicyConfig,
    SchedulePolicyStatus,
    ScheduleRequest,
    ScheduleTargetType,
    ScheduleWorkloadCategory,
)
from omega.domain.task import TaskState
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    Mission,
    ScheduleDecision,
    SchedulePolicy,
    SchedulerDispatchOutbox,
    ScheduleReservation,
    ScheduleStateTransition,
    Task,
)


@pytest_asyncio.fixture(autouse=True)
async def clean_scheduler_tables(db_session: AsyncSession):
    """Clean up scheduler tables before each integration test to ensure complete test hermeticity."""
    from sqlalchemy import delete

    await db_session.execute(delete(SchedulerDispatchOutbox))
    await db_session.execute(delete(ScheduleStateTransition))
    await db_session.execute(delete(ScheduleReservation))
    await db_session.execute(delete(ScheduleDecision))
    await db_session.execute(delete(SchedulePolicy))
    await db_session.commit()


@pytest.fixture
def default_policy_config() -> dict:
    return {
        "min_lead_time_seconds": 0,
        "max_schedule_horizon_days": 14,
        "min_gap_between_channel_items_seconds": 3600,
        "max_scheduled_items_per_day": 5,
        "global_concurrency_limit": 100,
        "channel_concurrency_limit": 10,
        "retry_backoff_base_seconds": 60,
        "retry_backoff_factor": 2.0,
        "retry_max_backoff_seconds": 3600,
        "max_deferrals": 5,
        "max_urgency_bonus": 50,
        "dispatching_timeout_seconds": 300,
        "dst_config": {
            "ambiguous_local_time_strategy": "FIRST_OCCURRENCE",
            "nonexistent_local_time_strategy": "SHIFT_FORWARD",
        },
        "fairness": {
            "age_bonus_per_interval": 5,
            "age_bonus_interval_seconds": 3600,
            "age_bonus_cap": 50,
            "starvation_threshold_seconds": 86400,
            "per_channel_cycle_cap": 3,
        },
    }


# ── 1. Policy Service Integration Tests ──


@pytest.mark.asyncio
async def test_policy_lifecycle_and_single_active_invariant(
    db_session: AsyncSession, default_policy_config: dict
):
    """Verify DRAFT -> ACTIVE -> RETIRED lifecycle and partial unique index."""
    cat = "EXTERNAL_PUBLISH"

    # Create v1 DRAFT
    p1 = await SchedulePolicyService.create_policy(
        db_session,
        workload_category=cat,
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config=default_policy_config,
        activate=False,
    )
    assert p1.status == SchedulePolicyStatus.DRAFT.value

    # Activate v1
    p1_active = await SchedulePolicyService.activate_policy(db_session, p1.id)
    assert p1_active.status == SchedulePolicyStatus.ACTIVE.value
    assert p1_active.effective_at is not None

    # Create v2 DRAFT
    p2 = await SchedulePolicyService.create_policy(
        db_session,
        workload_category=cat,
        version=f"2.0.0-{uuid4().hex[:6]}",
        policy_config=default_policy_config,
        activate=False,
    )

    # Activate v2: should retire v1 atomically
    p2_active = await SchedulePolicyService.activate_policy(db_session, p2.id)
    assert p2_active.status == SchedulePolicyStatus.ACTIVE.value

    # Re-fetch p1
    p1_refreshed = await db_session.get(SchedulePolicy, p1.id)
    assert p1_refreshed is not None
    assert p1_refreshed.status == SchedulePolicyStatus.RETIRED.value
    assert p1_refreshed.retired_at is not None

    # Active policy should now be v2
    active_now = await SchedulePolicyService.get_active_policy(db_session, cat)
    assert active_now is not None
    assert active_now.id == p2.id


# ── 2. Slot Allocator & Capacity Tests ──


@pytest.mark.asyncio
async def test_slot_allocator_empty_table_and_min_gap(
    db_session: AsyncSession, default_policy_config: dict
):
    """Verify slot allocation on empty table and min-gap collision rejection."""
    cat = "EXTERNAL_PUBLISH"
    policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category=cat,
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config=default_policy_config,
        activate=True,
    )
    policy_cfg = SchedulePolicyConfig(**policy.policy_config)

    # Create Channel and Mission
    channel_id = uuid4()
    channel = Channel(
        id=channel_id,
        slug=f"chan-{uuid4().hex[:8]}",
        name="Test Channel",
        state="ACTIVE",
    )
    db_session.add(channel)

    mission = Mission(
        id=uuid4(),
        channel_id=channel_id,
        title="Test Mission",
        objective="Scheduling Test",
        state="RUNNING",
        priority=1,
    )
    db_session.add(mission)
    await db_session.commit()

    now = datetime.now(UTC)
    candidate_start = now + timedelta(hours=2)
    candidate_end = candidate_start + timedelta(minutes=30)

    # 1. First allocation on empty table succeeds
    dec1, res1 = await SlotAllocator.allocate_slot(
        db_session,
        policy=policy,
        policy_config=policy_cfg,
        mission_id=mission.id,
        task_id=None,
        target_type="TASK_EXECUTION",
        target_id=uuid4(),
        workload_category=cat,
        channel_id=channel_id,
        channel_dna_revision_id=None,
        guardian_epoch=1,
        candidate_start=candidate_start,
        candidate_end=candidate_end,
        priority=1,
        estimated_duration_seconds=1800,
        deadline_at=now + timedelta(days=2),
        earliest_start_at=now,
        latest_start_at=now + timedelta(days=2),
        caller_key="test-call-1",
        priority_score=100.0,
    )
    assert dec1.action == ScheduleAction.SCHEDULE.value
    assert res1 is not None
    assert res1.state == ReservationState.ACTIVE.value
    await db_session.commit()

    # 2. Second allocation within min-gap window (3600s gap) is rejected with BLOCKED_SCHEDULE
    conflict_start = candidate_end + timedelta(minutes=15)  # only 15 min gap < 60 min
    conflict_end = conflict_start + timedelta(minutes=30)

    dec2, res2 = await SlotAllocator.allocate_slot(
        db_session,
        policy=policy,
        policy_config=policy_cfg,
        mission_id=mission.id,
        task_id=None,
        target_type="TASK_EXECUTION",
        target_id=uuid4(),
        workload_category=cat,
        channel_id=channel_id,
        channel_dna_revision_id=None,
        guardian_epoch=1,
        candidate_start=conflict_start,
        candidate_end=conflict_end,
        priority=1,
        estimated_duration_seconds=1800,
        deadline_at=now + timedelta(days=2),
        earliest_start_at=now,
        latest_start_at=now + timedelta(days=2),
        caller_key="test-call-2",
        priority_score=100.0,
    )
    assert dec2.action == ScheduleAction.BLOCKED_SCHEDULE.value
    assert res2 is None
    assert "Min gap violation" in dec2.reason


@pytest.mark.asyncio
async def test_slot_allocator_temporal_overlap_capacity(
    db_session: AsyncSession, default_policy_config: dict
):
    """Verify temporal overlap capacity: overlapping slots consume capacity, future slots do not."""
    cat = "HEAVY_RENDER"
    # Set global concurrency = 1
    custom_cfg = {
        **default_policy_config,
        "global_concurrency_limit": 1,
        "min_gap_between_channel_items_seconds": 0,
    }
    policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category=cat,
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config=custom_cfg,
        activate=True,
    )
    policy_cfg = SchedulePolicyConfig(**policy.policy_config)

    mission = Mission(
        id=uuid4(),
        title="Render Mission",
        objective="Render Capacity Test",
        state="RUNNING",
        priority=1,
    )
    db_session.add(mission)
    await db_session.commit()

    now = datetime.now(UTC)
    t_start1 = now + timedelta(hours=1)
    t_end1 = t_start1 + timedelta(hours=1)

    # 1. Allocate slot 1: 1h -> 2h
    dec1, res1 = await SlotAllocator.allocate_slot(
        db_session,
        policy=policy,
        policy_config=policy_cfg,
        mission_id=mission.id,
        task_id=None,
        target_type="RENDER_JOB",
        target_id=uuid4(),
        workload_category=cat,
        channel_id=None,
        channel_dna_revision_id=None,
        guardian_epoch=1,
        candidate_start=t_start1,
        candidate_end=t_end1,
        priority=1,
        estimated_duration_seconds=3600,
        deadline_at=None,
        earliest_start_at=now,
        latest_start_at=None,
        caller_key="render-1",
        priority_score=100.0,
    )
    assert dec1.action == ScheduleAction.SCHEDULE.value
    await db_session.commit()

    # 2. Overlapping slot 1.5h -> 2.5h hits global capacity limit (1)
    t_start2 = t_start1 + timedelta(minutes=30)
    t_end2 = t_start2 + timedelta(hours=1)
    dec2, res2 = await SlotAllocator.allocate_slot(
        db_session,
        policy=policy,
        policy_config=policy_cfg,
        mission_id=mission.id,
        task_id=None,
        target_type="RENDER_JOB",
        target_id=uuid4(),
        workload_category=cat,
        channel_id=None,
        channel_dna_revision_id=None,
        guardian_epoch=1,
        candidate_start=t_start2,
        candidate_end=t_end2,
        priority=1,
        estimated_duration_seconds=3600,
        deadline_at=None,
        earliest_start_at=now,
        latest_start_at=None,
        caller_key="render-2",
        priority_score=100.0,
    )
    assert dec2.action == ScheduleAction.WAITING_CAPACITY.value
    assert res2 is None

    # 3. Disjoint future slot (T+5h -> T+6h) succeeds because future does not overlap
    t_start3 = t_start1 + timedelta(hours=5)
    t_end3 = t_start3 + timedelta(hours=1)
    dec3, res3 = await SlotAllocator.allocate_slot(
        db_session,
        policy=policy,
        policy_config=policy_cfg,
        mission_id=mission.id,
        task_id=None,
        target_type="RENDER_JOB",
        target_id=uuid4(),
        workload_category=cat,
        channel_id=None,
        channel_dna_revision_id=None,
        guardian_epoch=1,
        candidate_start=t_start3,
        candidate_end=t_end3,
        priority=1,
        estimated_duration_seconds=3600,
        deadline_at=None,
        earliest_start_at=now,
        latest_start_at=None,
        caller_key="render-3",
        priority_score=100.0,
    )
    assert dec3.action == ScheduleAction.SCHEDULE.value
    assert res3 is not None


# ── 3. Evaluation Engine Integration Tests ──


@pytest.mark.asyncio
async def test_evaluation_engine_end_to_end_and_idempotency(
    db_session: AsyncSession, default_policy_config: dict
):
    """Verify ScheduleEvaluationEngine evaluation flow, channel preferences, and duplicate reuse."""
    cat = "EXTERNAL_PUBLISH"
    _policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category=cat,
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config=default_policy_config,
        activate=True,
    )

    channel_id = uuid4()
    channel = Channel(
        id=channel_id,
        slug=f"chan-{uuid4().hex[:8]}",
        name="Tech Channel",
        state="ACTIVE",
    )
    db_session.add(channel)

    dna = ChannelDNARevision(
        id=uuid4(),
        channel_id=channel_id,
        version=1,
        snapshot={
            "publishing_preferences": {
                "target_timezone": "UTC",
                "preferred_days": [
                    "MONDAY",
                    "TUESDAY",
                    "WEDNESDAY",
                    "THURSDAY",
                    "FRIDAY",
                    "SATURDAY",
                    "SUNDAY",
                ],
                "preferred_time_windows": ["14:00-16:00"],
            }
        },
        change_reason="Initial setup",
    )
    db_session.add(dna)

    mission = Mission(
        id=uuid4(),
        channel_id=channel_id,
        title="Publish Mission",
        objective="DNA Schedule Test",
        state="RUNNING",
        priority=2,
        guardian_epoch=1,
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="publish_video",
        title="Publish Video Task",
        state="READY",
    )
    db_session.add(task)
    await db_session.commit()

    now = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)
    req = ScheduleRequest(
        mission_id=mission.id,
        task_id=task.id,
        target_type=ScheduleTargetType.TASK_EXECUTION,
        target_id=task.id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH,
        channel_id=channel_id,
        earliest_start_at=now,
        estimated_duration_seconds=900,
        priority=2,
        caller_key="eval-test-1",
    )

    # 1. Evaluate request -> should schedule at 14:00 UTC (matching channel window)
    resp1 = await ScheduleEvaluationEngine.evaluate_schedule(db_session, req, now=now)
    assert resp1.action == ScheduleAction.SCHEDULE
    assert resp1.scheduled_start_at is not None
    assert resp1.scheduled_start_at.hour == 14
    assert resp1.reservation_id is not None

    # 2. Re-submitting exact same request returns existing decision
    resp2 = await ScheduleEvaluationEngine.evaluate_schedule(db_session, req, now=now)
    assert resp2.id == resp1.id
    assert resp2.reservation_id == resp1.reservation_id


# ── 4. Dispatch Fence Tests ──


@pytest.mark.asyncio
async def test_dispatch_fence_predicates(db_session: AsyncSession, default_policy_config: dict):
    """Verify dispatch fence verifies Guardian epoch, mission state, and policy pins."""
    cat = "EXTERNAL_PUBLISH"
    policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category=cat,
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config=default_policy_config,
        activate=True,
    )
    policy_cfg = SchedulePolicyConfig(**policy.policy_config)

    mission = Mission(
        id=uuid4(),
        title="Fence Mission",
        objective="Dispatch Fence Test",
        state="RUNNING",
        guardian_epoch=1,
        priority=1,
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="publish_step",
        title="Publish Step",
        state="READY",
    )
    db_session.add(task)
    await db_session.commit()

    now = datetime.now(UTC)
    dec, res = await SlotAllocator.allocate_slot(
        db_session,
        policy=policy,
        policy_config=policy_cfg,
        mission_id=mission.id,
        task_id=task.id,
        target_type="TASK_EXECUTION",
        target_id=task.id,
        workload_category=cat,
        channel_id=None,
        channel_dna_revision_id=None,
        guardian_epoch=1,
        candidate_start=now + timedelta(seconds=10),
        candidate_end=now + timedelta(minutes=10),
        priority=1,
        estimated_duration_seconds=300,
        deadline_at=None,
        earliest_start_at=now,
        latest_start_at=None,
        caller_key="fence-test-1",
        priority_score=100.0,
    )
    assert res is not None
    await db_session.commit()

    # 1. Happy path: fence passes
    result_happy, _ = await DispatchFence.evaluate_fence(db_session, res, now=now)
    assert result_happy == DispatchFenceResult.PROCEED

    # 2. Mutate Guardian epoch on mission -> fence rejects and releases reservation
    mission.guardian_epoch = 2
    await db_session.commit()

    result_stale, reason = await DispatchFence.evaluate_fence(db_session, res, now=now)
    assert result_stale == DispatchFenceResult.STALE_RELEASED
    assert "Guardian epoch" in reason

    # Check reservation state transitioned to RELEASED
    assert res.state == ReservationState.RELEASED.value


# ── 5. Dispatch Sweep & Outbox Relay Tests ──


@pytest.mark.asyncio
async def test_dispatch_sweep_and_outbox_relay_pipeline(
    db_session: AsyncSession, default_policy_config: dict
):
    """Verify dispatch sweep claims due reservation, sets DISPATCHING, and outbox relay marks SENT."""
    cat = "EXTERNAL_PUBLISH"
    policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category=cat,
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config=default_policy_config,
        activate=True,
    )
    policy_cfg = SchedulePolicyConfig(**policy.policy_config)

    mission = Mission(
        id=uuid4(),
        title="Sweep Mission",
        objective="Dispatch Sweep Test",
        state="RUNNING",
        guardian_epoch=1,
        priority=1,
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="publish_action",
        title="Action Task",
        state="READY",
    )
    db_session.add(task)
    await db_session.commit()

    now = datetime.now(UTC)
    # Create due reservation (start time in the past)
    dec, res = await SlotAllocator.allocate_slot(
        db_session,
        policy=policy,
        policy_config=policy_cfg,
        mission_id=mission.id,
        task_id=task.id,
        target_type="TASK_EXECUTION",
        target_id=task.id,
        workload_category=cat,
        channel_id=None,
        channel_dna_revision_id=None,
        guardian_epoch=1,
        candidate_start=now - timedelta(seconds=5),  # already due
        candidate_end=now + timedelta(minutes=10),
        priority=1,
        estimated_duration_seconds=300,
        deadline_at=None,
        earliest_start_at=now,
        latest_start_at=None,
        caller_key="sweep-test-1",
        priority_score=100.0,
    )
    assert res is not None
    await db_session.commit()

    # 1. Run dispatch sweep: claims due reservation, transitions to DISPATCHING, creates outbox row
    sweep_stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert sweep_stats["claimed"] >= 1
    assert sweep_stats["dispatched"] >= 1

    # Check reservation is DISPATCHING and task is QUEUED
    await db_session.refresh(res)
    await db_session.refresh(task)
    assert res.state == ReservationState.DISPATCHING.value
    assert task.state == TaskState.QUEUED.value

    # Check outbox row exists in PENDING
    outbox_res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == res.id)
    )
    outbox_item = outbox_res.scalar_one_or_none()
    assert outbox_item is not None
    assert outbox_item.status == DispatchOutboxStatus.PENDING.value

    # 2. Run outbox relay: processes item and marks SENT
    relay_stats = await OutboxRelayService.process_outbox_batch(db_session, now=now)
    assert relay_stats["claimed"] >= 1
    assert relay_stats["sent"] >= 1

    await db_session.refresh(outbox_item)
    assert outbox_item.status == DispatchOutboxStatus.SENT.value
    assert outbox_item.sent_at is not None


# ── 6. Stale Dispatching Recovery Test ──


@pytest.mark.asyncio
async def test_stale_dispatching_recovery_sweep(
    db_session: AsyncSession, default_policy_config: dict
):
    """Verify stale dispatching recovery reconciles tasks that completed to CONSUMED."""
    cat = "EXTERNAL_PUBLISH"
    policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category=cat,
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config=default_policy_config,
        activate=True,
    )

    mission = Mission(
        id=uuid4(),
        title="Recovery Mission",
        objective="Stale Dispatch Recovery Test",
        state="RUNNING",
        priority=1,
    )
    db_session.add(mission)

    # Task that already finished successfully
    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="publish_step",
        title="Completed Step",
        state="SUCCEEDED",
        started_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    db_session.add(task)

    decision = ScheduleDecision(
        id=uuid4(),
        mission_id=mission.id,
        task_id=task.id,
        target_type="TASK_EXECUTION",
        target_id=task.id,
        workload_category=cat,
        action="SCHEDULE",
        reason="Allocated",
        policy_id=policy.id,
        policy_version=policy.version,
        policy_checksum=policy.checksum,
        guardian_epoch=1,
        idempotency_key=f"recov-{uuid4().hex}",
        evaluated_at=datetime.now(UTC),
    )
    db_session.add(decision)

    # Reservation stuck in DISPATCHING from 10 minutes ago
    ten_min_ago = datetime.now(UTC) - timedelta(minutes=10)
    stuck_res = ScheduleReservation(
        id=uuid4(),
        decision_id=decision.id,
        mission_id=mission.id,
        target_type="TASK_EXECUTION",
        target_id=task.id,
        workload_category=cat,
        scheduled_start_at=ten_min_ago,
        scheduled_end_at=ten_min_ago + timedelta(minutes=5),
        state=ReservationState.DISPATCHING.value,
        dispatching_at=ten_min_ago,
        policy_id=policy.id,
        policy_version=policy.version,
        policy_checksum=policy.checksum,
        guardian_epoch=1,
    )
    db_session.add(stuck_res)
    await db_session.commit()

    # Run stale dispatch recovery sweep
    recov_stats = await SchedulerSweepService.run_stale_dispatching_recovery_sweep(
        db_session, timeout_seconds=300
    )
    assert recov_stats["recovered"] >= 1
    assert recov_stats["consumed"] >= 1

    await db_session.refresh(stuck_res)
    assert stuck_res.state == ReservationState.CONSUMED.value


# ── 7. Outbox Retry and Dead Letter Tests ──


@pytest.mark.asyncio
async def test_outbox_retry_and_dead_letter_progression(
    db_session: AsyncSession, default_policy_config: dict, monkeypatch
):
    """Verify outbox relay marks items RETRY with exponential backoff on failure and DEAD_LETTER on max attempts."""
    cat = "EXTERNAL_PUBLISH"
    policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category=cat,
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config=default_policy_config,
        activate=True,
    )
    mission = Mission(
        id=uuid4(),
        title="Outbox Fail Mission",
        objective="Fail Test",
        state="RUNNING",
        priority=1,
    )
    db_session.add(mission)
    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="publish_video",
        title="Publish Video Task",
        state="READY",
    )
    db_session.add(task)

    dec = ScheduleDecision(
        id=uuid4(),
        mission_id=mission.id,
        task_id=task.id,
        target_type="TASK_EXECUTION",
        target_id=task.id,
        workload_category=cat,
        action="SCHEDULE",
        reason="Test",
        policy_id=policy.id,
        policy_version=policy.version,
        policy_checksum=policy.checksum,
        guardian_epoch=1,
        idempotency_key=f"outbox-fail-{uuid4().hex}",
        evaluated_at=datetime.now(UTC),
    )
    db_session.add(dec)

    res = ScheduleReservation(
        id=uuid4(),
        decision_id=dec.id,
        mission_id=mission.id,
        target_type="TASK_EXECUTION",
        target_id=task.id,
        workload_category=cat,
        scheduled_start_at=datetime.now(UTC),
        scheduled_end_at=datetime.now(UTC) + timedelta(minutes=5),
        state=ReservationState.DISPATCHING.value,
        policy_id=policy.id,
        policy_version=policy.version,
        policy_checksum=policy.checksum,
        guardian_epoch=1,
    )
    db_session.add(res)

    outbox_item = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=res.id,
        task_id=task.id,
        mission_id=mission.id,
        celery_task_name="omega.tasks.execute",
        celery_args={"args": [str(task.id)]},
        idempotency_key=f"outbox-item-{uuid4().hex}",
        status=DispatchOutboxStatus.PENDING.value,
        attempt_count=0,
        max_attempts=2,
        scheduled_send_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    db_session.add(outbox_item)
    await db_session.commit()

    # Mock celery_app.send_task to simulate broker failure
    from omega.infrastructure.celery_app import celery_app

    def mock_send_fail(*args, **kwargs):
        raise ConnectionError("Simulated broker network failure")

    monkeypatch.setattr(celery_app, "send_task", mock_send_fail)

    # 1. First attempt -> RETRY (attempt_count = 1 < 2)
    stats1 = await OutboxRelayService.process_outbox_batch(db_session)
    assert stats1["claimed"] == 1
    assert stats1["retried"] == 1

    await db_session.refresh(outbox_item)
    assert outbox_item.status == DispatchOutboxStatus.RETRY.value
    assert outbox_item.attempt_count == 1
    assert outbox_item.next_retry_at is not None
    assert "ConnectionError" in (outbox_item.last_error or "")

    # Set next_retry_at to past so it's eligible for attempt 2
    outbox_item.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    # 2. Second attempt -> DEAD_LETTER (attempt_count = 2 == max_attempts)
    stats2 = await OutboxRelayService.process_outbox_batch(db_session)
    assert stats2["claimed"] == 1
    assert stats2["dead_letter"] == 1

    await db_session.refresh(outbox_item)
    assert outbox_item.status == DispatchOutboxStatus.DEAD_LETTER.value
    assert outbox_item.attempt_count == 2


# ── 8. Expiration Sweep Test ──


@pytest.mark.asyncio
async def test_expiration_sweep_transitions_past_due_reservations(
    db_session: AsyncSession, default_policy_config: dict
):
    """Verify expiration sweep moves ACTIVE reservations past expires_at to EXPIRED."""
    cat = "EXTERNAL_PUBLISH"
    policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category=cat,
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config=default_policy_config,
        activate=True,
    )
    mission = Mission(
        id=uuid4(),
        title="Expire Mission",
        objective="Expiration Test",
        state="RUNNING",
        priority=1,
    )
    db_session.add(mission)

    dec = ScheduleDecision(
        id=uuid4(),
        mission_id=mission.id,
        target_type="TASK_EXECUTION",
        target_id=uuid4(),
        workload_category=cat,
        action="SCHEDULE",
        reason="Test",
        policy_id=policy.id,
        policy_version=policy.version,
        policy_checksum=policy.checksum,
        guardian_epoch=1,
        idempotency_key=f"expire-{uuid4().hex}",
        evaluated_at=datetime.now(UTC),
    )
    db_session.add(dec)

    past_time = datetime.now(UTC) - timedelta(minutes=10)
    expired_res = ScheduleReservation(
        id=uuid4(),
        decision_id=dec.id,
        mission_id=mission.id,
        target_type="TASK_EXECUTION",
        target_id=dec.target_id,
        workload_category=cat,
        scheduled_start_at=past_time - timedelta(minutes=5),
        scheduled_end_at=past_time,
        state=ReservationState.ACTIVE.value,
        expires_at=past_time,  # already expired
        policy_id=policy.id,
        policy_version=policy.version,
        policy_checksum=policy.checksum,
        guardian_epoch=1,
    )
    db_session.add(expired_res)
    await db_session.commit()

    # Run expiration sweep
    count = await SchedulerSweepService.run_expiration_sweep(db_session)
    assert count >= 1

    await db_session.refresh(expired_res)
    assert expired_res.state == ReservationState.EXPIRED.value
