"""Cross-Service Retry Handoff Relay for OMEGA-011 Publisher.

Claims pending or expired retry handoffs from publisher_scheduler_handoff_outbox
and invokes OMEGA-010 ScheduleEvaluationEngine with deterministic idempotency.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.scheduler.evaluation_engine import ScheduleEvaluationEngine
from omega.domain.publisher import HandoffStatus
from omega.domain.scheduler import (
    ScheduleRequest,
    ScheduleTargetType,
    ScheduleWorkloadCategory,
)
from omega.infrastructure.models import (
    PublisherSchedulerHandoffOutbox,
    PublishIntent,
)
from omega.logging import get_logger

logger = get_logger(service="omega-publisher-handoff-relay")

MAX_HANDOFF_ATTEMPTS = 5


class HandoffRelayService:
    """Relays failed publish attempts to OMEGA-010 Scheduler for deferred retry."""

    @classmethod
    async def process_pending_handoffs(
        cls,
        session: AsyncSession,
        batch_size: int = 20,
        worker_id: str | None = None,
    ) -> int:
        """Claim and deliver due handoff outbox rows to OMEGA-010 Scheduler."""
        now = datetime.now(UTC)
        effective_worker_id = worker_id or f"relay-{os.getpid()}-{secrets_hex()}"

        # 1. TX-CLAIM: Find and lock pending or expired-lease rows
        stmt = (
            select(PublisherSchedulerHandoffOutbox)
            .where(
                (
                    (PublisherSchedulerHandoffOutbox.status == HandoffStatus.PENDING.value)
                    | (
                        (PublisherSchedulerHandoffOutbox.status == HandoffStatus.CLAIMED.value)
                        & (PublisherSchedulerHandoffOutbox.lease_expires_at <= now)
                    )
                ),
                PublisherSchedulerHandoffOutbox.next_attempt_at <= now,
            )
            .order_by(PublisherSchedulerHandoffOutbox.next_attempt_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )

        res = await session.execute(stmt)
        rows = res.scalars().all()
        if not rows:
            return 0

        claimed_items: list[tuple[UUID, UUID, int, PublisherSchedulerHandoffOutbox]] = []
        for r in rows:
            new_token = uuid4()
            r.status = HandoffStatus.CLAIMED.value
            r.claim_token = new_token
            r.claimed_by_worker_id = effective_worker_id
            r.claimed_at = now
            r.lease_expires_at = now + timedelta(minutes=2)
            r.delivery_generation += 1
            r.attempt_count += 1
            claimed_items.append((r.id, new_token, r.delivery_generation, r))

        await session.commit()

        # 2. Process each claimed handoff outside DB transaction
        delivered_count = 0
        for handoff_id, claim_token, generation, handoff in claimed_items:
            try:
                # Load intent details
                intent_res = await session.execute(
                    select(PublishIntent).where(PublishIntent.id == handoff.publish_intent_id)
                )
                intent = intent_res.scalar_one_or_none()
                if not intent:
                    raise RuntimeError(f"PublishIntent {handoff.publish_intent_id} not found.")

                # Construct deterministic caller_key
                caller_key = f"handoff:{handoff.idempotency_key}:gen-{generation}"

                # Build ScheduleRequest for OMEGA-010
                sched_req = ScheduleRequest(
                    mission_id=handoff.mission_id,
                    task_id=handoff.task_id,
                    target_type=ScheduleTargetType.PUBLISH_INTENT,
                    target_id=handoff.publish_intent_id,
                    workload_category=ScheduleWorkloadCategory.EXTERNAL_PUBLISH,
                    channel_id=intent.channel_id,
                    earliest_start_at=handoff.earliest_retry_at,
                    caller_key=caller_key,
                )

                # Invoke ScheduleEvaluationEngine
                eval_resp = await ScheduleEvaluationEngine.evaluate_schedule(
                    session=session,
                    request=sched_req,
                    now=datetime.now(UTC),
                )

                # 3. TX-ACK: Fenced update verifying claim_token
                ack_stmt = (
                    update(PublisherSchedulerHandoffOutbox)
                    .where(
                        PublisherSchedulerHandoffOutbox.id == handoff_id,
                        PublisherSchedulerHandoffOutbox.claim_token == claim_token,
                    )
                    .values(
                        status=HandoffStatus.DELIVERED.value,
                        delivered_at=datetime.now(UTC),
                        claim_token=None,
                        lease_expires_at=None,
                        last_error=None,
                    )
                )
                ack_res = await session.execute(ack_stmt)
                await session.commit()

                if ack_res.rowcount > 0:
                    delivered_count += 1
                    logger.info(
                        "Handoff successfully delivered to OMEGA-010 Scheduler",
                        handoff_id=str(handoff_id),
                        decision_id=str(eval_resp.id),
                    )

            except Exception as exc:
                logger.error(
                    "Error delivering retry handoff to Scheduler",
                    handoff_id=str(handoff_id),
                    error=str(exc),
                )
                # Handle retry or dead letter
                await cls._handle_handoff_failure(session, handoff_id, claim_token, str(exc))

        return delivered_count

    @classmethod
    async def _handle_handoff_failure(
        cls,
        session: AsyncSession,
        handoff_id: UUID,
        claim_token: UUID,
        error_msg: str,
    ) -> None:
        """Update handoff status to backoff PENDING or DEAD_LETTER."""
        stmt = (
            select(PublisherSchedulerHandoffOutbox)
            .where(
                PublisherSchedulerHandoffOutbox.id == handoff_id,
                PublisherSchedulerHandoffOutbox.claim_token == claim_token,
            )
            .with_for_update()
        )
        res = await session.execute(stmt)
        row = res.scalar_one_or_none()
        if not row:
            return

        row.last_error = error_msg
        row.claim_token = None
        row.lease_expires_at = None

        if row.attempt_count >= MAX_HANDOFF_ATTEMPTS:
            row.status = HandoffStatus.DEAD_LETTER.value
            logger.error(
                "Handoff outbox moved to DEAD_LETTER after max attempts",
                handoff_id=str(handoff_id),
                attempts=row.attempt_count,
            )
        else:
            row.status = HandoffStatus.PENDING.value
            backoff_sec = min(300, 10 * (2 ** (row.attempt_count - 1)))
            row.next_attempt_at = datetime.now(UTC) + timedelta(seconds=backoff_sec)

        await session.commit()


def secrets_hex() -> str:
    import secrets

    return secrets.token_hex(4)
