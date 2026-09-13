"""Transactional outbox relay service for OMEGA-010.

Guarantees crash-safe, idempotent dispatch to Celery broker with strict
separation between database transactions and broker network I/O.
Never holds PostgreSQL row locks while publishing to broker.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_OID, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.publisher import PublishIntentState
from omega.domain.scheduler import DispatchOutboxStatus, ReservationState
from omega.infrastructure.celery_app import celery_app
from omega.infrastructure.models import (
    PublishAttempt,
    PublishIntent,
    SchedulerDispatchOutbox,
    ScheduleReservation,
    ScheduleStateTransition,
    Task,
    UploadSession,
)
from omega.logging import get_logger

logger = get_logger(service="omega-scheduler-outbox")


def _sanitize_error(exc: Exception) -> str:
    """Sanitize error message to prevent secret leaking."""
    raw = str(exc)
    for sensitive in ["password", "secret", "token", "key", "authorization"]:
        if sensitive in raw.lower():
            return f"{type(exc).__name__}: Error sanitized to prevent credential exposure"
    return f"{type(exc).__name__}: {raw[:500]}"


def decode_celery_payload(raw_args: Any) -> tuple[list[Any], dict[str, Any]]:
    """Decode and validate Celery payload envelope into (args, kwargs).

    Expected canonical envelope:
        {"args": [...], "kwargs": {...}}
    where:
        - "args" is optional (defaults to []) and must be a list or tuple.
        - "kwargs" is optional (defaults to {}) and must be a dict.

    Raises ValueError if raw_args or its components are malformed.
    """
    if not isinstance(raw_args, dict):
        raise ValueError(
            f"Malformed Celery payload: expected dict envelope, got {type(raw_args).__name__}"
        )

    # Canonical envelope check:
    # If "args" or "kwargs" key is present, parse strictly according to canonical envelope.
    if "args" in raw_args or "kwargs" in raw_args:
        args_val = raw_args.get("args", [])
        kwargs_val = raw_args.get("kwargs", {})

        if not isinstance(args_val, (list, tuple)):
            raise ValueError(
                f"Malformed Celery payload 'args': expected list or tuple, got {type(args_val).__name__}"
            )
        if not isinstance(kwargs_val, dict):
            raise ValueError(
                f"Malformed Celery payload 'kwargs': expected dict, got {type(kwargs_val).__name__}"
            )

        extra_keys = set(raw_args.keys()) - {"args", "kwargs"}
        if extra_keys:
            raise ValueError(
                f"Malformed Celery payload: unexpected envelope keys {sorted(extra_keys)}"
            )

        return list(args_val), dict(kwargs_val)

    # Empty dictionary envelope
    if not raw_args:
        return [], {}

    # Backward compatibility for legacy plain kwargs dictionaries without "args"/"kwargs" envelope
    if all(isinstance(k, str) for k in raw_args):
        return [], dict(raw_args)

    raise ValueError(
        "Malformed Celery payload: non-empty dict without 'args' or 'kwargs' contains non-string keys"
    )


_decode_celery_payload = decode_celery_payload


class OutboxRelayService:
    """Processes pending/retry outbox items without holding DB transactions during broker I/O."""

    @staticmethod
    async def process_outbox_batch(
        session: AsyncSession,
        *,
        batch_size: int = 50,
        now: datetime | None = None,
        outbox_ids: Collection[UUID] | None = None,
    ) -> dict[str, int]:
        """Claim, publish, and acknowledge an outbox batch with decoupled transaction boundaries."""
        if outbox_ids is not None and not outbox_ids:
            return {"claimed": 0, "sent": 0, "retried": 0, "dead_letter": 0}

        if now is None:
            now = datetime.now(UTC)

        # ── Step 1: TX-CLAIM ──
        # Claim eligible rows under row lock and increment attempt count
        stmt = (
            select(SchedulerDispatchOutbox)
            .where(
                SchedulerDispatchOutbox.status.in_(
                    [
                        DispatchOutboxStatus.PENDING.value,
                        DispatchOutboxStatus.RETRY.value,
                    ]
                ),
                SchedulerDispatchOutbox.scheduled_send_at <= now,
                (
                    SchedulerDispatchOutbox.next_retry_at.is_(None)
                    | (SchedulerDispatchOutbox.next_retry_at <= now)
                ),
            )
            .order_by(SchedulerDispatchOutbox.scheduled_send_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        if outbox_ids is not None:
            stmt = stmt.where(SchedulerDispatchOutbox.id.in_(tuple(outbox_ids)))

        res = await session.execute(stmt)
        claimed_items = list(res.scalars().all())

        if not claimed_items:
            return {"claimed": 0, "sent": 0, "retried": 0, "dead_letter": 0}

        # Snapshot payload data for broker I/O outside transaction
        item_snapshots: list[dict[str, Any]] = []
        for item in claimed_items:
            item.attempt_count += 1
            item_snapshots.append(
                {
                    "id": item.id,
                    "celery_task_name": item.celery_task_name,
                    "celery_args": item.celery_args,
                    "attempt_count": item.attempt_count,
                    "max_attempts": item.max_attempts,
                    "idempotency_key": item.idempotency_key,
                }
            )

        # Commit TX-CLAIM immediately to release row locks before network I/O
        await session.commit()

        # ── Step 2: BROKER I/O OUTSIDE DB TRANSACTION ──
        results: list[tuple[UUID, bool, str | None]] = []
        for snap in item_snapshots:
            item_id = snap["id"]
            task_name = snap["celery_task_name"]
            raw_args = snap["celery_args"]

            try:
                # Format arguments via validated decoder
                args, kwargs = _decode_celery_payload(raw_args)

                # Publish to Celery
                celery_app.send_task(task_name, args=args, kwargs=kwargs)
                results.append((item_id, True, None))
                logger.info(
                    "dispatch_outbox_sent",
                    outbox_id=str(item_id),
                    task_name=task_name,
                    attempt=snap["attempt_count"],
                )
            except Exception as exc:
                err = _sanitize_error(exc)
                results.append((item_id, False, err))
                logger.error(
                    "dispatch_outbox_failed",
                    outbox_id=str(item_id),
                    task_name=task_name,
                    error=err,
                    attempt=snap["attempt_count"],
                )

        # ── Step 3: TX-ACK ──
        # Short acknowledgment transaction to persist sent/retry/dead_letter state
        sent_count = 0
        retry_count = 0
        dead_letter_count = 0
        ack_now = datetime.now(UTC)

        for item_id, success, error_msg in results:
            item_res = await session.execute(
                select(SchedulerDispatchOutbox)
                .where(SchedulerDispatchOutbox.id == item_id)
                .with_for_update()
            )
            item = item_res.scalar_one_or_none()
            if not item:
                continue

            if success:
                item.status = DispatchOutboxStatus.SENT.value
                item.sent_at = ack_now
                sent_count += 1
            else:
                item.last_error = error_msg
                if item.attempt_count >= item.max_attempts:
                    item.status = DispatchOutboxStatus.DEAD_LETTER.value
                    dead_letter_count += 1
                    logger.error(
                        "dispatch_outbox_dead_letter",
                        outbox_id=str(item.id),
                        attempt=item.attempt_count,
                        error=error_msg,
                    )
                else:
                    item.status = DispatchOutboxStatus.RETRY.value
                    backoff_seconds = min(300, (2**item.attempt_count) * 5)
                    item.next_retry_at = ack_now + timedelta(seconds=backoff_seconds)
                    retry_count += 1
                    logger.warning(
                        "dispatch_outbox_retry",
                        outbox_id=str(item.id),
                        attempt=item.attempt_count,
                        next_retry_at=item.next_retry_at.isoformat(),
                        error=error_msg,
                    )

        await session.commit()

        return {
            "claimed": len(claimed_items),
            "sent": sent_count,
            "retried": retry_count,
            "dead_letter": dead_letter_count,
        }

    @classmethod
    async def redrive_sent_item(
        cls,
        session: AsyncSession,
        *,
        outbox_id: UUID,
        actor: str,
        reason: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Manually redrive a SENT publisher outbox where broker accepted but execution never started.

        Strict safety contract:
        - explicit operator actor and reason required
        - explicit caller idempotency_key required
        - outbox exists and status == SENT
        - reservation exists and state == DISPATCHING
        - task exists and state == QUEUED
        - celery_task_name == omega.publisher.execute_publish
        - associated PublishIntent exists and state == APPROVED
        - no PublishAttempt exists for intent
        - no UploadSession exists for intent
        - no provider_video_id evidence exists for intent
        - at most one broker send guaranteed via append-only ScheduleStateTransition idempotency fence
        - DB locks released before broker network I/O
        - reuses canonical _decode_celery_payload
        """
        if not actor or not actor.strip():
            raise ValueError("actor is required for manual outbox redrive")
        if not reason or not reason.strip():
            raise ValueError("reason is required for manual outbox redrive")
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key is required for manual outbox redrive")

        clean_actor = actor.strip()
        clean_reason = reason.strip()
        clean_idempotency_key = idempotency_key.strip()

        if now is None:
            now = datetime.now(UTC)

        # ── Step 1: TX-VALIDATE-AND-CLAIM-AUDIT ──
        # Validate all precondition boundaries under row lock
        outbox_res = await session.execute(
            select(SchedulerDispatchOutbox)
            .where(SchedulerDispatchOutbox.id == outbox_id)
            .with_for_update()
        )
        outbox = outbox_res.scalar_one_or_none()
        if not outbox:
            raise ValueError(f"SchedulerDispatchOutbox {outbox_id} not found")

        if outbox.status != DispatchOutboxStatus.SENT.value:
            raise ValueError(
                f"SchedulerDispatchOutbox {outbox_id} status is '{outbox.status}', "
                f"expected '{DispatchOutboxStatus.SENT.value}' for manual redrive"
            )

        if outbox.celery_task_name != "omega.publisher.execute_publish":
            raise ValueError(
                f"SchedulerDispatchOutbox {outbox_id} task name is '{outbox.celery_task_name}', "
                "only 'omega.publisher.execute_publish' can be manually redriven"
            )

        res_res = await session.execute(
            select(ScheduleReservation)
            .where(ScheduleReservation.id == outbox.reservation_id)
            .with_for_update()
        )
        reservation = res_res.scalar_one_or_none()
        if not reservation:
            raise ValueError(f"ScheduleReservation {outbox.reservation_id} not found")

        if reservation.state != ReservationState.DISPATCHING.value:
            raise ValueError(
                f"ScheduleReservation {reservation.id} state is '{reservation.state}', "
                f"expected '{ReservationState.DISPATCHING.value}'"
            )

        task_res = await session.execute(select(Task).where(Task.id == outbox.task_id))
        task = task_res.scalar_one_or_none()
        if not task:
            raise ValueError(f"Task {outbox.task_id} not found")

        if task.state != "QUEUED":
            raise ValueError(f"Task {task.id} state is '{task.state}', expected 'QUEUED'")

        # Resolve associated PublishIntent
        intent_res = await session.execute(
            select(PublishIntent).where(PublishIntent.task_id == outbox.task_id)
        )
        intent = intent_res.scalar_one_or_none()
        if not intent:
            intent_res = await session.execute(
                select(PublishIntent).where(PublishIntent.id == reservation.target_id)
            )
            intent = intent_res.scalar_one_or_none()

        if not intent:
            raise ValueError(
                f"PublishIntent not found for task {outbox.task_id} or reservation {reservation.id}"
            )

        if intent.state != PublishIntentState.APPROVED.value:
            raise ValueError(
                f"PublishIntent {intent.id} state is '{intent.state}', "
                f"expected '{PublishIntentState.APPROVED.value}'"
            )

        # Provider evidence checks: must be 0 attempts, 0 upload sessions, 0 provider videos
        attempt_count = (
            await session.scalar(
                select(func.count(PublishAttempt.id)).where(
                    PublishAttempt.publish_intent_id == intent.id
                )
            )
            or 0
        )
        if attempt_count > 0:
            raise ValueError(
                f"Redrive blocked: {attempt_count} PublishAttempt(s) already exist for intent {intent.id}"
            )

        upload_session_count = (
            await session.scalar(
                select(func.count(UploadSession.id))
                .join(PublishAttempt, UploadSession.publish_attempt_id == PublishAttempt.id)
                .where(PublishAttempt.publish_intent_id == intent.id)
            )
            or 0
        )
        if upload_session_count > 0:
            raise ValueError(
                f"Redrive blocked: {upload_session_count} UploadSession(s) already exist for intent {intent.id}"
            )

        provider_video_count = (
            await session.scalar(
                select(func.count(PublishAttempt.id)).where(
                    PublishAttempt.publish_intent_id == intent.id,
                    PublishAttempt.provider_video_id.is_not(None),
                )
            )
            or 0
        )
        if provider_video_count > 0:
            raise ValueError(
                f"Redrive blocked: provider_video_id evidence already exists for intent {intent.id}"
            )

        # Idempotency fence check via ScheduleStateTransition
        transition_id = uuid5(
            NAMESPACE_OID, f"outbox-redrive:{outbox.id}:{clean_idempotency_key}"
        )
        existing_trans = await session.execute(
            select(ScheduleStateTransition).where(ScheduleStateTransition.id == transition_id)
        )
        if existing_trans.scalar_one_or_none() is not None:
            raise ValueError(
                f"Redrive with idempotency key '{clean_idempotency_key}' already executed for outbox {outbox.id}"
            )

        # Decode payload using canonical decoder fixed in P17-E1 (fails closed if malformed)
        args, kwargs = _decode_celery_payload(outbox.celery_args)

        # Append audit transition (DISPATCHING -> DISPATCHING)
        semantic_reason = (
            f"MANUAL_BROKER_REDRIVE_AFTER_PREEXECUTION_FAILURE: "
            f"outbox_id={outbox.id} reason={clean_reason} idempotency_key={clean_idempotency_key}"
        )
        transition = ScheduleStateTransition(
            id=transition_id,
            reservation_id=reservation.id,
            from_state=ReservationState.DISPATCHING.value,
            to_state=ReservationState.DISPATCHING.value,
            reason=semantic_reason,
            actor=clean_actor,
            created_at=now,
        )
        session.add(transition)

        # Increment outbox attempt count
        outbox.attempt_count += 1

        # Snapshot parameters for broker send outside DB transaction
        task_name = outbox.celery_task_name
        outbox_snapshot_id = outbox.id
        attempt_count_val = outbox.attempt_count

        # Commit TX-VALIDATE-AND-CLAIM-AUDIT to release all row locks before broker network I/O
        await session.commit()

        # ── Step 2: BROKER I/O OUTSIDE DB TRANSACTION ──
        send_error: str | None = None
        try:
            celery_app.send_task(task_name, args=args, kwargs=kwargs)
            logger.info(
                "dispatch_outbox_redrive_sent",
                outbox_id=str(outbox_snapshot_id),
                task_name=task_name,
                attempt=attempt_count_val,
                actor=clean_actor,
            )
        except Exception as exc:
            send_error = _sanitize_error(exc)
            logger.error(
                "dispatch_outbox_redrive_failed",
                outbox_id=str(outbox_snapshot_id),
                task_name=task_name,
                error=send_error,
                attempt=attempt_count_val,
            )

        # ── Step 3: TX-ACK ──
        if send_error is not None:
            ack_res = await session.execute(
                select(SchedulerDispatchOutbox)
                .where(SchedulerDispatchOutbox.id == outbox_snapshot_id)
                .with_for_update()
            )
            item = ack_res.scalar_one_or_none()
            if item:
                item.last_error = send_error
            await session.commit()
            raise RuntimeError(f"Broker send failed during redrive: {send_error}")

        return {
            "outbox_id": outbox_snapshot_id,
            "status": DispatchOutboxStatus.SENT.value,
            "redriven": True,
            "attempt_count": attempt_count_val,
            "celery_task_name": task_name,
            "args": args,
            "kwargs": kwargs,
            "transition_id": transition_id,
        }
