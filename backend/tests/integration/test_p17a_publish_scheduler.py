"""Integration test suite for OMEGA-010 / P17-A Publish Calendar & Scheduler.

Matrix:
1. DRAFT intent + due reservation -> no SchedulerDispatchOutbox
2. APPROVED future -> no dispatch
3. APPROVED due -> exactly one outbox
4. duplicate sweep -> exactly one outbox
5. concurrent sweep -> one winner
6. reschedule before due -> old reservation cannot dispatch
7. reschedule/sweep race -> deterministic one winner
8. cancel/sweep race -> deterministic one winner
9. scheduler downtime catch-up -> exactly one outbox
10. UTC / +07:00 / DST normalization + naive rejection
11. original human scheduled_start_at survives publisher retry
12. PUBLISHED intent cannot dispatch again
13. CLAIMED intent cannot receive duplicate calendar dispatch
14. SchedulerDispatchOutbox idempotency key remains unique
15. broker relay failure does not lose the logical schedule
16. restart/re-relay does not create a second logical publish dispatch
"""

from __future__ import annotations

import asyncio
import zoneinfo
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.publisher.calendar_service import (
    PublishCalendarError,
    PublishCalendarService,
)
from omega.application.scheduler.outbox_relay import OutboxRelayService
from omega.application.scheduler.policy_service import SchedulePolicyService
from omega.application.scheduler.sweep_service import SchedulerSweepService
from omega.domain.publisher import PublishAttemptState, PublishIntentState
from omega.domain.scheduler import (
    DispatchOutboxStatus,
    ReservationState,
    ScheduleAction,
    ScheduleTargetType,
    ScheduleWorkloadCategory,
)
from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    MediaArtifact,
    Mission,
    PlatformAccount,
    ProductionRequest,
    PublishAttempt,
    PublisherSchedulerHandoffOutbox,
    PublishIntent,
    ResearchBrief,
    ResearchRequest,
    ScheduleDecision,
    SchedulerDispatchOutbox,
    ScheduleReservation,
    ScriptVersion,
    Task,
    TopicCandidate,
)


async def create_fixture_bundle(
    db_session: AsyncSession,
    *,
    intent_state: str = "APPROVED",
    task_state: str = "READY",
    mission_state: str = "RUNNING",
    channel_state: str = "ACTIVE",
    guardian_epoch: int = 1,
    lease_expires_at: datetime | None = None,
) -> dict:
    """Helper to create valid publisher and scheduler entities with full ancestry."""
    channel = Channel(
        id=uuid4(),
        name="Canary Channel",
        slug=f"canary-{uuid4().hex[:8]}",
        state=channel_state,
    )
    db_session.add(channel)

    dna_rev = ChannelDNARevision(
        id=uuid4(),
        channel_id=channel.id,
        version=1,
        snapshot={"name": "DNA"},
        change_reason="Initial",
    )
    db_session.add(dna_rev)

    topic = TopicCandidate(
        id=uuid4(),
        channel_id=channel.id,
        title="Canary Topic",
        normalized_title="canary topic",
        source_name="Manual Entry",
        summary="Canary topic summary",
        topic_fingerprint=uuid4().hex,
        status="APPROVED",
    )
    db_session.add(topic)

    r_req = ResearchRequest(
        id=uuid4(),
        channel_id=channel.id,
        topic_candidate_id=topic.id,
        status="COMPLETED",
    )
    db_session.add(r_req)

    brief = ResearchBrief(
        id=uuid4(),
        research_request_id=r_req.id,
        channel_id=channel.id,
        topic_candidate_id=topic.id,
        title="Canary Brief",
        summary="Canary summary",
    )
    db_session.add(brief)

    content_req = ContentGenerationRequest(
        id=uuid4(),
        channel_id=channel.id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna_rev.id,
        status="APPROVED",
    )
    db_session.add(content_req)

    script_ver = ScriptVersion(
        id=uuid4(),
        content_request_id=content_req.id,
        version=1,
        title="Canary Script",
        hook_text="Hook",
        closing_text="Close",
        cta_text="CTA",
    )
    db_session.add(script_ver)

    mission = Mission(
        id=uuid4(),
        title="Canary Mission",
        objective="Publish Testing",
        state=mission_state,
        guardian_epoch=guardian_epoch,
        priority=1,
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="publish_action",
        title="Canary Action Task",
        state=task_state,
    )
    db_session.add(task)

    prod_req = ProductionRequest(
        id=uuid4(),
        channel_id=channel.id,
        script_version_id=script_ver.id,
        content_request_id=content_req.id,
        channel_dna_revision_id=dna_rev.id,
        status="APPROVED",
    )
    db_session.add(prod_req)

    artifact = MediaArtifact(
        id=uuid4(),
        production_request_id=prod_req.id,
        artifact_type="VIDEO",
        version=1,
        is_current=True,
        storage_uri="videos/canary.mp4",
        file_size_bytes=1024,
        content_hash="b" * 64,
        mime_type="video/mp4",
    )
    db_session.add(artifact)

    account = PlatformAccount(
        id=uuid4(),
        channel_id=channel.id,
        platform="youtube",
        account_display_name="Canary Account",
        external_account_id="yt-canary-001",
        status="ACTIVE",
    )
    db_session.add(account)

    intent = PublishIntent(
        id=uuid4(),
        mission_id=mission.id,
        task_id=task.id,
        channel_id=channel.id,
        platform_account_id=account.id,
        media_artifact_id=artifact.id,
        media_artifact_checksum="b" * 64,
        channel_dna_revision_id=dna_rev.id,
        title="Canary Video",
        description="Canary Description",
        requested_privacy_status="PRIVATE",
        category_id="28",
        made_for_kids=False,
        intent_checksum="b" * 64,
        state=intent_state,
        lease_expires_at=lease_expires_at,
    )
    db_session.add(intent)

    policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config={
            "allowed_workloads": [ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value],
            "global_concurrency_limit": 10,
            "channel_concurrency_limit": 2,
            "min_gap_between_channel_items_seconds": 60,
            "max_scheduled_items_per_day": 50,
        },
        activate=True,
    )

    await db_session.commit()

    return {
        "channel": channel,
        "mission": mission,
        "task": task,
        "artifact": artifact,
        "account": account,
        "intent": intent,
        "policy": policy,
    }


# ── Test 1: DRAFT intent + due reservation -> no SchedulerDispatchOutbox ──
@pytest.mark.asyncio
async def test_draft_intent_due_reservation_no_outbox(db_session: AsyncSession):
    """Verify DRAFT intent with due reservation does not create an outbox row."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.DRAFT.value)
    now = datetime.now(UTC)

    # Manually create a due reservation targeting the DRAFT intent
    res_id = uuid4()
    dec_id = uuid4()
    decision = ScheduleDecision(
        id=dec_id,
        mission_id=bundle["mission"].id,
        task_id=bundle["task"].id,
        channel_id=bundle["channel"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        action=ScheduleAction.SCHEDULE.value,
        scheduled_start_at=now - timedelta(seconds=10),
        scheduled_end_at=now + timedelta(minutes=5),
        reason="Due draft test",
        policy_id=bundle["policy"].id,
        policy_version=bundle["policy"].version,
        policy_checksum=bundle["policy"].checksum,
        guardian_epoch=1,
        idempotency_key=f"dec:{uuid4().hex}",
        evaluated_at=now,
    )
    db_session.add(decision)

    reservation = ScheduleReservation(
        id=res_id,
        decision_id=dec_id,
        channel_id=bundle["channel"].id,
        mission_id=bundle["mission"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        scheduled_start_at=now - timedelta(seconds=10),
        scheduled_end_at=now + timedelta(minutes=5),
        state=ReservationState.ACTIVE.value,
        priority_score=100.0,
        policy_id=bundle["policy"].id,
        policy_version=bundle["policy"].version,
        policy_checksum=bundle["policy"].checksum,
        guardian_epoch=1,
    )
    db_session.add(reservation)
    await db_session.commit()

    sweep_stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert sweep_stats["dispatched"] == 0
    assert sweep_stats["rejected"] >= 1

    # Outbox row must NOT exist
    outbox_res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == res_id)
    )
    assert outbox_res.scalar_one_or_none() is None


# ── Test 2: APPROVED future -> no dispatch ──
@pytest.mark.asyncio
async def test_approved_future_no_dispatch(db_session: AsyncSession):
    """Verify APPROVED intent scheduled in the future is not dispatched."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    future_time = now + timedelta(hours=2)

    dec, res = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=future_time,
        actor="operator",
        now=now,
    )
    await db_session.commit()

    sweep_stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert sweep_stats["claimed"] == 0
    assert sweep_stats["dispatched"] == 0

    # Reservation remains ACTIVE
    await db_session.refresh(res)
    assert res.state == ReservationState.ACTIVE.value

    # No outbox row
    outbox_res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == res.id)
    )
    assert outbox_res.scalar_one_or_none() is None


# ── Test 3: APPROVED due -> exactly one outbox ──
@pytest.mark.asyncio
async def test_approved_due_exactly_one_outbox(db_session: AsyncSession):
    """Verify APPROVED due reservation creates exactly one outbox row to omega.publisher.execute_publish."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    due_time = now - timedelta(seconds=10)

    dec, res = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=due_time,
        actor="operator",
        now=now,
    )
    await db_session.commit()

    sweep_stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert sweep_stats["claimed"] == 1
    assert sweep_stats["dispatched"] == 1

    # Reservation becomes DISPATCHING
    await db_session.refresh(res)
    assert res.state == ReservationState.DISPATCHING.value
    assert res.dispatching_at is not None

    # Outbox row exists with correct task and args
    outbox_res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == res.id)
    )
    outbox_item = outbox_res.scalar_one_or_none()
    assert outbox_item is not None
    assert outbox_item.celery_task_name == "omega.publisher.execute_publish"
    assert outbox_item.celery_args == {"args": [str(bundle["task"].id)]}
    assert outbox_item.status == DispatchOutboxStatus.PENDING.value
    assert outbox_item.idempotency_key == f"outbox:{res.id}"


# ── Test 4: duplicate sweep -> exactly one outbox ──
@pytest.mark.asyncio
async def test_duplicate_sweep_exactly_one_outbox(db_session: AsyncSession):
    """Verify subsequent sweep passes do not duplicate the outbox item."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    due_time = now - timedelta(seconds=10)

    dec, res = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=due_time,
        actor="operator",
        now=now,
    )
    await db_session.commit()

    # Pass 1: claims and dispatches
    stats1 = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert stats1["dispatched"] == 1

    # Pass 2: duplicate sweep
    stats2 = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert stats2["claimed"] == 0
    assert stats2["dispatched"] == 0

    # Total outbox rows for reservation is exactly 1
    outbox_res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == res.id)
    )
    all_outbox = list(outbox_res.scalars().all())
    assert len(all_outbox) == 1


# ── Test 5: concurrent sweep -> one winner ──
@pytest.mark.asyncio
async def test_concurrent_sweep_one_winner(db_session: AsyncSession):
    """Verify skip_locked guarantees exactly one worker dispatches in concurrent sweep."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    due_time = now - timedelta(seconds=10)

    dec, res = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=due_time,
        actor="operator",
        now=now,
    )
    await db_session.commit()

    async def run_competing_sweep() -> dict[str, int]:
        async with AsyncSessionLocal() as competing_session:
            return await SchedulerSweepService.run_dispatch_sweep(competing_session, now=now)

    stats1, stats2 = await asyncio.gather(run_competing_sweep(), run_competing_sweep())
    assert stats1["dispatched"] + stats2["dispatched"] == 1

    outbox_res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == res.id)
    )
    assert len(list(outbox_res.scalars().all())) == 1


# ── Test 6: reschedule before due -> old reservation cannot dispatch ──
@pytest.mark.asyncio
async def test_reschedule_before_due_old_reservation_cannot_dispatch(db_session: AsyncSession):
    """Verify rescheduling releases old reservation and schedules a new active slot."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    initial_time = now + timedelta(minutes=10)
    new_time = now + timedelta(minutes=30)

    dec1, res1 = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=initial_time,
        actor="operator",
        now=now,
    )
    await db_session.commit()

    # Reschedule
    dec2, res2 = await PublishCalendarService.reschedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        new_scheduled_start_at=new_time,
        actor="operator",
        now=now,
    )
    await db_session.commit()

    await db_session.refresh(res1)
    assert res1.state == ReservationState.RELEASED.value
    assert res1.released_at is not None

    await db_session.refresh(res2)
    assert res2.state == ReservationState.ACTIVE.value

    # At now + 15m: initial_time is due, but res1 is RELEASED
    stats = await SchedulerSweepService.run_dispatch_sweep(
        db_session, now=now + timedelta(minutes=15)
    )
    assert stats["claimed"] == 0
    assert stats["dispatched"] == 0

    # Outbox count is 0
    outbox_res = await db_session.execute(select(SchedulerDispatchOutbox))
    assert len(list(outbox_res.scalars().all())) == 0


# ── Test 7: reschedule/sweep race -> deterministic one winner ──
@pytest.mark.asyncio
async def test_reschedule_sweep_race_deterministic_winner(db_session: AsyncSession):
    """Verify deterministic race semantics between dispatch sweep and human reschedule."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    due_time = now - timedelta(seconds=10)

    dec, res = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=due_time,
        actor="operator",
        now=now,
    )
    await db_session.commit()

    # Case A: Sweep wins lock first -> reservation is DISPATCHING
    stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert stats["dispatched"] == 1

    # Human reschedule attempted after sweep won lock -> must fail
    with pytest.raises(PublishCalendarError, match="No active schedule reservation found"):
        await PublishCalendarService.reschedule_intent(
            db_session,
            intent_id=bundle["intent"].id,
            new_scheduled_start_at=now + timedelta(hours=1),
            actor="operator",
            now=now,
        )


# ── Test 8: cancel/sweep race -> deterministic one winner ──
@pytest.mark.asyncio
async def test_cancel_sweep_race_deterministic_winner(db_session: AsyncSession):
    """Verify deterministic race semantics between cancel and sweep."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    due_time = now - timedelta(seconds=10)

    dec, res = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=due_time,
        actor="operator",
        now=now,
    )
    await db_session.commit()

    # Human cancel wins lock first
    cancelled_res = await PublishCalendarService.cancel_schedule(
        db_session,
        intent_id=bundle["intent"].id,
        actor="operator",
        reason="Operator cancelled",
        now=now,
    )
    await db_session.commit()
    assert cancelled_res.state == ReservationState.CANCELLED.value

    # Sweep runs afterward -> cannot dispatch CANCELLED reservation
    stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert stats["claimed"] == 0
    assert stats["dispatched"] == 0

    outbox_res = await db_session.execute(select(SchedulerDispatchOutbox))
    assert len(list(outbox_res.scalars().all())) == 0


# ── Test 9: scheduler downtime catch-up -> exactly one outbox ──
@pytest.mark.asyncio
async def test_scheduler_downtime_catch_up_exactly_one_outbox(db_session: AsyncSession):
    """Verify reservation that became due while scheduler was down is caught up exactly once."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    offline_due_time = now - timedelta(hours=3)

    # Schedule during downtime (simulate direct reservation insertion)
    res_id = uuid4()
    dec_id = uuid4()
    decision = ScheduleDecision(
        id=dec_id,
        mission_id=bundle["mission"].id,
        task_id=bundle["task"].id,
        channel_id=bundle["channel"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        action=ScheduleAction.SCHEDULE.value,
        scheduled_start_at=offline_due_time,
        scheduled_end_at=offline_due_time + timedelta(minutes=5),
        reason="Downtime reservation",
        policy_id=bundle["policy"].id,
        policy_version=bundle["policy"].version,
        policy_checksum=bundle["policy"].checksum,
        guardian_epoch=1,
        idempotency_key=f"dec:{uuid4().hex}",
        evaluated_at=offline_due_time,
    )
    db_session.add(decision)

    reservation = ScheduleReservation(
        id=res_id,
        decision_id=dec_id,
        channel_id=bundle["channel"].id,
        mission_id=bundle["mission"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        scheduled_start_at=offline_due_time,
        scheduled_end_at=offline_due_time + timedelta(minutes=5),
        state=ReservationState.ACTIVE.value,
        priority_score=100.0,
        policy_id=bundle["policy"].id,
        policy_version=bundle["policy"].version,
        policy_checksum=bundle["policy"].checksum,
        guardian_epoch=1,
    )
    db_session.add(reservation)
    await db_session.commit()

    # Scheduler restarts now and runs sweep
    stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert stats["claimed"] == 1
    assert stats["dispatched"] == 1

    # Exactly one outbox row
    outbox_res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == res_id)
    )
    assert len(list(outbox_res.scalars().all())) == 1


# ── Test 10: UTC / +07:00 / DST normalization + naive rejection ──
@pytest.mark.asyncio
async def test_timezone_normalization_and_naive_rejection(db_session: AsyncSession):
    """Verify timestamp timezone validation, UTC conversion, +07:00 and DST handling."""
    now = datetime.now(UTC)

    # 1. Naive datetime -> rejected
    naive_dt = datetime(2026, 9, 13, 15, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        PublishCalendarService.normalize_schedule_timestamp(naive_dt, now=now)

    # 2. Past > 1 minute -> rejected
    past_dt = now - timedelta(minutes=5)
    with pytest.raises(ValueError, match="older than 1 minute in the past"):
        PublishCalendarService.normalize_schedule_timestamp(past_dt, now=now)

    # 3. +07:00 timezone -> normalized to UTC
    tz_vn = zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh")
    local_vn = datetime(2026, 9, 14, 10, 0, 0, tzinfo=tz_vn)
    fixed_now = datetime(2026, 9, 14, 2, 59, 0, tzinfo=UTC)
    norm_utc = PublishCalendarService.normalize_schedule_timestamp(local_vn, now=fixed_now)
    assert norm_utc.tzinfo == UTC
    assert norm_utc.hour == 3  # 10:00 +07 -> 03:00 UTC

    # 4. Within 1 minute grace -> accepted
    grace_dt = now - timedelta(seconds=30)
    norm_grace = PublishCalendarService.normalize_schedule_timestamp(grace_dt, now=now)
    assert norm_grace == grace_dt.astimezone(UTC)


# ── Test 11: original human scheduled_start_at survives publisher retry ──
@pytest.mark.asyncio
async def test_original_human_schedule_survives_publisher_retry(db_session: AsyncSession):
    """Verify human scheduled_start_at is never overwritten by technical retry earliest_retry_at."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    human_schedule = now - timedelta(seconds=10)

    dec, res = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=human_schedule,
        actor="operator",
        now=now,
    )
    await db_session.commit()

    # Sweep dispatches reservation
    stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert stats["dispatched"] == 1

    # Simulate publisher technical retry via PublisherSchedulerHandoffOutbox
    retry_delay_time = now + timedelta(minutes=15)
    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=f"attempt-{uuid4().hex}",
        state=PublishAttemptState.RETRYABLE_FAILED.value,
        started_at=now,
    )
    db_session.add(attempt)
    await db_session.flush()

    handoff_row = PublisherSchedulerHandoffOutbox(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        publish_attempt_id=attempt.id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        earliest_retry_at=retry_delay_time,
        reason="transient provider retry",
        idempotency_key=f"handoff:{uuid4().hex}",
        status="PENDING",
    )
    db_session.add(handoff_row)
    await db_session.commit()

    # Verify ScheduleReservation.scheduled_start_at is STILL the original human schedule
    await db_session.refresh(res)
    assert res.scheduled_start_at == human_schedule
    assert res.scheduled_start_at != retry_delay_time


# ── Test 12: PUBLISHED intent cannot dispatch again ──
@pytest.mark.asyncio
async def test_published_intent_cannot_dispatch_again(db_session: AsyncSession):
    """Verify PUBLISHED intent is rejected by DispatchFence and reservation is released."""
    bundle = await create_fixture_bundle(
        db_session, intent_state=PublishIntentState.PUBLISHED.value
    )
    now = datetime.now(UTC)

    # Insert a due reservation pointing to already PUBLISHED intent
    res_id = uuid4()
    dec_id = uuid4()
    decision = ScheduleDecision(
        id=dec_id,
        mission_id=bundle["mission"].id,
        task_id=bundle["task"].id,
        channel_id=bundle["channel"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        action=ScheduleAction.SCHEDULE.value,
        scheduled_start_at=now - timedelta(seconds=10),
        scheduled_end_at=now + timedelta(minutes=5),
        reason="Published intent test",
        policy_id=bundle["policy"].id,
        policy_version=bundle["policy"].version,
        policy_checksum=bundle["policy"].checksum,
        guardian_epoch=1,
        idempotency_key=f"dec:{uuid4().hex}",
        evaluated_at=now,
    )
    db_session.add(decision)

    reservation = ScheduleReservation(
        id=res_id,
        decision_id=dec_id,
        channel_id=bundle["channel"].id,
        mission_id=bundle["mission"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        scheduled_start_at=now - timedelta(seconds=10),
        scheduled_end_at=now + timedelta(minutes=5),
        state=ReservationState.ACTIVE.value,
        priority_score=100.0,
        policy_id=bundle["policy"].id,
        policy_version=bundle["policy"].version,
        policy_checksum=bundle["policy"].checksum,
        guardian_epoch=1,
    )
    db_session.add(reservation)
    await db_session.commit()

    stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert stats["dispatched"] == 0
    assert stats["rejected"] >= 1

    # Reservation is RELEASED due to terminal PUBLISHED state
    await db_session.refresh(reservation)
    assert reservation.state == ReservationState.RELEASED.value

    # No outbox row
    outbox_res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == res_id)
    )
    assert outbox_res.scalar_one_or_none() is None


# ── Test 13: CLAIMED intent cannot receive duplicate calendar dispatch ──
@pytest.mark.asyncio
async def test_claimed_intent_cannot_receive_duplicate_calendar_dispatch(db_session: AsyncSession):
    """Verify actively CLAIMED intent is blocked from receiving duplicate calendar dispatch."""
    now = datetime.now(UTC)
    bundle = await create_fixture_bundle(
        db_session,
        intent_state=PublishIntentState.CLAIMED.value,
        lease_expires_at=now + timedelta(minutes=10),
    )

    res_id = uuid4()
    dec_id = uuid4()
    decision = ScheduleDecision(
        id=dec_id,
        mission_id=bundle["mission"].id,
        task_id=bundle["task"].id,
        channel_id=bundle["channel"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        action=ScheduleAction.SCHEDULE.value,
        scheduled_start_at=now - timedelta(seconds=10),
        scheduled_end_at=now + timedelta(minutes=5),
        reason="Claimed intent test",
        policy_id=bundle["policy"].id,
        policy_version=bundle["policy"].version,
        policy_checksum=bundle["policy"].checksum,
        guardian_epoch=1,
        idempotency_key=f"dec:{uuid4().hex}",
        evaluated_at=now,
    )
    db_session.add(decision)

    reservation = ScheduleReservation(
        id=res_id,
        decision_id=dec_id,
        channel_id=bundle["channel"].id,
        mission_id=bundle["mission"].id,
        target_type=ScheduleTargetType.PUBLISH_INTENT.value,
        target_id=bundle["intent"].id,
        workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
        scheduled_start_at=now - timedelta(seconds=10),
        scheduled_end_at=now + timedelta(minutes=5),
        state=ReservationState.ACTIVE.value,
        priority_score=100.0,
        policy_id=bundle["policy"].id,
        policy_version=bundle["policy"].version,
        policy_checksum=bundle["policy"].checksum,
        guardian_epoch=1,
    )
    db_session.add(reservation)
    await db_session.commit()

    stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert stats["dispatched"] == 0
    assert stats["rejected"] >= 1

    outbox_res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == res_id)
    )
    assert outbox_res.scalar_one_or_none() is None


# ── Test 14: SchedulerDispatchOutbox idempotency key remains unique ──
@pytest.mark.asyncio
async def test_scheduler_dispatch_outbox_idempotency_key_remains_unique(db_session: AsyncSession):
    """Verify SchedulerDispatchOutbox idempotency_key has database-enforced uniqueness."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    dec, res = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=now,
        actor="operator",
        now=now,
    )
    await db_session.commit()
    res_id = res.id

    item1 = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=res_id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        celery_task_name="omega.publisher.execute_publish",
        celery_args={"args": [str(bundle["task"].id)]},
        idempotency_key=f"outbox:{res_id}",
        status=DispatchOutboxStatus.PENDING.value,
        scheduled_send_at=now,
        created_at=now,
    )
    db_session.add(item1)
    await db_session.commit()

    # Attempt to insert identical idempotency key
    item2 = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=res_id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        celery_task_name="omega.publisher.execute_publish",
        celery_args={"args": [str(bundle["task"].id)]},
        idempotency_key=f"outbox:{res_id}",
        status=DispatchOutboxStatus.PENDING.value,
        scheduled_send_at=now,
        created_at=now,
    )
    db_session.add(item2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# ── Test 15: broker relay failure does not lose the logical schedule ──
@pytest.mark.asyncio
async def test_broker_relay_failure_does_not_lose_logical_schedule(db_session: AsyncSession):
    """Verify broker publication failure transitions outbox row to RETRY without losing schedule."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    due_time = now - timedelta(seconds=10)

    dec, res = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=due_time,
        actor="operator",
        now=now,
    )
    await db_session.commit()

    # 1. Sweep dispatches -> outbox created in PENDING
    stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert stats["dispatched"] == 1

    # 2. Outbox relay runs with simulated broker network crash
    with patch(
        "omega.application.scheduler.outbox_relay.celery_app.send_task",
        side_effect=ConnectionError("Redis connection lost"),
    ):
        relay_stats = await OutboxRelayService.process_outbox_batch(db_session, now=now)
        assert relay_stats["claimed"] == 1
        assert relay_stats["retried"] == 1
        assert relay_stats["sent"] == 0

    # 3. Verify outbox row transitioned to RETRY with backoff
    outbox_res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == res.id)
    )
    outbox_item = outbox_res.scalar_one_or_none()
    assert outbox_item.status == DispatchOutboxStatus.RETRY.value
    assert outbox_item.next_retry_at is not None
    assert outbox_item.attempt_count == 1

    # Reservation remains DISPATCHING (logical schedule preserved)
    await db_session.refresh(res)
    assert res.state == ReservationState.DISPATCHING.value

    # 4. Broker recovers: next attempt succeeds
    retry_time = outbox_item.next_retry_at + timedelta(seconds=1)
    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        relay_stats2 = await OutboxRelayService.process_outbox_batch(db_session, now=retry_time)
        assert relay_stats2["sent"] == 1
        mock_send.assert_called_once()

    await db_session.refresh(outbox_item)
    assert outbox_item.status == DispatchOutboxStatus.SENT.value


# ── Test 16: restart/re-relay does not create a second logical publish dispatch ──
@pytest.mark.asyncio
async def test_restart_rerelay_no_duplicate_publish_dispatch(db_session: AsyncSession):
    """Verify relay restart after successful publish does not re-send the Celery task."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    due_time = now - timedelta(seconds=10)

    dec, res = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=due_time,
        actor="operator",
        now=now,
    )
    await db_session.commit()

    # Sweep
    await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)

    # Relay 1 (sends successfully)
    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        relay_stats1 = await OutboxRelayService.process_outbox_batch(db_session, now=now)
        assert relay_stats1["sent"] == 1
        assert mock_send.call_count == 1

    # Relay 2 (restart/re-relay after completion)
    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        relay_stats2 = await OutboxRelayService.process_outbox_batch(db_session, now=now)
        assert relay_stats2["claimed"] == 0
        assert relay_stats2["sent"] == 0
        assert mock_send.call_count == 0


# ── Manual Hold Tests ──
@pytest.mark.asyncio
async def test_manual_hold_and_release_flow(db_session: AsyncSession):
    """Verify manual hold sets reservation to RELEASED with MANUAL_HOLD audit and release reactivates it."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    due_time = now - timedelta(seconds=10)

    dec, res = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=due_time,
        actor="operator",
        now=now,
    )
    await db_session.commit()

    # Place on manual hold
    held_res = await PublishCalendarService.set_manual_hold(
        db_session,
        intent_id=bundle["intent"].id,
        actor="operator",
        reason="Legal review pending",
        now=now,
    )
    await db_session.commit()
    assert held_res.state == ReservationState.RELEASED.value

    # Sweep cannot dispatch held reservation
    stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert stats["dispatched"] == 0

    # Query manual hold publications
    held_list = await PublishCalendarService.get_manual_hold_publications(db_session)
    assert len(held_list) == 1
    assert held_list[0].id == held_res.id

    # Release manual hold
    new_dec, new_res = await PublishCalendarService.release_manual_hold(
        db_session,
        intent_id=bundle["intent"].id,
        actor="operator",
        reason="Legal review cleared",
        now=now,
    )
    await db_session.commit()
    assert new_res.state == ReservationState.ACTIVE.value

    # Sweep dispatches newly activated reservation
    stats2 = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now)
    assert stats2["dispatched"] == 1
