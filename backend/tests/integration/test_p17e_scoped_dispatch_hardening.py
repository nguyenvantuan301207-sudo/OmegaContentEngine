"""Integration tests for P17-E0 scoped scheduler sweep and outbox relay hardening.

Validates that:
1. Broad scheduler sweep behavior is unchanged when reservation_ids=None
2. Empty reservation_ids returns immediately with zero work
3. Explicit reservation ID processes only that due reservation
4. Other due historical reservations remain untouched in ACTIVE state
5. Explicit reservation still passes DispatchFence (cannot bypass fence)
6. Explicit future reservation is NOT dispatched
7. Explicit non-ACTIVE reservation is NOT dispatched
8. Duplicate scoped sweep creates no second SchedulerDispatchOutbox
9. Concurrent scoped sweeps result in exactly one winner
10. Broad outbox relay behavior is unchanged when outbox_ids=None
11. Empty outbox_ids returns zero work
12. Explicit outbox ID sends only that row
13. Other eligible scheduler outbox rows remain untouched in PENDING
14. Explicit ineligible/future outbox is not sent
15. Scoped relay retry semantics are unchanged
16. Scoped relay dead-letter semantics are unchanged
17. Scoped relay preserves idempotency
18. HandoffRelayService.count_eligible_handoffs mirrors exact relay eligibility
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.publisher.calendar_service import PublishCalendarService
from omega.application.publisher.handoff_relay import HandoffRelayService
from omega.application.scheduler.outbox_relay import OutboxRelayService
from omega.application.scheduler.sweep_service import SchedulerSweepService
from omega.domain.publisher import HandoffStatus, PublishAttemptState, PublishIntentState
from omega.domain.scheduler import (
    DispatchOutboxStatus,
    ReservationState,
    ScheduleAction,
    ScheduleTargetType,
    ScheduleWorkloadCategory,
)
from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import (
    PublishAttempt,
    PublisherSchedulerHandoffOutbox,
    ScheduleDecision,
    SchedulerDispatchOutbox,
    ScheduleReservation,
)
from tests.integration.test_p17a_publish_scheduler import create_fixture_bundle


# ── Test 1: Broad scheduler sweep behavior unchanged when reservation_ids=None ──
@pytest.mark.asyncio
async def test_broad_scheduler_sweep_behavior_unchanged(db_session: AsyncSession):
    """When reservation_ids is None, all due active reservations are claimed and dispatched."""
    b1 = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    b2 = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)

    _, r1 = await PublishCalendarService.schedule_intent(
        db_session, intent_id=b1["intent"].id, scheduled_start_at=now - timedelta(seconds=10), actor="op", now=now
    )
    _, r2 = await PublishCalendarService.schedule_intent(
        db_session, intent_id=b2["intent"].id, scheduled_start_at=now - timedelta(seconds=5), actor="op", now=now
    )
    await db_session.commit()

    stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now, reservation_ids=None)
    assert stats["claimed"] == 2
    assert stats["dispatched"] == 2

    await db_session.refresh(r1)
    await db_session.refresh(r2)
    assert r1.state == ReservationState.DISPATCHING.value
    assert r2.state == ReservationState.DISPATCHING.value


# ── Test 2: Empty reservation_ids -> zero claimed ──
@pytest.mark.asyncio
async def test_empty_reservation_ids_zero_claimed(db_session: AsyncSession):
    """When reservation_ids is an empty collection, returns zero work immediately."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)

    _, res = await PublishCalendarService.schedule_intent(
        db_session, intent_id=bundle["intent"].id, scheduled_start_at=now - timedelta(seconds=10), actor="op", now=now
    )
    await db_session.commit()

    stats = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now, reservation_ids=[])
    assert stats == {"claimed": 0, "dispatched": 0, "rejected": 0}

    await db_session.refresh(res)
    assert res.state == ReservationState.ACTIVE.value

    outbox = (await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == res.id)
    )).scalar_one_or_none()
    assert outbox is None


# ── Test 3: Explicit reservation ID processes only that due reservation ──
@pytest.mark.asyncio
async def test_explicit_reservation_id_processes_only_that_due_reservation(db_session: AsyncSession):
    """Only the explicitly targeted reservation is claimed and transitioned to DISPATCHING."""
    b_target = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    b_other = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)

    _, r_target = await PublishCalendarService.schedule_intent(
        db_session, intent_id=b_target["intent"].id, scheduled_start_at=now - timedelta(seconds=10), actor="op", now=now
    )
    _, r_other = await PublishCalendarService.schedule_intent(
        db_session, intent_id=b_other["intent"].id, scheduled_start_at=now - timedelta(seconds=30), actor="op", now=now
    )
    await db_session.commit()

    stats = await SchedulerSweepService.run_dispatch_sweep(
        db_session, now=now, reservation_ids=[r_target.id]
    )
    assert stats["claimed"] == 1
    assert stats["dispatched"] == 1

    await db_session.refresh(r_target)
    assert r_target.state == ReservationState.DISPATCHING.value
    outbox_target = (await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == r_target.id)
    )).scalar_one_or_none()
    assert outbox_target is not None
    assert outbox_target.status == DispatchOutboxStatus.PENDING.value


# ── Test 4: Other due historical reservation remains ACTIVE ──
@pytest.mark.asyncio
async def test_other_due_historical_reservation_remains_active(db_session: AsyncSession):
    """When a scoped sweep runs for one reservation, other due historical reservations remain strictly ACTIVE."""
    b_target = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    b_historical = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)

    _, r_target = await PublishCalendarService.schedule_intent(
        db_session, intent_id=b_target["intent"].id, scheduled_start_at=now - timedelta(seconds=10), actor="op", now=now
    )
    _, r_historical = await PublishCalendarService.schedule_intent(
        db_session, intent_id=b_historical["intent"].id, scheduled_start_at=now - timedelta(seconds=30), actor="op", now=now
    )
    await db_session.commit()

    # Sweep target only
    await SchedulerSweepService.run_dispatch_sweep(
        db_session, now=now, reservation_ids=[r_target.id]
    )

    await db_session.refresh(r_historical)
    assert r_historical.state == ReservationState.ACTIVE.value
    outbox_historical = (await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == r_historical.id)
    )).scalar_one_or_none()
    assert outbox_historical is None


# ── Test 5: Explicit reservation still passes DispatchFence ──
@pytest.mark.asyncio
async def test_explicit_reservation_enforces_dispatch_fence(db_session: AsyncSession):
    """Scoped sweep still enforces DispatchFence: if fence rejects (e.g. intent is DRAFT), reservation is rejected."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.DRAFT.value)
    now = datetime.now(UTC)

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

    stats = await SchedulerSweepService.run_dispatch_sweep(
        db_session, now=now, reservation_ids=[res_id]
    )
    assert stats["claimed"] == 1
    assert stats["dispatched"] == 0
    assert stats["rejected"] == 1

    await db_session.refresh(reservation)
    # Remains ACTIVE (fence rejected dispatch)
    assert reservation.state == ReservationState.ACTIVE.value

    outbox = (await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == res_id)
    )).scalar_one_or_none()
    assert outbox is None


# ── Test 6: Explicit future reservation is NOT dispatched ──
@pytest.mark.asyncio
async def test_explicit_future_reservation_not_dispatched(db_session: AsyncSession):
    """An explicit reservation scheduled in the future is not dispatched even if its ID is provided."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)

    _, res = await PublishCalendarService.schedule_intent(
        db_session, intent_id=bundle["intent"].id, scheduled_start_at=now + timedelta(hours=1), actor="op", now=now
    )
    await db_session.commit()

    stats = await SchedulerSweepService.run_dispatch_sweep(
        db_session, now=now, reservation_ids=[res.id]
    )
    assert stats["claimed"] == 0
    assert stats["dispatched"] == 0

    await db_session.refresh(res)
    assert res.state == ReservationState.ACTIVE.value


# ── Test 7: Explicit non-ACTIVE reservation is NOT dispatched ──
@pytest.mark.asyncio
async def test_explicit_non_active_reservation_not_dispatched(db_session: AsyncSession):
    """An explicit reservation in RELEASED or CANCELLED state is ignored by the scoped sweep."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)

    _, res = await PublishCalendarService.schedule_intent(
        db_session, intent_id=bundle["intent"].id, scheduled_start_at=now - timedelta(seconds=10), actor="op", now=now
    )
    res.state = ReservationState.RELEASED.value
    await db_session.commit()

    stats = await SchedulerSweepService.run_dispatch_sweep(
        db_session, now=now, reservation_ids=[res.id]
    )
    assert stats["claimed"] == 0
    assert stats["dispatched"] == 0


# ── Test 8: Duplicate scoped sweep creates no second SchedulerDispatchOutbox ──
@pytest.mark.asyncio
async def test_duplicate_scoped_sweep_creates_no_second_outbox(db_session: AsyncSession):
    """Running scoped sweep repeatedly for the same reservation produces exactly one outbox row."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)

    _, res = await PublishCalendarService.schedule_intent(
        db_session, intent_id=bundle["intent"].id, scheduled_start_at=now - timedelta(seconds=10), actor="op", now=now
    )
    await db_session.commit()

    stats1 = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now, reservation_ids=[res.id])
    assert stats1["claimed"] == 1
    assert stats1["dispatched"] == 1

    stats2 = await SchedulerSweepService.run_dispatch_sweep(db_session, now=now, reservation_ids=[res.id])
    assert stats2["claimed"] == 0
    assert stats2["dispatched"] == 0

    outbox_count = (await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.reservation_id == res.id)
    )).scalars().all()
    assert len(outbox_count) == 1


# ── Test 9: Concurrent scoped sweeps result in exactly one winner ──
@pytest.mark.asyncio
async def test_concurrent_scoped_sweeps_one_winner(db_session: AsyncSession):
    """Concurrent scoped sweeps competing for the same reservation ID yield exactly one dispatch."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)

    _, res = await PublishCalendarService.schedule_intent(
        db_session, intent_id=bundle["intent"].id, scheduled_start_at=now - timedelta(seconds=10), actor="op", now=now
    )
    await db_session.commit()
    target_id = res.id

    async def run_competing_sweep():
        async with AsyncSessionLocal() as competing_session:
            return await SchedulerSweepService.run_dispatch_sweep(
                competing_session, now=now, reservation_ids=[target_id]
            )

    results = await asyncio.gather(run_competing_sweep(), run_competing_sweep())
    dispatched_sum = sum(r["dispatched"] for r in results)
    assert dispatched_sum == 1, f"Expected exactly 1 dispatched, got {dispatched_sum}"


# ── Helper for outbox rows with valid foreign keys ──
async def _create_outbox_item(
    db_session: AsyncSession,
    *,
    scheduled_send_at: datetime,
    status: DispatchOutboxStatus = DispatchOutboxStatus.PENDING,
    next_retry_at: datetime | None = None,
    attempt_count: int = 0,
    max_attempts: int = 5,
) -> SchedulerDispatchOutbox:
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    _, res = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=scheduled_send_at,
        actor="operator",
        now=now,
    )
    await db_session.commit()

    item = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=res.id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        celery_task_name="omega.publisher.execute_publish",
        celery_args={"args": [str(bundle["task"].id)]},
        idempotency_key=f"outbox:{uuid4()}",
        status=status.value,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        scheduled_send_at=scheduled_send_at,
        next_retry_at=next_retry_at,
        created_at=scheduled_send_at,
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return item


# ── Test 10: Broad outbox relay behavior unchanged when outbox_ids=None ──
@pytest.mark.asyncio
async def test_broad_outbox_relay_behavior_unchanged(db_session: AsyncSession):
    """When outbox_ids is None, all eligible pending outbox rows are claimed and sent."""
    now = datetime.now(UTC)
    ob1 = await _create_outbox_item(db_session, scheduled_send_at=now - timedelta(seconds=10))
    ob2 = await _create_outbox_item(db_session, scheduled_send_at=now - timedelta(seconds=5))

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        stats = await OutboxRelayService.process_outbox_batch(db_session, now=now, outbox_ids=None)
        assert stats["claimed"] == 2
        assert stats["sent"] == 2
        assert mock_send.call_count == 2

    await db_session.refresh(ob1)
    await db_session.refresh(ob2)
    assert ob1.status == DispatchOutboxStatus.SENT.value
    assert ob2.status == DispatchOutboxStatus.SENT.value


# ── Test 11: Empty outbox_ids -> zero claimed ──
@pytest.mark.asyncio
async def test_empty_outbox_ids_zero_claimed(db_session: AsyncSession):
    """When outbox_ids is an empty collection, zero rows are claimed or sent."""
    now = datetime.now(UTC)
    ob = await _create_outbox_item(db_session, scheduled_send_at=now - timedelta(seconds=10))

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        stats = await OutboxRelayService.process_outbox_batch(db_session, now=now, outbox_ids=[])
        assert stats == {"claimed": 0, "sent": 0, "retried": 0, "dead_letter": 0}
        assert mock_send.call_count == 0

    await db_session.refresh(ob)
    assert ob.status == DispatchOutboxStatus.PENDING.value


# ── Test 12: Explicit outbox ID sends only that row ──
@pytest.mark.asyncio
async def test_explicit_outbox_id_sends_only_that_row(db_session: AsyncSession):
    """Only the explicitly specified outbox ID is published to the broker and transitioned to SENT."""
    now = datetime.now(UTC)
    ob_target = await _create_outbox_item(db_session, scheduled_send_at=now - timedelta(seconds=10))
    ob_other = await _create_outbox_item(db_session, scheduled_send_at=now - timedelta(seconds=20))

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        stats = await OutboxRelayService.process_outbox_batch(
            db_session, now=now, outbox_ids=[ob_target.id]
        )
        assert stats["claimed"] == 1
        assert stats["sent"] == 1
        assert mock_send.call_count == 1

    await db_session.refresh(ob_target)
    assert ob_target.status == DispatchOutboxStatus.SENT.value
    assert ob_target.attempt_count == 1
    assert ob_target.sent_at is not None

    await db_session.refresh(ob_other)
    assert ob_other.status == DispatchOutboxStatus.PENDING.value


# ── Test 13: Other eligible scheduler outbox remains untouched ──
@pytest.mark.asyncio
async def test_other_eligible_scheduler_outbox_remains_untouched(db_session: AsyncSession):
    """When an explicit outbox ID is relayed, other eligible outbox rows remain strictly untouched in PENDING."""
    now = datetime.now(UTC)
    ob_target = await _create_outbox_item(db_session, scheduled_send_at=now - timedelta(seconds=10))
    ob_other = await _create_outbox_item(db_session, scheduled_send_at=now - timedelta(seconds=20))

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task"):
        await OutboxRelayService.process_outbox_batch(
            db_session, now=now, outbox_ids=[ob_target.id]
        )

    await db_session.refresh(ob_other)
    assert ob_other.status == DispatchOutboxStatus.PENDING.value
    assert ob_other.attempt_count == 0
    assert ob_other.sent_at is None


# ── Test 14: Explicit ineligible/future outbox is not sent ──
@pytest.mark.asyncio
async def test_explicit_ineligible_future_outbox_not_sent(db_session: AsyncSession):
    """An outbox row scheduled for the future is not sent even if its ID is explicitly passed."""
    now = datetime.now(UTC)
    ob_future = await _create_outbox_item(db_session, scheduled_send_at=now + timedelta(minutes=10))

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        stats = await OutboxRelayService.process_outbox_batch(
            db_session, now=now, outbox_ids=[ob_future.id]
        )
        assert stats["claimed"] == 0
        assert stats["sent"] == 0
        assert mock_send.call_count == 0

    await db_session.refresh(ob_future)
    assert ob_future.status == DispatchOutboxStatus.PENDING.value


# ── Test 15: Scoped relay retry semantics unchanged ──
@pytest.mark.asyncio
async def test_scoped_relay_retry_semantics_unchanged(db_session: AsyncSession):
    """Broker failures during scoped relay set row to RETRY with exponential backoff."""
    now = datetime.now(UTC)
    ob = await _create_outbox_item(db_session, scheduled_send_at=now - timedelta(seconds=10))

    with patch(
        "omega.application.scheduler.outbox_relay.celery_app.send_task",
        side_effect=ConnectionError("Broker unreachable"),
    ):
        stats = await OutboxRelayService.process_outbox_batch(
            db_session, now=now, outbox_ids=[ob.id]
        )
        assert stats["claimed"] == 1
        assert stats["sent"] == 0
        assert stats["retried"] == 1
        assert stats["dead_letter"] == 0

    await db_session.refresh(ob)
    assert ob.status == DispatchOutboxStatus.RETRY.value
    assert ob.attempt_count == 1
    assert ob.next_retry_at is not None
    assert ob.next_retry_at > now


# ── Test 16: Scoped relay dead-letter semantics unchanged ──
@pytest.mark.asyncio
async def test_scoped_relay_dead_letter_semantics_unchanged(db_session: AsyncSession):
    """When attempt_count reaches max_attempts, scoped relay transitions row to DEAD_LETTER."""
    now = datetime.now(UTC)
    ob = await _create_outbox_item(
        db_session,
        scheduled_send_at=now - timedelta(seconds=10),
        attempt_count=4,
        max_attempts=5,
    )

    with patch(
        "omega.application.scheduler.outbox_relay.celery_app.send_task",
        side_effect=RuntimeError("Permanent dispatch error"),
    ):
        stats = await OutboxRelayService.process_outbox_batch(
            db_session, now=now, outbox_ids=[ob.id]
        )
        assert stats["claimed"] == 1
        assert stats["sent"] == 0
        assert stats["retried"] == 0
        assert stats["dead_letter"] == 1

    await db_session.refresh(ob)
    assert ob.status == DispatchOutboxStatus.DEAD_LETTER.value
    assert ob.attempt_count == 5


# ── Test 17: Scoped relay preserves idempotency ──
@pytest.mark.asyncio
async def test_scoped_relay_preserves_idempotency(db_session: AsyncSession):
    """Re-processing an already-sent outbox ID claims zero rows and does not duplicate broker sends."""
    now = datetime.now(UTC)
    ob = await _create_outbox_item(db_session, scheduled_send_at=now - timedelta(seconds=10))

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        stats1 = await OutboxRelayService.process_outbox_batch(
            db_session, now=now, outbox_ids=[ob.id]
        )
        assert stats1["claimed"] == 1
        assert stats1["sent"] == 1
        assert mock_send.call_count == 1

    # Second pass for the same ID
    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        stats2 = await OutboxRelayService.process_outbox_batch(
            db_session, now=now, outbox_ids=[ob.id]
        )
        assert stats2["claimed"] == 0
        assert stats2["sent"] == 0
        assert mock_send.call_count == 0


# ── Test 18: HandoffRelayService.count_eligible_handoffs mirrors exact relay eligibility ──
@pytest.mark.asyncio
async def test_count_eligible_handoffs_mirrors_relay_eligibility(db_session: AsyncSession):
    """Verifies that count_eligible_handoffs accurately reflects HandoffRelayService claim criteria."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=f"attempt:{uuid4().hex}",
        state=PublishAttemptState.RETRYABLE_FAILED.value,
    )
    db_session.add(attempt)
    await db_session.commit()

    def _make_handoff(
        status: HandoffStatus,
        next_attempt_at: datetime,
        lease_expires_at: datetime | None = None,
        attempt_count: int = 1,
    ) -> PublisherSchedulerHandoffOutbox:
        return PublisherSchedulerHandoffOutbox(
            id=uuid4(),
            publish_intent_id=bundle["intent"].id,
            publish_attempt_id=attempt.id,
            task_id=bundle["task"].id,
            mission_id=bundle["mission"].id,
            earliest_retry_at=now,
            reason="test retry eligibility",
            idempotency_key=f"handoff:{uuid4().hex}",
            status=status.value,
            lease_expires_at=lease_expires_at,
            next_attempt_at=next_attempt_at,
            attempt_count=attempt_count,
        )

    # 1. PENDING with next_attempt_at in the past -> ELIGIBLE
    h1 = _make_handoff(HandoffStatus.PENDING, next_attempt_at=now - timedelta(seconds=10))
    # 2. PENDING with next_attempt_at in future -> INELIGIBLE
    h2 = _make_handoff(HandoffStatus.PENDING, next_attempt_at=now + timedelta(minutes=10))
    # 3. CLAIMED with expired lease and next_attempt_at <= now -> ELIGIBLE (stale claim recovery)
    h3 = _make_handoff(
        HandoffStatus.CLAIMED,
        next_attempt_at=now - timedelta(seconds=10),
        lease_expires_at=now - timedelta(seconds=5),
    )
    # 4. CLAIMED with active lease -> INELIGIBLE
    h4 = _make_handoff(
        HandoffStatus.CLAIMED,
        next_attempt_at=now - timedelta(seconds=10),
        lease_expires_at=now + timedelta(minutes=5),
    )
    # 5. DELIVERED -> INELIGIBLE
    h5 = _make_handoff(HandoffStatus.DELIVERED, next_attempt_at=now - timedelta(seconds=10))
    # 6. DEAD_LETTER -> INELIGIBLE
    h6 = _make_handoff(
        HandoffStatus.DEAD_LETTER,
        next_attempt_at=now - timedelta(seconds=10),
        attempt_count=5,
    )

    db_session.add_all([h1, h2, h3, h4, h5, h6])
    await db_session.commit()

    count = await HandoffRelayService.count_eligible_handoffs(db_session, now=now)
    # Exactly h1 and h3 are eligible
    assert count == 2
