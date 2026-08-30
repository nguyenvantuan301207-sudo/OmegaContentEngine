"""Transactional outbox relay service for OMEGA-010.

Guarantees crash-safe, idempotent dispatch to Celery broker with strict
separation between database transactions and broker network I/O.
Never holds PostgreSQL row locks while publishing to broker.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.scheduler import DispatchOutboxStatus
from omega.infrastructure.celery_app import celery_app
from omega.infrastructure.models import SchedulerDispatchOutbox
from omega.logging import get_logger

logger = get_logger(service="omega-scheduler-outbox")


def _sanitize_error(exc: Exception) -> str:
    """Sanitize error message to prevent secret leaking."""
    raw = str(exc)
    for sensitive in ["password", "secret", "token", "key", "authorization"]:
        if sensitive in raw.lower():
            return f"{type(exc).__name__}: Error sanitized to prevent credential exposure"
    return f"{type(exc).__name__}: {raw[:500]}"


class OutboxRelayService:
    """Processes pending/retry outbox items without holding DB transactions during broker I/O."""

    @staticmethod
    async def process_outbox_batch(
        session: AsyncSession,
        *,
        batch_size: int = 50,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Claim, publish, and acknowledge an outbox batch with decoupled transaction boundaries."""
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
                # Format arguments
                args = raw_args.get("args", []) if isinstance(raw_args, dict) else []
                kwargs = (
                    raw_args.get("kwargs", raw_args) if isinstance(raw_args, dict) else raw_args
                )

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
