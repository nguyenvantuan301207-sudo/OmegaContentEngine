"""Pure read-only query service for Publisher Observability and Operations.

Provides structured projections of publisher calendar, queue configuration,
in-flight publications, retry queues, recovery holds, dead letters, and history.

INVARIANTS:
1. PURE READ-ONLY: Never mutates database rows, never creates attempts/handoffs,
   never calls external providers (Google/YouTube).
2. ZERO SECRETS: Never exposes access tokens, refresh tokens, credentials, or session URIs.
3. SQL PAGINATION: Always enforces bounded LIMIT/OFFSET at the database query level.
4. CANONICAL SCHEDULER: Reuses OMEGA-010 ScheduleReservation/ScheduleDecision truth.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.error_sanitizer import sanitize_sensitive_text
from omega.application.publisher.handoff_relay import MAX_HANDOFF_ATTEMPTS
from omega.domain.publisher import (
    HandoffStatus,
    PublishAttemptState,
    PublishIntentState,
    ReconciliationStatus,
)
from omega.domain.scheduler import (
    ReservationState,
    ScheduleTargetType,
    ScheduleWorkloadCategory,
)
from omega.infrastructure.celery_app import publisher_worker_metadata
from omega.infrastructure.models import (
    Channel,
    PublishAttempt,
    PublisherSchedulerHandoffOutbox,
    PublishIntent,
    ScheduleReservation,
    ScheduleStateTransition,
    UploadSession,
)

# Canonical active/in-flight attempt states (NO "COMMITTING" - strictly domain-defined)
IN_FLIGHT_ATTEMPT_STATES = (
    PublishAttemptState.CREATED.value,
    PublishAttemptState.UPLOADING.value,
    PublishAttemptState.FINALIZING.value,
    PublishAttemptState.UNKNOWN.value,
)


class PublisherOperationsQueryService:
    """Read-only operational projection service for the publisher dashboard."""

    @staticmethod
    def _clamp_pagination(limit: int, offset: int) -> tuple[int, int]:
        safe_limit = max(1, min(limit, 100))
        safe_offset = max(0, offset)
        return safe_limit, safe_offset

    @classmethod
    async def get_overview(cls, session: AsyncSession) -> dict[str, Any]:
        """Return aggregate operational metrics for cards and status indicators."""
        now = datetime.now(UTC)
        recent_window_hours = 24
        recent_cutoff = now - timedelta(hours=recent_window_hours)

        # 1. Calendar scheduling metrics (OMEGA-010 canonical linkage)
        sched_upcoming_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ScheduleReservation)
                    .where(
                        ScheduleReservation.target_type == ScheduleTargetType.PUBLISH_INTENT.value,
                        ScheduleReservation.state.in_(
                            [ReservationState.ACTIVE.value, ReservationState.DISPATCHING.value]
                        ),
                        ScheduleReservation.scheduled_start_at > now,
                    )
                )
            ).scalar_one()
        )

        sched_due_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ScheduleReservation)
                    .where(
                        ScheduleReservation.target_type == ScheduleTargetType.PUBLISH_INTENT.value,
                        ScheduleReservation.state.in_(
                            [ReservationState.ACTIVE.value, ReservationState.DISPATCHING.value]
                        ),
                        ScheduleReservation.scheduled_start_at <= now,
                    )
                )
            ).scalar_one()
        )

        # 2. In-flight and attempt counts
        publish_claimed = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(PublishIntent)
                    .where(PublishIntent.state == PublishIntentState.CLAIMED.value)
                )
            ).scalar_one()
        )

        publish_uploading = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(PublishAttempt)
                    .where(PublishAttempt.state == PublishAttemptState.UPLOADING.value)
                )
            ).scalar_one()
        )

        publish_unknown = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(PublishAttempt)
                    .where(PublishAttempt.state == PublishAttemptState.UNKNOWN.value)
                )
            ).scalar_one()
        )

        # 3. Recent success / failure (deterministic 24h window)
        publish_succeeded_recent = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(PublishAttempt)
                    .where(
                        PublishAttempt.state == PublishAttemptState.SUCCEEDED.value,
                        or_(
                            PublishAttempt.completed_at >= recent_cutoff,
                            and_(
                                PublishAttempt.completed_at.is_(None),
                                PublishAttempt.started_at >= recent_cutoff,
                            ),
                        ),
                    )
                )
            ).scalar_one()
        )

        publish_failed_recent = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(PublishAttempt)
                    .where(
                        PublishAttempt.state.in_(
                            [
                                PublishAttemptState.PERMANENT_FAILED.value,
                                PublishAttemptState.RETRYABLE_FAILED.value,
                            ]
                        ),
                        or_(
                            PublishAttempt.completed_at >= recent_cutoff,
                            and_(
                                PublishAttempt.completed_at.is_(None),
                                PublishAttempt.started_at >= recent_cutoff,
                            ),
                        ),
                    )
                )
            ).scalar_one()
        )

        # 4. Retry and dead-letter counts
        async def count_handoff(status: HandoffStatus) -> int:
            return int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(PublisherSchedulerHandoffOutbox)
                        .where(PublisherSchedulerHandoffOutbox.status == status.value)
                    )
                ).scalar_one()
            )

        retry_pending = await count_handoff(HandoffStatus.PENDING)
        retry_claimed = await count_handoff(HandoffStatus.CLAIMED)
        dead_letter_count = await count_handoff(HandoffStatus.DEAD_LETTER)

        oldest_pending = (
            await session.execute(
                select(func.min(PublisherSchedulerHandoffOutbox.next_attempt_at)).where(
                    PublisherSchedulerHandoffOutbox.status == HandoffStatus.PENDING.value
                )
            )
        ).scalar_one_or_none()
        oldest_pending_retry_age = (
            max(0.0, (now - oldest_pending).total_seconds()) if oldest_pending else None
        )

        # 5. Distinct hold metrics (Recovery Hold vs Schedule Hold)
        recovery_manual_hold_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(PublishAttempt)
                    .where(
                        PublishAttempt.reconciliation_status
                        == ReconciliationStatus.MANUAL_HOLD.value
                    )
                )
            ).scalar_one()
        )

        oldest_recovery_hold = (
            await session.execute(
                select(func.min(PublishAttempt.started_at)).where(
                    PublishAttempt.reconciliation_status
                    == ReconciliationStatus.MANUAL_HOLD.value
                )
            )
        ).scalar_one_or_none()
        oldest_recovery_hold_age = (
            max(0.0, (now - oldest_recovery_hold).total_seconds()) if oldest_recovery_hold else None
        )

        schedule_manual_hold_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ScheduleReservation)
                    .join(
                        ScheduleStateTransition,
                        ScheduleStateTransition.reservation_id == ScheduleReservation.id,
                    )
                    .where(
                        ScheduleReservation.workload_category
                        == ScheduleWorkloadCategory.EXTERNAL_PUBLISH.value,
                        ScheduleReservation.state == ReservationState.RELEASED.value,
                        ScheduleStateTransition.reason.like("MANUAL_HOLD%"),
                    )
                )
            ).scalar_one()
        )

        # 6. Fleet configuration (pure metadata, no liveness claim)
        worker_cfg = publisher_worker_metadata()

        return {
            "scheduled_upcoming": sched_upcoming_count,
            "scheduled_due": sched_due_count,
            "publish_claimed": publish_claimed,
            "publish_uploading": publish_uploading,
            "publish_unknown": publish_unknown,
            "publish_succeeded_recent": publish_succeeded_recent,
            "publish_failed_recent": publish_failed_recent,
            "recent_window_hours": recent_window_hours,
            "retry_pending": retry_pending,
            "retry_claimed": retry_claimed,
            "dead_letter_count": dead_letter_count,
            "recovery_manual_hold_count": recovery_manual_hold_count,
            "schedule_manual_hold_count": schedule_manual_hold_count,
            "oldest_pending_retry_age_seconds": oldest_pending_retry_age,
            "oldest_recovery_hold_age_seconds": oldest_recovery_hold_age,
            "publisher_queue_name": worker_cfg.get("queue_name", "omega-publisher"),
            "publisher_worker_role": worker_cfg.get("worker_role", "publisher"),
            "configured_concurrency": worker_cfg.get("concurrency_default", 1),
            "prefetch_multiplier": worker_cfg.get("prefetch_multiplier", 1),
            "max_handoff_attempts": MAX_HANDOFF_ATTEMPTS,
        }

    @classmethod
    async def get_upcoming_publications(
        cls,
        session: AsyncSession,
        *,
        limit: int = 25,
        offset: int = 0,
        channel_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Return upcoming publication calendar entries from canonical ScheduleReservation."""
        safe_limit, safe_offset = cls._clamp_pagination(limit, offset)

        base_stmt = (
            select(ScheduleReservation, PublishIntent, Channel)
            .join(
                PublishIntent,
                and_(
                    ScheduleReservation.target_id == PublishIntent.id,
                    ScheduleReservation.target_type == ScheduleTargetType.PUBLISH_INTENT.value,
                ),
            )
            .outerjoin(Channel, PublishIntent.channel_id == Channel.id)
            .where(
                ScheduleReservation.state.in_(
                    [ReservationState.ACTIVE.value, ReservationState.DISPATCHING.value]
                ),
            )
        )
        if channel_id:
            base_stmt = base_stmt.where(PublishIntent.channel_id == channel_id)

        # Count total
        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total_count = int((await session.execute(count_stmt)).scalar_one())

        # Fetch page with SQL LIMIT/OFFSET
        stmt = (
            base_stmt.order_by(ScheduleReservation.scheduled_start_at.asc())
            .limit(safe_limit)
            .offset(safe_offset)
        )
        rows = (await session.execute(stmt)).all()

        items = []
        for res, intent, ch in rows:
            items.append(
                {
                    "reservation_id": res.id,
                    "publish_intent_id": intent.id,
                    "title": intent.title,
                    "channel_id": intent.channel_id,
                    "channel_name": ch.name if ch else None,
                    "scheduled_start_at": res.scheduled_start_at,
                    "scheduled_end_at": res.scheduled_end_at,
                    "reservation_state": res.state,
                    "dispatching_at": res.dispatching_at,
                    "intent_state": intent.state,
                    "requested_privacy_status": intent.requested_privacy_status,
                    "revision_number": intent.revision_number,
                }
            )

        return {
            "items": items,
            "total_count": total_count,
            "limit": safe_limit,
            "offset": safe_offset,
        }

    @classmethod
    async def get_active_publications(
        cls,
        session: AsyncSession,
        *,
        limit: int = 25,
        offset: int = 0,
        channel_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Return active/in-flight publications (intents claimed or in-flight attempts)."""
        safe_limit, safe_offset = cls._clamp_pagination(limit, offset)

        # Join attempt, intent, channel, and upload session (excluding session_uri)
        base_stmt = (
            select(PublishAttempt, PublishIntent, Channel, UploadSession)
            .join(PublishIntent, PublishAttempt.publish_intent_id == PublishIntent.id)
            .outerjoin(Channel, PublishIntent.channel_id == Channel.id)
            .outerjoin(UploadSession, UploadSession.publish_attempt_id == PublishAttempt.id)
            .where(
                or_(
                    PublishAttempt.state.in_(IN_FLIGHT_ATTEMPT_STATES),
                    PublishIntent.state == PublishIntentState.CLAIMED.value,
                )
            )
        )
        if channel_id:
            base_stmt = base_stmt.where(PublishIntent.channel_id == channel_id)

        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total_count = int((await session.execute(count_stmt)).scalar_one())

        stmt = (
            base_stmt.order_by(PublishAttempt.started_at.desc())
            .limit(safe_limit)
            .offset(safe_offset)
        )
        rows = (await session.execute(stmt)).all()

        items = []
        for attempt, intent, ch, upload in rows:
            bytes_up = upload.bytes_uploaded if upload else 0
            total_b = upload.total_bytes if upload else 0
            pct = round((bytes_up / total_b) * 100.0, 2) if total_b > 0 else 0.0

            items.append(
                {
                    "publish_intent_id": intent.id,
                    "task_id": intent.task_id,
                    "channel_id": intent.channel_id,
                    "channel_name": ch.name if ch else None,
                    "title": intent.title,
                    "intent_state": intent.state,
                    "requested_privacy_status": intent.requested_privacy_status,
                    "attempt_id": attempt.id,
                    "attempt_number": attempt.attempt_number,
                    "attempt_state": attempt.state,
                    "error_category": attempt.error_category,
                    "reconciliation_status": attempt.reconciliation_status,
                    "started_at": attempt.started_at,
                    "claimed_by_worker_id": intent.claimed_by_worker_id,
                    "lease_expires_at": intent.lease_expires_at,
                    "bytes_uploaded": bytes_up,
                    "total_bytes": total_b,
                    "progress_percentage": pct,
                    "upload_expires_at": upload.expires_at if upload else None,
                }
            )

        return {
            "items": items,
            "total_count": total_count,
            "limit": safe_limit,
            "offset": safe_offset,
        }

    @classmethod
    async def get_retry_queue(
        cls,
        session: AsyncSession,
        *,
        limit: int = 25,
        offset: int = 0,
        channel_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Return pending/claimed retry handoff outbox entries."""
        safe_limit, safe_offset = cls._clamp_pagination(limit, offset)

        base_stmt = (
            select(PublisherSchedulerHandoffOutbox, PublishIntent, Channel)
            .join(
                PublishIntent,
                PublisherSchedulerHandoffOutbox.publish_intent_id == PublishIntent.id,
            )
            .outerjoin(Channel, PublishIntent.channel_id == Channel.id)
            .where(
                PublisherSchedulerHandoffOutbox.status.in_(
                    [HandoffStatus.PENDING.value, HandoffStatus.CLAIMED.value]
                )
            )
        )
        if channel_id:
            base_stmt = base_stmt.where(PublishIntent.channel_id == channel_id)

        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total_count = int((await session.execute(count_stmt)).scalar_one())

        stmt = (
            base_stmt.order_by(PublisherSchedulerHandoffOutbox.next_attempt_at.asc())
            .limit(safe_limit)
            .offset(safe_offset)
        )
        rows = (await session.execute(stmt)).all()

        items = []
        for handoff, intent, ch in rows:
            items.append(
                {
                    "handoff_id": handoff.id,
                    "publish_intent_id": intent.id,
                    "publish_attempt_id": handoff.publish_attempt_id,
                    "status": handoff.status,
                    "attempt_count": handoff.attempt_count,
                    "max_attempts": MAX_HANDOFF_ATTEMPTS,
                    "next_attempt_at": handoff.next_attempt_at,
                    "last_sanitized_error": (
                        sanitize_sensitive_text(handoff.last_error) if handoff.last_error else None
                    ),
                    "title": intent.title,
                    "channel_id": intent.channel_id,
                    "channel_name": ch.name if ch else None,
                    "created_at": handoff.created_at,
                }
            )

        return {
            "items": items,
            "total_count": total_count,
            "limit": safe_limit,
            "offset": safe_offset,
        }

    @classmethod
    async def get_manual_holds(
        cls,
        session: AsyncSession,
        *,
        limit: int = 25,
        offset: int = 0,
        channel_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Return attempts currently in provider recovery MANUAL_HOLD status."""
        safe_limit, safe_offset = cls._clamp_pagination(limit, offset)
        now = datetime.now(UTC)

        base_stmt = (
            select(PublishAttempt, PublishIntent, Channel)
            .join(PublishIntent, PublishAttempt.publish_intent_id == PublishIntent.id)
            .outerjoin(Channel, PublishIntent.channel_id == Channel.id)
            .where(
                PublishAttempt.reconciliation_status == ReconciliationStatus.MANUAL_HOLD.value
            )
        )
        if channel_id:
            base_stmt = base_stmt.where(PublishIntent.channel_id == channel_id)

        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total_count = int((await session.execute(count_stmt)).scalar_one())

        stmt = (
            base_stmt.order_by(PublishAttempt.started_at.desc())
            .limit(safe_limit)
            .offset(safe_offset)
        )
        rows = (await session.execute(stmt)).all()

        items = []
        for attempt, intent, ch in rows:
            age = max(0.0, (now - attempt.started_at).total_seconds()) if attempt.started_at else None
            items.append(
                {
                    "attempt_id": attempt.id,
                    "publish_intent_id": intent.id,
                    "attempt_number": attempt.attempt_number,
                    "attempt_state": attempt.state,
                    "error_category": attempt.error_category,
                    "reconciliation_status": attempt.reconciliation_status,
                    "last_sanitized_error": (
                        sanitize_sensitive_text(attempt.error_message)
                        if attempt.error_message
                        else None
                    ),
                    "started_at": attempt.started_at,
                    "age_seconds": age,
                    "title": intent.title,
                    "channel_id": intent.channel_id,
                    "channel_name": ch.name if ch else None,
                    "hold_source": "RECOVERY",
                }
            )

        return {
            "items": items,
            "total_count": total_count,
            "limit": safe_limit,
            "offset": safe_offset,
        }

    @classmethod
    async def get_dead_letters(
        cls,
        session: AsyncSession,
        *,
        limit: int = 25,
        offset: int = 0,
        channel_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Return exhausted retry handoffs placed in DEAD_LETTER status."""
        safe_limit, safe_offset = cls._clamp_pagination(limit, offset)

        base_stmt = (
            select(PublisherSchedulerHandoffOutbox, PublishIntent, Channel)
            .join(
                PublishIntent,
                PublisherSchedulerHandoffOutbox.publish_intent_id == PublishIntent.id,
            )
            .outerjoin(Channel, PublishIntent.channel_id == Channel.id)
            .where(
                PublisherSchedulerHandoffOutbox.status == HandoffStatus.DEAD_LETTER.value
            )
        )
        if channel_id:
            base_stmt = base_stmt.where(PublishIntent.channel_id == channel_id)

        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total_count = int((await session.execute(count_stmt)).scalar_one())

        stmt = (
            base_stmt.order_by(PublisherSchedulerHandoffOutbox.created_at.desc())
            .limit(safe_limit)
            .offset(safe_offset)
        )
        rows = (await session.execute(stmt)).all()

        items = []
        for handoff, intent, ch in rows:
            # Evaluate requeue eligibility purely in read mode
            is_approved = intent.state == PublishIntentState.APPROVED.value
            is_unleased = intent.claim_token is None and intent.lease_expires_at is None

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

            can_requeue = is_approved and is_unleased and (unresolved_unknown is None)
            blocked_reason = None
            if not is_approved:
                blocked_reason = "Intent is not in APPROVED state"
            elif not is_unleased:
                blocked_reason = "Intent has active or unexpired worker lease"
            elif unresolved_unknown is not None:
                blocked_reason = "Unresolved UNKNOWN attempt blocks safe requeue"

            items.append(
                {
                    "handoff_id": handoff.id,
                    "publish_intent_id": intent.id,
                    "publish_attempt_id": handoff.publish_attempt_id,
                    "status": handoff.status,
                    "attempt_count": handoff.attempt_count,
                    "max_attempts": MAX_HANDOFF_ATTEMPTS,
                    "next_retry_time": handoff.next_attempt_at,
                    "last_sanitized_error": (
                        sanitize_sensitive_text(handoff.last_error) if handoff.last_error else None
                    ),
                    "title": intent.title,
                    "channel_id": intent.channel_id,
                    "channel_name": ch.name if ch else None,
                    "created_at": handoff.created_at,
                    "can_requeue": can_requeue,
                    "requeue_blocked_reason": blocked_reason,
                }
            )

        return {
            "items": items,
            "total_count": total_count,
            "limit": safe_limit,
            "offset": safe_offset,
        }

    @classmethod
    async def get_recent_history(
        cls,
        session: AsyncSession,
        *,
        limit: int = 25,
        offset: int = 0,
        channel_id: UUID | None = None,
        state: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Return history of publication attempts with safe identifiers and sanitized errors."""
        safe_limit, safe_offset = cls._clamp_pagination(limit, offset)

        base_stmt = (
            select(PublishAttempt, PublishIntent, Channel)
            .join(PublishIntent, PublishAttempt.publish_intent_id == PublishIntent.id)
            .outerjoin(Channel, PublishIntent.channel_id == Channel.id)
        )

        if channel_id:
            base_stmt = base_stmt.where(PublishIntent.channel_id == channel_id)
        if state:
            base_stmt = base_stmt.where(PublishAttempt.state == state.upper())
        if search:
            s = f"%{search.strip()}%"
            base_stmt = base_stmt.where(
                or_(
                    PublishIntent.title.ilike(s),
                    PublishAttempt.provider_video_id.ilike(s),
                )
            )

        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total_count = int((await session.execute(count_stmt)).scalar_one())

        stmt = (
            base_stmt.order_by(PublishAttempt.started_at.desc())
            .limit(safe_limit)
            .offset(safe_offset)
        )
        rows = (await session.execute(stmt)).all()

        items = []
        for attempt, intent, ch in rows:
            duration = None
            if attempt.started_at and attempt.completed_at:
                duration = max(0.0, (attempt.completed_at - attempt.started_at).total_seconds())

            items.append(
                {
                    "attempt_id": attempt.id,
                    "publish_intent_id": intent.id,
                    "attempt_number": attempt.attempt_number,
                    "state": attempt.state,
                    "error_category": attempt.error_category,
                    "reconciliation_status": attempt.reconciliation_status,
                    "provider_video_id": attempt.provider_video_id,
                    "last_sanitized_error": (
                        sanitize_sensitive_text(attempt.error_message)
                        if attempt.error_message
                        else None
                    ),
                    "started_at": attempt.started_at,
                    "completed_at": attempt.completed_at,
                    "duration_seconds": duration,
                    "title": intent.title,
                    "channel_id": intent.channel_id,
                    "channel_name": ch.name if ch else None,
                    "requested_privacy_status": intent.requested_privacy_status,
                }
            )

        return {
            "items": items,
            "total_count": total_count,
            "limit": safe_limit,
            "offset": safe_offset,
        }

    @classmethod
    async def get_publication_detail(
        cls,
        session: AsyncSession,
        intent_id: UUID,
    ) -> dict[str, Any] | None:
        """Return the detailed view model for one PublishIntent."""
        intent = await session.get(PublishIntent, intent_id)
        if not intent:
            return None

        # Fetch channel
        channel = await session.get(Channel, intent.channel_id)

        # 1. Canonical calendar reservation
        reservation = (
            await session.execute(
                select(ScheduleReservation)
                .where(
                    ScheduleReservation.target_id == intent_id,
                    ScheduleReservation.target_type == ScheduleTargetType.PUBLISH_INTENT.value,
                )
                .order_by(desc(ScheduleReservation.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()

        # 2. Attempts history
        attempts_res = (
            await session.execute(
                select(PublishAttempt)
                .where(PublishAttempt.publish_intent_id == intent_id)
                .order_by(PublishAttempt.attempt_number.asc())
            )
        ).scalars().all()

        attempt_items = []
        latest_attempt = attempts_res[-1] if attempts_res else None
        active_upload = None

        for att in attempts_res:
            upload = (
                await session.execute(
                    select(UploadSession).where(UploadSession.publish_attempt_id == att.id)
                )
            ).scalar_one_or_none()
            if upload and not active_upload:
                active_upload = upload

            duration = None
            if att.started_at and att.completed_at:
                duration = max(0.0, (att.completed_at - att.started_at).total_seconds())

            attempt_items.append(
                {
                    "attempt_id": att.id,
                    "attempt_number": att.attempt_number,
                    "state": att.state,
                    "error_category": att.error_category,
                    "reconciliation_status": att.reconciliation_status,
                    "provider_video_id": att.provider_video_id,
                    "last_sanitized_error": (
                        sanitize_sensitive_text(att.error_message) if att.error_message else None
                    ),
                    "started_at": att.started_at,
                    "completed_at": att.completed_at,
                    "duration_seconds": duration,
                }
            )

        # 3. Upload summary (NEVER EXPOSING session_uri)
        upload_summary = None
        if active_upload:
            pct = (
                round((active_upload.bytes_uploaded / active_upload.total_bytes) * 100.0, 2)
                if active_upload.total_bytes > 0
                else 0.0
            )
            upload_summary = {
                "session_id": active_upload.id,
                "bytes_uploaded": active_upload.bytes_uploaded,
                "total_bytes": active_upload.total_bytes,
                "progress_percentage": pct,
                "expires_at": active_upload.expires_at,
            }

        # 4. Latest retry handoff
        latest_handoff = (
            await session.execute(
                select(PublisherSchedulerHandoffOutbox)
                .where(PublisherSchedulerHandoffOutbox.publish_intent_id == intent_id)
                .order_by(desc(PublisherSchedulerHandoffOutbox.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()

        retry_summary = None
        if latest_handoff:
            retry_summary = {
                "handoff_id": latest_handoff.id,
                "status": latest_handoff.status,
                "attempt_count": latest_handoff.attempt_count,
                "max_attempts": MAX_HANDOFF_ATTEMPTS,
                "next_attempt_at": latest_handoff.next_attempt_at,
                "last_sanitized_error": (
                    sanitize_sensitive_text(latest_handoff.last_error)
                    if latest_handoff.last_error
                    else None
                ),
            }

        # 5. Recovery action eligibility
        allowed_operations: list[str] = []
        blocked_operations: dict[str, str] = {}
        now = datetime.now(UTC)

        # Evaluate DEAD_LETTER requeue
        if latest_handoff and latest_handoff.status == HandoffStatus.DEAD_LETTER.value:
            if intent.state != PublishIntentState.APPROVED.value:
                blocked_operations["REQUEUE_DEAD_LETTER"] = "Intent is not in APPROVED state"
            elif intent.claim_token is not None or intent.lease_expires_at is not None:
                blocked_operations["REQUEUE_DEAD_LETTER"] = "Intent has active or unexpired worker claim"
            else:
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
                    blocked_operations["REQUEUE_DEAD_LETTER"] = "Unresolved UNKNOWN attempt blocks requeue"
                else:
                    allowed_operations.append("REQUEUE_DEAD_LETTER")

        # Evaluate MANUAL_HOLD reconciliation / session resume
        if latest_attempt and latest_attempt.reconciliation_status == ReconciliationStatus.MANUAL_HOLD.value:
            if latest_attempt.state == PublishAttemptState.UNKNOWN.value:
                allowed_operations.append("RECONCILE_AGAIN")
                if (
                    active_upload
                    and active_upload.expires_at > now
                    and active_upload.bytes_uploaded < active_upload.total_bytes
                ):
                    allowed_operations.append("AUTHORIZE_EXISTING_SESSION_RESUME")
                else:
                    blocked_operations["AUTHORIZE_EXISTING_SESSION_RESUME"] = (
                        "Upload session is expired or already fully uploaded"
                    )
            else:
                blocked_operations["RECONCILE_AGAIN"] = (
                    "Attempt is not in UNKNOWN state; manual hold is terminal"
                )

        # UNKNOWN outcomes NEVER permit a generic retry
        if latest_attempt and latest_attempt.state == PublishAttemptState.UNKNOWN.value:
            blocked_operations["RETRY"] = (
                "Ambiguous UNKNOWN outcome requires authoritative reconciliation; generic retry is strictly forbidden"
            )

        # Provider output
        provider_video_id = (
            latest_attempt.provider_video_id if latest_attempt else None
        )
        provider_url = (
            f"https://youtu.be/{provider_video_id}" if provider_video_id else None
        )

        return {
            "intent_id": intent.id,
            "task_id": intent.task_id,
            "channel_id": intent.channel_id,
            "channel_name": channel.name if channel else None,
            "title": intent.title,
            "requested_privacy_status": intent.requested_privacy_status,
            "intent_state": intent.state,
            "revision_number": intent.revision_number,
            "attempt_generation": intent.attempt_generation,
            "created_at": intent.created_at,
            "calendar": (
                {
                    "reservation_id": reservation.id,
                    "scheduled_start_at": reservation.scheduled_start_at,
                    "scheduled_end_at": reservation.scheduled_end_at,
                    "reservation_state": reservation.state,
                    "dispatching_at": reservation.dispatching_at,
                }
                if reservation
                else None
            ),
            "attempts": attempt_items,
            "upload_session": upload_summary,
            "retry_handoff": retry_summary,
            "provider": {
                "video_id": provider_video_id,
                "url": provider_url,
            },
            "recovery_eligibility": {
                "allowed_operations": allowed_operations,
                "blocked_operations": blocked_operations,
            },
        }
