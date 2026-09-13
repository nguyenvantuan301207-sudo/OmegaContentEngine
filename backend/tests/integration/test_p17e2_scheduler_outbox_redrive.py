"""Integration and safety contract tests for P17-E2 controlled broker redrive of SENT scheduler outbox.

Validates that:
1. eligible SENT publisher outbox can redrive
2. payload is exact args + empty kwargs
3. original outbox remains SENT
4. original outbox ID unchanged
5. original idempotency key unchanged
6. audit transition written (DISPATCHING -> DISPATCHING)
7. actor required
8. reason required
9. PENDING outbox rejected
10. RETRY outbox rejected
11. DEAD_LETTER outbox rejected
12. reservation not DISPATCHING rejected
13. task not QUEUED rejected
14. intent not APPROVED rejected
15. existing PublishAttempt blocks redrive
16. existing UploadSession blocks redrive
17. provider video evidence blocks redrive
18. concurrent same redrive -> one broker send
19. malformed stored payload -> zero broker sends
20. no provider/network adapter invoked
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.publisher.calendar_service import PublishCalendarService
from omega.application.scheduler.outbox_relay import OutboxRelayService
from omega.domain.publisher import PublishIntentState
from omega.domain.scheduler import DispatchOutboxStatus, ReservationState
from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import (
    PublishAttempt,
    SchedulerDispatchOutbox,
    ScheduleStateTransition,
    UploadSession,
)
from tests.integration.test_p17a_publish_scheduler import create_fixture_bundle


async def create_sent_canary_fixture(db_session: AsyncSession) -> dict[str, Any]:
    """Create a fixture bundle matching the authoritative P17-E canary state."""
    bundle = await create_fixture_bundle(db_session, intent_state=PublishIntentState.APPROVED.value)
    now = datetime.now(UTC)
    dec, res = await PublishCalendarService.schedule_intent(
        db_session,
        intent_id=bundle["intent"].id,
        scheduled_start_at=now,
        actor="operator",
        now=now,
    )
    # Reservation is in DISPATCHING state
    res.state = ReservationState.DISPATCHING.value
    res.dispatching_at = now

    # Task is in QUEUED state
    task = bundle["task"]
    task.state = "QUEUED"

    outbox = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=res.id,
        task_id=task.id,
        mission_id=bundle["mission"].id,
        celery_task_name="omega.publisher.execute_publish",
        celery_args={"args": [str(task.id)]},
        idempotency_key=f"outbox:{res.id}",
        status=DispatchOutboxStatus.SENT.value,
        attempt_count=1,
        max_attempts=5,
        scheduled_send_at=now,
        sent_at=now,
        created_at=now,
    )
    db_session.add(outbox)
    await db_session.commit()

    bundle["decision"] = dec
    bundle["reservation"] = res
    bundle["outbox"] = outbox
    return bundle


# ── Test 1: Eligible SENT publisher outbox can redrive ──
@pytest.mark.asyncio
async def test_eligible_sent_publisher_outbox_can_redrive(db_session: AsyncSession):
    """Eligible SENT publisher outbox can be redriven successfully."""
    bundle = await create_sent_canary_fixture(db_session)
    outbox_id = bundle["outbox"].id

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        result = await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=outbox_id,
            actor="operator_alice",
            reason="preexecution failure recovery",
            idempotency_key="op-key-001",
        )

        assert result["redriven"] is True
        assert result["status"] == DispatchOutboxStatus.SENT.value
        assert result["outbox_id"] == outbox_id
        mock_send.assert_called_once()


# ── Test 2: Payload is exact args + empty kwargs ──
@pytest.mark.asyncio
async def test_payload_is_exact_args_plus_empty_kwargs(db_session: AsyncSession):
    """Redrive emits args=[str(task_id)] and kwargs={} with no duplicate args keyword."""
    bundle = await create_sent_canary_fixture(db_session)
    task_id = str(bundle["task"].id)

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=bundle["outbox"].id,
            actor="operator_alice",
            reason="payload verification",
            idempotency_key="op-key-002",
        )

        mock_send.assert_called_once_with(
            "omega.publisher.execute_publish",
            args=[task_id],
            kwargs={},
        )
        kwargs_used = mock_send.call_args.kwargs.get("kwargs")
        assert kwargs_used == {}
        assert kwargs_used != {"args": [task_id]}


# ── Test 3: Original outbox remains SENT ──
@pytest.mark.asyncio
async def test_original_outbox_remains_sent(db_session: AsyncSession):
    """Original SchedulerDispatchOutbox row remains in status SENT."""
    bundle = await create_sent_canary_fixture(db_session)
    outbox_id = bundle["outbox"].id

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task"):
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=outbox_id,
            actor="operator_alice",
            reason="status preservation",
            idempotency_key="op-key-003",
        )

    res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.id == outbox_id)
    )
    item = res.scalar_one()
    assert item.status == DispatchOutboxStatus.SENT.value


# ── Test 4: Original outbox ID unchanged ──
@pytest.mark.asyncio
async def test_original_outbox_id_unchanged(db_session: AsyncSession):
    """Original outbox ID is unchanged after redrive."""
    bundle = await create_sent_canary_fixture(db_session)
    original_id = bundle["outbox"].id

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task"):
        result = await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=original_id,
            actor="operator_alice",
            reason="id preservation",
            idempotency_key="op-key-004",
        )

    assert result["outbox_id"] == original_id


# ── Test 5: Original idempotency key unchanged ──
@pytest.mark.asyncio
async def test_original_idempotency_key_unchanged(db_session: AsyncSession):
    """Original outbox idempotency_key is unchanged after redrive."""
    bundle = await create_sent_canary_fixture(db_session)
    outbox_id = bundle["outbox"].id
    original_key = bundle["outbox"].idempotency_key

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task"):
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=outbox_id,
            actor="operator_alice",
            reason="idempotency key preservation",
            idempotency_key="op-key-005",
        )

    res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.id == outbox_id)
    )
    item = res.scalar_one()
    assert item.idempotency_key == original_key


# ── Test 6: Audit transition written ──
@pytest.mark.asyncio
async def test_audit_transition_written(db_session: AsyncSession):
    """Append-only ScheduleStateTransition (DISPATCHING -> DISPATCHING) is recorded."""
    bundle = await create_sent_canary_fixture(db_session)
    outbox_id = bundle["outbox"].id
    res_id = bundle["reservation"].id

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task"):
        result = await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=outbox_id,
            actor="operator_alice",
            reason="test audit reason",
            idempotency_key="op-key-006",
        )

    transition_id = result["transition_id"]
    res = await db_session.execute(
        select(ScheduleStateTransition).where(ScheduleStateTransition.id == transition_id)
    )
    transition = res.scalar_one()
    assert transition.reservation_id == res_id
    assert transition.from_state == ReservationState.DISPATCHING.value
    assert transition.to_state == ReservationState.DISPATCHING.value
    assert transition.actor == "operator_alice"
    assert "MANUAL_BROKER_REDRIVE_AFTER_PREEXECUTION_FAILURE" in transition.reason
    assert f"outbox_id={outbox_id}" in transition.reason
    assert "idempotency_key=op-key-006" in transition.reason


# ── Test 7: Actor required ──
@pytest.mark.asyncio
async def test_actor_required(db_session: AsyncSession):
    """Missing or blank actor raises ValueError."""
    bundle = await create_sent_canary_fixture(db_session)

    with pytest.raises(ValueError, match="actor is required"):
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=bundle["outbox"].id,
            actor="",
            reason="reason",
            idempotency_key="op-key-007",
        )


# ── Test 8: Reason required ──
@pytest.mark.asyncio
async def test_reason_required(db_session: AsyncSession):
    """Missing or blank reason raises ValueError."""
    bundle = await create_sent_canary_fixture(db_session)

    with pytest.raises(ValueError, match="reason is required"):
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=bundle["outbox"].id,
            actor="operator_alice",
            reason="   ",
            idempotency_key="op-key-008",
        )


# ── Test 9: PENDING outbox rejected ──
@pytest.mark.asyncio
async def test_pending_outbox_rejected(db_session: AsyncSession):
    """Outbox with status PENDING cannot be manually redriven."""
    bundle = await create_sent_canary_fixture(db_session)
    bundle["outbox"].status = DispatchOutboxStatus.PENDING.value
    await db_session.commit()

    with pytest.raises(ValueError, match="status is 'PENDING'"):
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=bundle["outbox"].id,
            actor="operator_alice",
            reason="reason",
            idempotency_key="op-key-009",
        )


# ── Test 10: RETRY outbox rejected ──
@pytest.mark.asyncio
async def test_retry_outbox_rejected(db_session: AsyncSession):
    """Outbox with status RETRY cannot be manually redriven."""
    bundle = await create_sent_canary_fixture(db_session)
    bundle["outbox"].status = DispatchOutboxStatus.RETRY.value
    await db_session.commit()

    with pytest.raises(ValueError, match="status is 'RETRY'"):
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=bundle["outbox"].id,
            actor="operator_alice",
            reason="reason",
            idempotency_key="op-key-010",
        )


# ── Test 11: DEAD_LETTER outbox rejected ──
@pytest.mark.asyncio
async def test_dead_letter_outbox_rejected(db_session: AsyncSession):
    """Outbox with status DEAD_LETTER cannot be manually redriven."""
    bundle = await create_sent_canary_fixture(db_session)
    bundle["outbox"].status = DispatchOutboxStatus.DEAD_LETTER.value
    await db_session.commit()

    with pytest.raises(ValueError, match="status is 'DEAD_LETTER'"):
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=bundle["outbox"].id,
            actor="operator_alice",
            reason="reason",
            idempotency_key="op-key-011",
        )


# ── Test 12: Reservation not DISPATCHING rejected ──
@pytest.mark.asyncio
async def test_reservation_not_dispatching_rejected(db_session: AsyncSession):
    """Reservation not in DISPATCHING state rejects redrive."""
    bundle = await create_sent_canary_fixture(db_session)
    bundle["reservation"].state = ReservationState.ACTIVE.value
    await db_session.commit()

    with pytest.raises(ValueError, match="expected 'DISPATCHING'"):
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=bundle["outbox"].id,
            actor="operator_alice",
            reason="reason",
            idempotency_key="op-key-012",
        )


# ── Test 13: Task not QUEUED rejected ──
@pytest.mark.asyncio
async def test_task_not_queued_rejected(db_session: AsyncSession):
    """Task not in QUEUED state rejects redrive."""
    bundle = await create_sent_canary_fixture(db_session)
    bundle["task"].state = "RUNNING"
    await db_session.commit()

    with pytest.raises(ValueError, match="Task .* state is 'RUNNING', expected 'QUEUED'"):
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=bundle["outbox"].id,
            actor="operator_alice",
            reason="reason",
            idempotency_key="op-key-013",
        )


# ── Test 14: Intent not APPROVED rejected ──
@pytest.mark.asyncio
async def test_intent_not_approved_rejected(db_session: AsyncSession):
    """PublishIntent not in APPROVED state rejects redrive."""
    bundle = await create_sent_canary_fixture(db_session)
    bundle["intent"].state = PublishIntentState.DRAFT.value
    await db_session.commit()

    with pytest.raises(ValueError, match="expected 'APPROVED'"):
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=bundle["outbox"].id,
            actor="operator_alice",
            reason="reason",
            idempotency_key="op-key-014",
        )


# ── Test 15: Existing PublishAttempt blocks redrive ──
@pytest.mark.asyncio
async def test_existing_publish_attempt_blocks_redrive(db_session: AsyncSession):
    """Any existing PublishAttempt row for the intent blocks redrive."""
    bundle = await create_sent_canary_fixture(db_session)
    now = datetime.now(UTC)

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=f"att:{uuid4()}",
        state="CREATED",
        started_at=now,
    )
    db_session.add(attempt)
    await db_session.commit()

    with pytest.raises(ValueError, match="PublishAttempt.*already exist"):
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=bundle["outbox"].id,
            actor="operator_alice",
            reason="reason",
            idempotency_key="op-key-015",
        )


# ── Test 16: Existing UploadSession blocks redrive ──
@pytest.mark.asyncio
async def test_existing_upload_session_blocks_redrive(db_session: AsyncSession):
    """Any existing UploadSession row for the intent blocks redrive."""
    bundle = await create_sent_canary_fixture(db_session)
    now = datetime.now(UTC)

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=f"att:{uuid4()}",
        state="UPLOADING",
        started_at=now,
    )
    db_session.add(attempt)
    await db_session.commit()

    session_item = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt.id,
        session_uri="https://upload.example.com/session/123",
        total_bytes=1000,
        bytes_uploaded=500,
        expires_at=now + timedelta(hours=1),
    )
    db_session.add(session_item)
    await db_session.commit()

    with pytest.raises(ValueError, match="PublishAttempt.*already exist|UploadSession.*already exist"):
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=bundle["outbox"].id,
            actor="operator_alice",
            reason="reason",
            idempotency_key="op-key-016",
        )


# ── Test 17: Provider video evidence blocks redrive ──
@pytest.mark.asyncio
async def test_provider_video_evidence_blocks_redrive(db_session: AsyncSession):
    """Any provider_video_id evidence for the intent blocks redrive."""
    bundle = await create_sent_canary_fixture(db_session)
    now = datetime.now(UTC)

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=bundle["intent"].id,
        attempt_number=1,
        idempotency_key=f"att:{uuid4()}",
        state="SUCCEEDED",
        provider_video_id="yt-canary-evidence-123",
        started_at=now,
    )
    db_session.add(attempt)
    await db_session.commit()

    with pytest.raises(ValueError, match="already exist"):
        await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=bundle["outbox"].id,
            actor="operator_alice",
            reason="reason",
            idempotency_key="op-key-017",
        )


# ── Test 18: Concurrent same redrive -> at most one broker send ──
@pytest.mark.asyncio
async def test_concurrent_same_redrive_one_broker_send():
    """Two concurrent redrives with the same idempotency key produce at most 1 broker send."""
    async with AsyncSessionLocal() as setup_session:
        bundle = await create_sent_canary_fixture(setup_session)
        outbox_id = bundle["outbox"].id

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        async def _call():
            async with AsyncSessionLocal() as sess:
                return await OutboxRelayService.redrive_sent_item(
                    sess,
                    outbox_id=outbox_id,
                    actor="operator_alice",
                    reason="concurrent test",
                    idempotency_key="concurrent-key-001",
                )

        results = await asyncio.gather(_call(), _call(), return_exceptions=True)

        successes = [r for r in results if isinstance(r, dict) and r.get("redriven") is True]
        failures = [r for r in results if isinstance(r, Exception)]

        assert len(successes) == 1
        assert len(failures) == 1
        assert "already executed" in str(failures[0])
        mock_send.assert_called_once()


# ── Test 19: Malformed stored payload -> zero broker sends ──
@pytest.mark.asyncio
async def test_malformed_stored_payload_zero_broker_sends(db_session: AsyncSession):
    """Malformed stored outbox payload fails closed before any broker dispatch."""
    bundle = await create_sent_canary_fixture(db_session)
    bundle["outbox"].celery_args = {"args": "malformed_string_not_list"}
    await db_session.commit()

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        with pytest.raises(ValueError, match="Malformed Celery payload 'args'"):
            await OutboxRelayService.redrive_sent_item(
                db_session,
                outbox_id=bundle["outbox"].id,
                actor="operator_alice",
                reason="malformed payload test",
                idempotency_key="op-key-019",
            )

        mock_send.assert_not_called()


# ── Test 20: No provider/network adapter invoked ──
@pytest.mark.asyncio
async def test_no_provider_or_network_adapter_invoked(db_session: AsyncSession):
    """Redrive strictly dispatches Celery broker task without touching provider adapters."""
    bundle = await create_sent_canary_fixture(db_session)

    with (
        patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send,
        patch("omega.application.publisher.adapters.youtube.YouTubeDataApiAdapter.initialize_resumable_upload") as mock_upload,
    ):
        result = await OutboxRelayService.redrive_sent_item(
            db_session,
            outbox_id=bundle["outbox"].id,
            actor="operator_alice",
            reason="network isolation test",
            idempotency_key="op-key-020",
        )

        assert result["redriven"] is True
        mock_send.assert_called_once()
        mock_upload.assert_not_called()
