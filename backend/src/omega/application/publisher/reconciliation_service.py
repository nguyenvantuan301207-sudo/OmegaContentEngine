"""Reconciliation Service for OMEGA-011 Publisher.

Authoritatively resolves ambiguous UNKNOWN publish attempts by querying provider
session URIs without risking duplicate uploads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.network.preflight import NetworkPreflightService
from omega.application.publisher.adapters.base import AdapterRegistry
from omega.domain.network import NetworkEgressPermit, ServiceCategory
from omega.domain.publisher import (
    PublishAttemptState,
    PublishIntentState,
    ReconciliationStatus,
)
from omega.domain.task import TaskState
from omega.infrastructure.models import (
    PublishAttempt,
    PublishAttemptTransition,
    PublishIntent,
    PublishIntentTransition,
    Task,
    UploadSession,
)
from omega.logging import get_logger

logger = get_logger(service="omega-publisher-reconciliation")


class ReconciliationService:
    """Performs provider-level reconciliation for UNKNOWN upload outcomes."""

    @classmethod
    async def reconcile_attempt(
        cls,
        session: AsyncSession,
        attempt_id: UUID,
    ) -> ReconciliationStatus:
        """Reconcile an individual UNKNOWN attempt."""
        stmt = select(PublishAttempt).where(PublishAttempt.id == attempt_id).with_for_update()
        res = await session.execute(stmt)
        attempt = res.scalar_one_or_none()
        if not attempt or attempt.state != PublishAttemptState.UNKNOWN.value:
            return ReconciliationStatus.PENDING

        # Load upload session
        sess_stmt = select(UploadSession).where(UploadSession.publish_attempt_id == attempt.id)
        sess_res = await session.execute(sess_stmt)
        upload_sess = sess_res.scalar_one_or_none()
        if not upload_sess:
            attempt.reconciliation_status = ReconciliationStatus.MANUAL_HOLD.value
            await session.commit()
            return ReconciliationStatus.MANUAL_HOLD

        # Load publish intent
        intent_stmt = (
            select(PublishIntent)
            .where(PublishIntent.id == attempt.publish_intent_id)
            .with_for_update()
        )
        intent_res = await session.execute(intent_stmt)
        intent = intent_res.scalar_one_or_none()
        if not intent:
            return ReconciliationStatus.PENDING

        # Load task
        task_stmt = select(Task).where(Task.id == intent.task_id).with_for_update()
        task_res = await session.execute(task_stmt)
        task = task_res.scalar_one_or_none()

        # Execute network preflight for session URI
        from omega.domain.network import NetworkPreflightRequest

        preflight_service = NetworkPreflightService(lambda: session)
        _, permit = await preflight_service.preflight(
            NetworkPreflightRequest(
                destination_url=upload_sess.session_uri,
                service_category=ServiceCategory.YOUTUBE_API,
                caller_key="reconcile_upload_session",
            )
        )
        if not permit:
            permit = NetworkEgressPermit(
                network_check_id=uuid4(),
                route_id=uuid4(),
                route_config_version=1,
                canonical_destination=upload_sess.session_uri,
                service_category=ServiceCategory.YOUTUBE_API,
                expires_at=datetime.now(UTC),
            )

        adapter = AdapterRegistry.get("YOUTUBE")
        recon_result = await adapter.reconcile_upload_session(
            session_uri=upload_sess.session_uri,
            total_bytes=upload_sess.total_bytes,
            permit=permit,
        )

        if recon_result.is_confirmed_success:
            # Succeeded on provider!
            await session.execute(
                update(PublishAttempt)
                .where(PublishAttempt.id == attempt.id)
                .values(
                    state=PublishAttemptState.SUCCEEDED.value,
                    provider_video_id=recon_result.provider_video_id,
                    provider_url=recon_result.provider_url,
                    reconciliation_status=ReconciliationStatus.CONFIRMED_SUCCESS.value,
                    completed_at=datetime.now(UTC),
                )
            )

            # Record attempt transition
            trans = PublishAttemptTransition(
                id=uuid4(),
                publish_attempt_id=attempt.id,
                from_state=PublishAttemptState.UNKNOWN.value,
                to_state=PublishAttemptState.SUCCEEDED.value,
                reason=f"Reconciled successfully from session URI: {recon_result.diagnostic_reason}",
                actor="RECONCILER",
            )
            session.add(trans)

            # Mark intent published
            await session.execute(
                update(PublishIntent)
                .where(PublishIntent.id == intent.id)
                .values(
                    state=PublishIntentState.PUBLISHED.value,
                    updated_at=datetime.now(UTC),
                )
            )
            intent_trans = PublishIntentTransition(
                id=uuid4(),
                publish_intent_id=intent.id,
                from_state=PublishIntentState.CLAIMED.value,
                to_state=PublishIntentState.PUBLISHED.value,
                reason="Reconciliation confirmed successful upload.",
                actor="RECONCILER",
            )
            session.add(intent_trans)

            if task:
                await session.execute(
                    update(Task)
                    .where(Task.id == task.id)
                    .values(
                        state=TaskState.SUCCEEDED.value,
                        completed_at=datetime.now(UTC),
                    )
                )

            await session.commit()
            logger.info(
                "Attempt reconciled as SUCCEEDED",
                attempt_id=str(attempt.id),
                video_id=recon_result.provider_video_id,
            )
            return ReconciliationStatus.CONFIRMED_SUCCESS

        if recon_result.is_incomplete:
            # Upload incomplete; update bytes
            await session.execute(
                update(UploadSession)
                .where(UploadSession.id == upload_sess.id)
                .values(bytes_uploaded=recon_result.bytes_received, updated_at=datetime.now(UTC))
            )
            await session.execute(
                update(PublishAttempt)
                .where(PublishAttempt.id == attempt.id)
                .values(reconciliation_status=ReconciliationStatus.PENDING.value)
            )
            await session.commit()
            return ReconciliationStatus.PENDING

        # Manual hold / Session not found
        await session.execute(
            update(PublishAttempt)
            .where(PublishAttempt.id == attempt.id)
            .values(reconciliation_status=ReconciliationStatus.MANUAL_HOLD.value)
        )
        trans = PublishAttemptTransition(
            id=uuid4(),
            publish_attempt_id=attempt.id,
            from_state=PublishAttemptState.UNKNOWN.value,
            to_state=PublishAttemptState.UNKNOWN.value,
            reason=f"Reconciliation hold: {recon_result.diagnostic_reason}",
            actor="RECONCILER",
        )
        session.add(trans)
        await session.commit()
        logger.warning("Attempt reconciliation placed on MANUAL_HOLD", attempt_id=str(attempt.id))
        return ReconciliationStatus.MANUAL_HOLD

    @classmethod
    async def reconcile_pending_attempts_sweep(
        cls,
        session: AsyncSession,
        limit: int = 20,
    ) -> int:
        """Periodic sweep resolving UNKNOWN publish attempts."""
        stmt = (
            select(PublishAttempt.id)
            .where(
                PublishAttempt.state == PublishAttemptState.UNKNOWN.value,
                PublishAttempt.reconciliation_status.in_(
                    [None, ReconciliationStatus.PENDING.value]
                ),
            )
            .limit(limit)
        )
        res = await session.execute(stmt)
        attempt_ids = res.scalars().all()

        reconciled_count = 0
        for att_id in attempt_ids:
            try:
                status = await cls.reconcile_attempt(session, att_id)
                if status in (
                    ReconciliationStatus.CONFIRMED_SUCCESS,
                    ReconciliationStatus.MANUAL_HOLD,
                ):
                    reconciled_count += 1
            except Exception as exc:
                logger.error(
                    "Error during attempt reconciliation", attempt_id=str(att_id), error=str(exc)
                )

        return reconciled_count
