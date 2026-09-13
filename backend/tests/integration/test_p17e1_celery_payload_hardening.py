"""Integration and contract tests for P17-E1 Celery outbox payload envelope hardening.

Validates that:
1. Exact production bug regression: {"args": ["<task_id>"]} dispatches args=["<task_id>"], kwargs={}
   and explicitly asserts kwargs != {"args": ["<task_id>"]}.
2. Task signature contract test: broker envelope binds cleanly to execute_publish_task(task_id: str),
   while old buggy envelope fails with TypeError.
3. Decoder unit tests cover all canonical and edge cases:
   - Case A: {"args": ["id"]} -> args ["id"], kwargs {}
   - Case B: {"kwargs": {"foo": "bar"}} -> args [], kwargs {"foo": "bar"}
   - Case C: {"args": ["id"], "kwargs": {"foo": "bar"}} -> both preserved
   - Case D: {"args": []} -> args [], kwargs {}
   - Case E: malformed args type raises ValueError
   - Case F: malformed kwargs type raises ValueError
   - Legacy plain kwargs dict without args/kwargs is backward-compatible
   - Non-dict payload raises ValueError
   - Extra unexpected top-level envelope keys raise ValueError
4. Safe relay error handling on malformed args: no broker dispatch, row marked RETRY.
5. Safe relay error handling on malformed kwargs: no broker dispatch, row marked RETRY.
6. Safe relay dead-letter on malformed payload after max attempts reached.
7. Scoped outbox relay sends only specified outbox ID with decoded envelope.
8. Broad outbox relay sends all eligible rows with decoded envelopes.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.publisher.calendar_service import PublishCalendarService
from omega.application.scheduler.outbox_relay import OutboxRelayService, decode_celery_payload
from omega.domain.publisher import PublishIntentState
from omega.domain.scheduler import DispatchOutboxStatus
from omega.infrastructure.models import SchedulerDispatchOutbox
from omega.worker.tasks import execute_publish_task
from tests.integration.test_p17a_publish_scheduler import create_fixture_bundle


async def create_outbox_fixture_bundle(db_session: AsyncSession) -> dict:
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
    bundle["decision"] = dec
    bundle["reservation"] = res
    return bundle


# ── 1. Decoder Unit Tests ──


def test_decode_canonical_args_only():
    """Case A: {"args": ["id"]} -> args ["id"], kwargs {}."""
    args, kwargs = decode_celery_payload({"args": ["some-task-id"]})
    assert args == ["some-task-id"]
    assert kwargs == {}
    assert kwargs != {"args": ["some-task-id"]}


def test_decode_canonical_kwargs_only():
    """Case B: {"kwargs": {"foo": "bar"}} -> args [], kwargs {"foo": "bar"}."""
    args, kwargs = decode_celery_payload({"kwargs": {"foo": "bar"}})
    assert args == []
    assert kwargs == {"foo": "bar"}


def test_decode_canonical_both_args_and_kwargs():
    """Case C: {"args": ["id"], "kwargs": {"foo": "bar"}} -> both preserved."""
    args, kwargs = decode_celery_payload({"args": ["task-123"], "kwargs": {"retry": True}})
    assert args == ["task-123"]
    assert kwargs == {"retry": True}


def test_decode_canonical_empty_args():
    """Case D: {"args": []} -> args [], kwargs {}."""
    args, kwargs = decode_celery_payload({"args": []})
    assert args == []
    assert kwargs == {}


def test_decode_empty_envelope():
    """Empty dict payload yields args [], kwargs {}."""
    args, kwargs = decode_celery_payload({})
    assert args == []
    assert kwargs == {}


def test_decode_legacy_plain_kwargs():
    """Legacy plain kwargs dictionary without 'args'/'kwargs' is decoded as kwargs."""
    args, kwargs = decode_celery_payload({"channel_id": "chan-1", "mode": "sync"})
    assert args == []
    assert kwargs == {"channel_id": "chan-1", "mode": "sync"}


def test_decode_malformed_args_type():
    """Case E: malformed args type raises ValueError."""
    with pytest.raises(ValueError, match="Malformed Celery payload 'args'"):
        decode_celery_payload({"args": "not-a-list"})

    with pytest.raises(ValueError, match="Malformed Celery payload 'args'"):
        decode_celery_payload({"args": 123})


def test_decode_malformed_kwargs_type():
    """Case F: malformed kwargs type raises ValueError."""
    with pytest.raises(ValueError, match="Malformed Celery payload 'kwargs'"):
        decode_celery_payload({"kwargs": "not-a-dict"})

    with pytest.raises(ValueError, match="Malformed Celery payload 'kwargs'"):
        decode_celery_payload({"kwargs": ["list", "instead", "of", "dict"]})


def test_decode_malformed_non_dict_payload():
    """Non-dict payload raises ValueError."""
    with pytest.raises(ValueError, match="expected dict envelope"):
        decode_celery_payload(["not", "a", "dict"])

    with pytest.raises(ValueError, match="expected dict envelope"):
        decode_celery_payload("string-payload")

    with pytest.raises(ValueError, match="expected dict envelope"):
        decode_celery_payload(None)


def test_decode_malformed_extra_envelope_keys():
    """Unrecognized extra keys alongside canonical envelope raise ValueError."""
    with pytest.raises(ValueError, match="unexpected envelope keys"):
        decode_celery_payload({"args": ["id"], "unexpected": 42})


# ── 2. Task Signature Contract Test ──


def test_publisher_task_signature_contract():
    """Execute_publish_task(task_id: str) binds cleanly with new envelope and rejects old bug."""
    task_id = "82d49ecc-c723-469b-8cf6-659458f64780"
    raw_args = {"args": [task_id]}

    # New fixed decoder:
    new_args, new_kwargs = decode_celery_payload(raw_args)
    assert new_args == [task_id]
    assert new_kwargs == {}

    # Inspect signature of execute_publish_task
    sig = inspect.signature(execute_publish_task)

    # Fixed envelope binds cleanly
    bound = sig.bind(*new_args, **new_kwargs)
    assert bound.arguments == {"task_id": task_id}

    # Old buggy behavior: kwargs = {"args": [task_id]}
    old_args = [task_id]
    old_kwargs = {"args": [task_id]}
    with pytest.raises(TypeError, match="unexpected keyword argument 'args'"):
        sig.bind(*old_args, **old_kwargs)


# ── 3. Exact Production Bug Regression in Relay ──


@pytest.mark.asyncio
async def test_exact_production_bug_regression_in_relay(db_session: AsyncSession):
    """P17-E canary payload {"args": ["<task_id>"]} sends args=["<task_id>"], kwargs={}."""
    bundle = await create_outbox_fixture_bundle(db_session)
    task_id = str(bundle["task"].id)
    now = datetime.now(UTC)

    outbox_item = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=bundle["reservation"].id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        celery_task_name="omega.publisher.execute_publish",
        celery_args={"args": [task_id]},
        idempotency_key=f"outbox:{uuid4()}",
        status=DispatchOutboxStatus.PENDING.value,
        attempt_count=0,
        max_attempts=5,
        scheduled_send_at=now,
        created_at=now,
    )
    db_session.add(outbox_item)
    await db_session.commit()

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        counts = await OutboxRelayService.process_outbox_batch(
            db_session, outbox_ids=[outbox_item.id], now=now
        )

        assert counts == {"claimed": 1, "sent": 1, "retried": 0, "dead_letter": 0}
        mock_send.assert_called_once_with(
            "omega.publisher.execute_publish",
            args=[task_id],
            kwargs={},
        )
        call_kwargs = mock_send.call_args.kwargs.get("kwargs")
        assert call_kwargs == {}
        assert call_kwargs != {"args": [task_id]}


# ── 4. Malformed Payload Safe Error Handling (No Broker Call) ──


@pytest.mark.asyncio
async def test_relay_malformed_args_fails_safely_to_retry(db_session: AsyncSession):
    """Outbox row with malformed args is NOT dispatched to broker and transitions to RETRY."""
    bundle = await create_outbox_fixture_bundle(db_session)
    now = datetime.now(UTC)

    outbox_item = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=bundle["reservation"].id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        celery_task_name="omega.publisher.execute_publish",
        celery_args={"args": "not-a-list"},
        idempotency_key=f"outbox:{uuid4()}",
        status=DispatchOutboxStatus.PENDING.value,
        attempt_count=0,
        max_attempts=5,
        scheduled_send_at=now,
        created_at=now,
    )
    db_session.add(outbox_item)
    await db_session.commit()

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        counts = await OutboxRelayService.process_outbox_batch(
            db_session, outbox_ids=[outbox_item.id], now=now
        )

        assert counts == {"claimed": 1, "sent": 0, "retried": 1, "dead_letter": 0}
        mock_send.assert_not_called()

    # Verify DB state
    res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.id == outbox_item.id)
    )
    updated = res.scalar_one()
    assert updated.status == DispatchOutboxStatus.RETRY.value
    assert updated.attempt_count == 1
    assert "Malformed Celery payload 'args'" in (updated.last_error or "")
    assert updated.next_retry_at is not None


@pytest.mark.asyncio
async def test_relay_malformed_kwargs_fails_safely_to_retry(db_session: AsyncSession):
    """Outbox row with malformed kwargs is NOT dispatched to broker and transitions to RETRY."""
    bundle = await create_outbox_fixture_bundle(db_session)
    now = datetime.now(UTC)

    outbox_item = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=bundle["reservation"].id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        celery_task_name="omega.publisher.execute_publish",
        celery_args={"kwargs": "not-a-dict"},
        idempotency_key=f"outbox:{uuid4()}",
        status=DispatchOutboxStatus.PENDING.value,
        attempt_count=0,
        max_attempts=5,
        scheduled_send_at=now,
        created_at=now,
    )
    db_session.add(outbox_item)
    await db_session.commit()

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        counts = await OutboxRelayService.process_outbox_batch(
            db_session, outbox_ids=[outbox_item.id], now=now
        )

        assert counts == {"claimed": 1, "sent": 0, "retried": 1, "dead_letter": 0}
        mock_send.assert_not_called()

    res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.id == outbox_item.id)
    )
    updated = res.scalar_one()
    assert updated.status == DispatchOutboxStatus.RETRY.value
    assert updated.attempt_count == 1
    assert "Malformed Celery payload 'kwargs'" in (updated.last_error or "")


@pytest.mark.asyncio
async def test_relay_malformed_payload_dead_letters_at_max_attempts(db_session: AsyncSession):
    """Outbox row with malformed payload transitions to DEAD_LETTER when max_attempts reached."""
    bundle = await create_outbox_fixture_bundle(db_session)
    now = datetime.now(UTC)

    outbox_item = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=bundle["reservation"].id,
        task_id=bundle["task"].id,
        mission_id=bundle["mission"].id,
        celery_task_name="omega.publisher.execute_publish",
        celery_args={"args": 999},
        idempotency_key=f"outbox:{uuid4()}",
        status=DispatchOutboxStatus.PENDING.value,
        attempt_count=4,
        max_attempts=5,
        scheduled_send_at=now,
        created_at=now,
    )
    db_session.add(outbox_item)
    await db_session.commit()

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        counts = await OutboxRelayService.process_outbox_batch(
            db_session, outbox_ids=[outbox_item.id], now=now
        )

        assert counts == {"claimed": 1, "sent": 0, "retried": 0, "dead_letter": 1}
        mock_send.assert_not_called()

    res = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.id == outbox_item.id)
    )
    updated = res.scalar_one()
    assert updated.status == DispatchOutboxStatus.DEAD_LETTER.value
    assert updated.attempt_count == 5
    assert "Malformed Celery payload 'args'" in (updated.last_error or "")


# ── 5. Scoped and Broad Relay Invariants ──


@pytest.mark.asyncio
async def test_scoped_relay_sends_only_allowed_outbox_with_fixed_envelope(db_session: AsyncSession):
    """Case G: Scoped relay sends only explicitly allowed outbox item with corrected envelope."""
    b1 = await create_outbox_fixture_bundle(db_session)
    b2 = await create_outbox_fixture_bundle(db_session)
    now = datetime.now(UTC)

    t1_id = str(b1["task"].id)
    t2_id = str(b2["task"].id)

    item1 = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=b1["reservation"].id,
        task_id=b1["task"].id,
        mission_id=b1["mission"].id,
        celery_task_name="omega.publisher.execute_publish",
        celery_args={"args": [t1_id]},
        idempotency_key=f"outbox:{uuid4()}",
        status=DispatchOutboxStatus.PENDING.value,
        attempt_count=0,
        max_attempts=5,
        scheduled_send_at=now,
        created_at=now,
    )
    item2 = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=b2["reservation"].id,
        task_id=b2["task"].id,
        mission_id=b2["mission"].id,
        celery_task_name="omega.publisher.execute_publish",
        celery_args={"args": [t2_id]},
        idempotency_key=f"outbox:{uuid4()}",
        status=DispatchOutboxStatus.PENDING.value,
        attempt_count=0,
        max_attempts=5,
        scheduled_send_at=now,
        created_at=now,
    )
    db_session.add_all([item1, item2])
    await db_session.commit()

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        counts = await OutboxRelayService.process_outbox_batch(
            db_session, outbox_ids=[item1.id], now=now
        )

        assert counts == {"claimed": 1, "sent": 1, "retried": 0, "dead_letter": 0}
        mock_send.assert_called_once_with(
            "omega.publisher.execute_publish",
            args=[t1_id],
            kwargs={},
        )

    # Item 1 is SENT
    r1 = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.id == item1.id)
    )
    assert r1.scalar_one().status == DispatchOutboxStatus.SENT.value

    # Item 2 remains PENDING
    r2 = await db_session.execute(
        select(SchedulerDispatchOutbox).where(SchedulerDispatchOutbox.id == item2.id)
    )
    assert r2.scalar_one().status == DispatchOutboxStatus.PENDING.value


@pytest.mark.asyncio
async def test_broad_relay_behavior_unchanged_with_corrected_decoding(db_session: AsyncSession):
    """Case H: Broad relay claims and sends all eligible outbox items with corrected envelopes."""
    b1 = await create_outbox_fixture_bundle(db_session)
    b2 = await create_outbox_fixture_bundle(db_session)
    now = datetime.now(UTC)

    t1_id = str(b1["task"].id)
    t2_id = str(b2["task"].id)

    item1 = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=b1["reservation"].id,
        task_id=b1["task"].id,
        mission_id=b1["mission"].id,
        celery_task_name="omega.publisher.execute_publish",
        celery_args={"args": [t1_id]},
        idempotency_key=f"outbox:{uuid4()}",
        status=DispatchOutboxStatus.PENDING.value,
        attempt_count=0,
        max_attempts=5,
        scheduled_send_at=now,
        created_at=now,
    )
    item2 = SchedulerDispatchOutbox(
        id=uuid4(),
        reservation_id=b2["reservation"].id,
        task_id=b2["task"].id,
        mission_id=b2["mission"].id,
        celery_task_name="omega.publisher.execute_publish",
        celery_args={"args": [t2_id], "kwargs": {"priority": "high"}},
        idempotency_key=f"outbox:{uuid4()}",
        status=DispatchOutboxStatus.PENDING.value,
        attempt_count=0,
        max_attempts=5,
        scheduled_send_at=now,
        created_at=now,
    )
    db_session.add_all([item1, item2])
    await db_session.commit()

    with patch("omega.application.scheduler.outbox_relay.celery_app.send_task") as mock_send:
        counts = await OutboxRelayService.process_outbox_batch(
            db_session, outbox_ids=None, now=now
        )

        assert counts["claimed"] >= 2
        assert counts["sent"] >= 2
        assert counts["retried"] == 0
        assert counts["dead_letter"] == 0

        # Verify item1 dispatch
        mock_send.assert_any_call(
            "omega.publisher.execute_publish",
            args=[t1_id],
            kwargs={},
        )
        # Verify item2 dispatch
        mock_send.assert_any_call(
            "omega.publisher.execute_publish",
            args=[t2_id],
            kwargs={"priority": "high"},
        )
