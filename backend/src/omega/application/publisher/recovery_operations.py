"""Operator-facing publisher recovery queries and tightly fenced controls.

The service reuses the publisher handoff outbox for retries, reconciliation
status for holds, and attempt transitions for append-only operator audit.
It intentionally exposes no operation that creates a replacement upload session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.error_sanitizer import sanitize_sensitive_text
from omega.application.publisher.handoff_relay import MAX_HANDOFF_ATTEMPTS
from omega.application.publisher.reconciliation_service import ReconciliationService
from omega.domain.publisher import (
    HandoffStatus,
    PublishAttemptState,
    PublishIntentState,
    ReconciliationStatus,
    compute_handoff_idempotency_key,
)
from omega.infrastructure.models import (
    PublishAttempt,
    PublishAttemptTransition,
    PublisherSchedulerHandoffOutbox,
    PublishIntent,
    UploadSession,
)


class RecoveryOperationRejected(ValueError):
    """Raised when an operator action cannot prove that recovery is safe."""


def _require_operator_context(actor: str, reason: str) -> tuple[str, str]:
    safe_actor = sanitize_sensitive_text(actor, limit=64).strip()
    safe_reason = sanitize_sensitive_text(reason).strip()
    if not safe_actor or not safe_reason:
        raise RecoveryOperationRejected("explicit actor and reason are required")
    return safe_actor, safe_reason


class PublisherRecoveryOperationsService:
    """Read recovery state and perform only evidence-preserving recovery actions."""

    @staticmethod
    async def create_retry_handoff(
        session: AsyncSession,
        *,
        intent: PublishIntent,
        attempt: PublishAttempt,
        task_id: UUID,
        earliest_retry_at: datetime,
        reason: str,
    ) -> PublisherSchedulerHandoffOutbox:
        """Idempotently create the one handoff for an attempt retry generation."""
        key = compute_handoff_idempotency_key(
            publish_intent_id=intent.id,
            publish_attempt_id=attempt.id,
            retry_generation=intent.attempt_generation,
            earliest_retry_at=earliest_retry_at,
        )
        existing = (
            await session.execute(
                select(PublisherSchedulerHandoffOutbox).where(
                    PublisherSchedulerHandoffOutbox.idempotency_key == key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        row = PublisherSchedulerHandoffOutbox(
            id=uuid4(),
            publish_intent_id=intent.id,
            publish_attempt_id=attempt.id,
            task_id=task_id,
            mission_id=intent.mission_id,
            earliest_retry_at=earliest_retry_at,
            reason=sanitize_sensitive_text(reason),
            idempotency_key=key,
            status=HandoffStatus.PENDING.value,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            return (
                await session.execute(
                    select(PublisherSchedulerHandoffOutbox).where(
                        PublisherSchedulerHandoffOutbox.idempotency_key == key
                    )
                )
            ).scalar_one()
        return row

    @staticmethod
    def _attempt_view(attempt: PublishAttempt) -> dict[str, Any]:
        return {
            "attempt_id": attempt.id,
            "publish_intent_id": attempt.publish_intent_id,
            "state": attempt.state,
            "error_category": attempt.error_category,
            "reconciliation_status": attempt.reconciliation_status,
            "last_sanitized_error": (
                sanitize_sensitive_text(attempt.error_message) if attempt.error_message else None
            ),
            "retry_after_seconds": attempt.retry_after_seconds,
        }

    @classmethod
    async def get_manual_holds(
        cls, session: AsyncSession, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(PublishAttempt)
                .where(
                    PublishAttempt.reconciliation_status == ReconciliationStatus.MANUAL_HOLD.value
                )
                .order_by(PublishAttempt.started_at.asc())
                .limit(limit)
            )
        ).scalars()
        return [cls._attempt_view(row) for row in rows]

    @staticmethod
    async def get_dead_letters(
        session: AsyncSession, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(PublisherSchedulerHandoffOutbox)
                .where(PublisherSchedulerHandoffOutbox.status == HandoffStatus.DEAD_LETTER.value)
                .order_by(PublisherSchedulerHandoffOutbox.created_at.asc())
                .limit(limit)
            )
        ).scalars()
        return [
            {
                "handoff_id": row.id,
                "publish_intent_id": row.publish_intent_id,
                "publish_attempt_id": row.publish_attempt_id,
                "status": row.status,
                "attempt_count": row.attempt_count,
                "max_attempts": MAX_HANDOFF_ATTEMPTS,
                "next_retry_time": row.next_attempt_at,
                "last_sanitized_error": (
                    sanitize_sensitive_text(row.last_error) if row.last_error else None
                ),
            }
            for row in rows
        ]

    @classmethod
    async def get_retryable_failures(
        cls, session: AsyncSession, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(PublishAttempt)
                .where(PublishAttempt.state == PublishAttemptState.RETRYABLE_FAILED.value)
                .order_by(PublishAttempt.started_at.asc())
                .limit(limit)
            )
        ).scalars()
        return [cls._attempt_view(row) for row in rows]

    @classmethod
    async def get_attempt_recovery_context(
        cls, session: AsyncSession, attempt_id: UUID
    ) -> dict[str, Any] | None:
        attempt = await session.get(PublishAttempt, attempt_id)
        if attempt is None:
            return None
        upload = (
            await session.execute(
                select(UploadSession).where(UploadSession.publish_attempt_id == attempt_id)
            )
        ).scalar_one_or_none()
        result = cls._attempt_view(attempt)
        result["upload_session"] = (
            {
                "session_id": upload.id,
                "bytes_uploaded": upload.bytes_uploaded,
                "total_bytes": upload.total_bytes,
                "expires_at": upload.expires_at,
            }
            if upload
            else None
        )
        return result

    @staticmethod
    async def get_recovery_observability(session: AsyncSession) -> dict[str, Any]:
        now = datetime.now(UTC)

        async def count_handoff(status: HandoffStatus) -> int:
            return int(
                (
                    await session.execute(
                        select(func.count()).select_from(PublisherSchedulerHandoffOutbox).where(
                            PublisherSchedulerHandoffOutbox.status == status.value
                        )
                    )
                ).scalar_one()
            )

        pending_count = await count_handoff(HandoffStatus.PENDING)
        oldest = (
            await session.execute(
                select(func.min(PublisherSchedulerHandoffOutbox.next_attempt_at)).where(
                    PublisherSchedulerHandoffOutbox.status == HandoffStatus.PENDING.value
                )
            )
        ).scalar_one_or_none()
        manual_holds = int(
            (
                await session.execute(
                    select(func.count()).select_from(PublishAttempt).where(
                        PublishAttempt.reconciliation_status
                        == ReconciliationStatus.MANUAL_HOLD.value
                    )
                )
            ).scalar_one()
        )
        return {
            "retry_pending_count": pending_count,
            "retry_claimed_count": await count_handoff(HandoffStatus.CLAIMED),
            "dead_letter_count": await count_handoff(HandoffStatus.DEAD_LETTER),
            "manual_hold_count": manual_holds,
            "oldest_pending_retry_age_seconds": (
                max(0.0, (now - oldest).total_seconds()) if oldest else None
            ),
            "max_attempts": MAX_HANDOFF_ATTEMPTS,
        }

    @staticmethod
    async def requeue_dead_letter(
        session: AsyncSession,
        handoff_id: UUID,
        *,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> PublisherSchedulerHandoffOutbox:
        safe_actor, safe_reason = _require_operator_context(actor, reason)
        operation_time = now or datetime.now(UTC)
        handoff = (
            await session.execute(
                select(PublisherSchedulerHandoffOutbox)
                .where(PublisherSchedulerHandoffOutbox.id == handoff_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if handoff is None or handoff.status != HandoffStatus.DEAD_LETTER.value:
            raise RecoveryOperationRejected("only DEAD_LETTER handoffs may be requeued")
        intent = (
            await session.execute(
                select(PublishIntent)
                .where(PublishIntent.id == handoff.publish_intent_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if intent is None or intent.state != PublishIntentState.APPROVED.value:
            raise RecoveryOperationRejected("associated publish intent is not retry-eligible")
        if intent.claim_token is not None or intent.lease_expires_at is not None:
            raise RecoveryOperationRejected("publish intent has an active or unresolved claim")
        unresolved_unknown = (
            await session.execute(
                select(PublishAttempt.id).where(
                    PublishAttempt.publish_intent_id == intent.id,
                    PublishAttempt.state == PublishAttemptState.UNKNOWN.value,
                    or_(
                        PublishAttempt.reconciliation_status.is_(None),
                        PublishAttempt.reconciliation_status.in_(
                            [
                                ReconciliationStatus.PENDING.value,
                                ReconciliationStatus.MANUAL_HOLD.value,
                            ]
                        ),
                    ),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if unresolved_unknown is not None:
            raise RecoveryOperationRejected("unresolved UNKNOWN attempt blocks requeue")

        attempt = await session.get(PublishAttempt, handoff.publish_attempt_id)
        if attempt is None:
            raise RecoveryOperationRejected("associated publish attempt is missing")
        prior_count = handoff.attempt_count
        prior_error = sanitize_sensitive_text(handoff.last_error) if handoff.last_error else "none"
        session.add(
            PublishAttemptTransition(
                id=uuid4(),
                publish_attempt_id=attempt.id,
                from_state=attempt.state,
                to_state=attempt.state,
                reason=(
                    f"DEAD_LETTER_REQUEUE handoff={handoff.id} prior_attempt_count={prior_count} "
                    f"delivery_generation={handoff.delivery_generation} prior_error={prior_error}; "
                    f"operator_reason={safe_reason}"
                ),
                actor=safe_actor,
            )
        )
        handoff.status = HandoffStatus.PENDING.value
        handoff.attempt_count = 0
        handoff.next_attempt_at = operation_time
        handoff.claim_token = None
        handoff.claimed_by_worker_id = None
        handoff.claimed_at = None
        handoff.lease_expires_at = None
        handoff.delivered_at = None
        handoff.last_error = None
        await session.commit()
        return handoff

    @staticmethod
    async def reconcile_again(
        session: AsyncSession,
        attempt_id: UUID,
        *,
        actor: str,
        reason: str,
    ) -> ReconciliationStatus:
        safe_actor, safe_reason = _require_operator_context(actor, reason)
        attempt = (
            await session.execute(
                select(PublishAttempt).where(PublishAttempt.id == attempt_id).with_for_update()
            )
        ).scalar_one_or_none()
        if attempt is None or attempt.reconciliation_status != ReconciliationStatus.MANUAL_HOLD.value:
            raise RecoveryOperationRejected("only MANUAL_HOLD attempts may be reconciled again")
        if attempt.state != PublishAttemptState.UNKNOWN.value:
            raise RecoveryOperationRejected("terminal session holds cannot be retried automatically")
        session.add(
            PublishAttemptTransition(
                id=uuid4(),
                publish_attempt_id=attempt.id,
                from_state=attempt.state,
                to_state=attempt.state,
                reason=f"MANUAL_HOLD_RECONCILE_AGAIN: {safe_reason}",
                actor=safe_actor,
            )
        )
        attempt.reconciliation_status = ReconciliationStatus.PENDING.value
        await session.commit()
        return await ReconciliationService.reconcile_attempt(session, attempt_id)

    @staticmethod
    async def authorize_existing_session_resume(
        session: AsyncSession,
        attempt_id: UUID,
        *,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        """Authorize only the persisted session/offset; never create a replacement session."""
        safe_actor, safe_reason = _require_operator_context(actor, reason)
        attempt = (
            await session.execute(
                select(PublishAttempt).where(PublishAttempt.id == attempt_id).with_for_update()
            )
        ).scalar_one_or_none()
        upload = (
            await session.execute(
                select(UploadSession).where(UploadSession.publish_attempt_id == attempt_id)
            )
        ).scalar_one_or_none()
        if attempt is None or upload is None:
            raise RecoveryOperationRejected("an existing UploadSession is required")
        if attempt.state != PublishAttemptState.UNKNOWN.value:
            raise RecoveryOperationRejected("only UNKNOWN attempts may resume an existing session")
        if attempt.reconciliation_status != ReconciliationStatus.MANUAL_HOLD.value:
            raise RecoveryOperationRejected("explicit MANUAL_HOLD is required before operator resume")
        if upload.expires_at <= datetime.now(UTC) or upload.bytes_uploaded >= upload.total_bytes:
            raise RecoveryOperationRejected("existing UploadSession is terminal or not resumable")
        session.add(
            PublishAttemptTransition(
                id=uuid4(),
                publish_attempt_id=attempt.id,
                from_state=attempt.state,
                to_state=attempt.state,
                reason=f"EXISTING_SESSION_RESUME_AUTHORIZED session={upload.id}: {safe_reason}",
                actor=safe_actor,
            )
        )
        await session.commit()
        return {
            "attempt_id": attempt.id,
            "upload_session_id": upload.id,
            "provider_offset": upload.bytes_uploaded,
            "total_bytes": upload.total_bytes,
        }
