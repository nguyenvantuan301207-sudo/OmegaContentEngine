"""Publisher Operations and Observability API Router for OMEGA-011 / P17-D.

Provides read-only queries for publisher operations dashboard and tightly fenced
operator action endpoints for manual-hold reconciliation and dead-letter requeue.

INVARIANTS:
1. Pure read queries delegate exclusively to PublisherOperationsQueryService.
2. Controlled actions delegate exclusively to PublisherRecoveryOperationsService.
3. Every response schema strictly enforces that tokens and session URIs are excluded.
4. Error responses are sanitized with sanitize_sensitive_text.
5. Reconciliation is explicitly classified as EXPLICIT_EXTERNAL_RECONCILIATION.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application.error_sanitizer import sanitize_sensitive_text
from omega.application.publisher.operations_query_service import (
    PublisherOperationsQueryService,
)
from omega.application.publisher.recovery_operations import (
    PublisherRecoveryOperationsService,
    RecoveryOperationRejected,
)
from omega.domain.publisher_operations import (
    ActivePublicationListResponse,
    AuthorizeSessionResumeResponse,
    CalendarPublicationListResponse,
    DeadLetterListResponse,
    ManualHoldListResponse,
    OperatorActionRequest,
    PublicationDetailResponse,
    PublicationHistoryListResponse,
    PublisherOperationsOverviewResponse,
    ReconcileManualHoldResponse,
    RequeueDeadLetterResponse,
    RetryQueueListResponse,
)
from omega.logging import get_logger

logger = get_logger(service="omega-api-publisher-operations")

router = APIRouter(prefix="/api/v1/publisher/operations", tags=["publisher-operations"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


def _safe_error_detail(exc: Exception) -> str:
    """Ensure error messages returned to the client are strictly redacted."""
    return sanitize_sensitive_text(str(exc))


# ── Read-Only Dashboard Observability Endpoints ──


@router.get("/overview", response_model=PublisherOperationsOverviewResponse)
async def get_operations_overview(
    session: DBSession,
) -> PublisherOperationsOverviewResponse:
    """Return aggregate operational metrics for cards and status indicators."""
    try:
        data = await PublisherOperationsQueryService.get_overview(session)
        return PublisherOperationsOverviewResponse.model_validate(data)
    except Exception as exc:
        logger.error("Failed to load publisher operations overview", error=_safe_error_detail(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load operations overview: {_safe_error_detail(exc)}",
        ) from exc


@router.get("/calendar", response_model=CalendarPublicationListResponse)
async def get_upcoming_calendar(
    session: DBSession,
    limit: Annotated[int, Query(ge=1, le=100, description="Page limit (max 100)")] = 25,
    offset: Annotated[int, Query(ge=0, description="Page offset")] = 0,
    channel_id: Annotated[UUID | None, Query(description="Filter by channel ID")] = None,
) -> CalendarPublicationListResponse:
    """Return upcoming publications from canonical ScheduleReservation truth."""
    try:
        data = await PublisherOperationsQueryService.get_upcoming_publications(
            session, limit=limit, offset=offset, channel_id=channel_id
        )
        return CalendarPublicationListResponse.model_validate(data)
    except Exception as exc:
        logger.error("Failed to query upcoming calendar", error=_safe_error_detail(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query upcoming calendar: {_safe_error_detail(exc)}",
        ) from exc


@router.get("/active", response_model=ActivePublicationListResponse)
async def get_active_publications(
    session: DBSession,
    limit: Annotated[int, Query(ge=1, le=100, description="Page limit (max 100)")] = 25,
    offset: Annotated[int, Query(ge=0, description="Page offset")] = 0,
    channel_id: Annotated[UUID | None, Query(description="Filter by channel ID")] = None,
) -> ActivePublicationListResponse:
    """Return active/in-flight publications and chunk progression."""
    try:
        data = await PublisherOperationsQueryService.get_active_publications(
            session, limit=limit, offset=offset, channel_id=channel_id
        )
        return ActivePublicationListResponse.model_validate(data)
    except Exception as exc:
        logger.error("Failed to query active publications", error=_safe_error_detail(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query active publications: {_safe_error_detail(exc)}",
        ) from exc


@router.get("/retries", response_model=RetryQueueListResponse)
async def get_retry_queue(
    session: DBSession,
    limit: Annotated[int, Query(ge=1, le=100, description="Page limit (max 100)")] = 25,
    offset: Annotated[int, Query(ge=0, description="Page offset")] = 0,
    channel_id: Annotated[UUID | None, Query(description="Filter by channel ID")] = None,
) -> RetryQueueListResponse:
    """Return pending/claimed retry handoff queue items."""
    try:
        data = await PublisherOperationsQueryService.get_retry_queue(
            session, limit=limit, offset=offset, channel_id=channel_id
        )
        return RetryQueueListResponse.model_validate(data)
    except Exception as exc:
        logger.error("Failed to query retry queue", error=_safe_error_detail(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query retry queue: {_safe_error_detail(exc)}",
        ) from exc


@router.get("/manual-holds", response_model=ManualHoldListResponse)
async def get_manual_holds(
    session: DBSession,
    limit: Annotated[int, Query(ge=1, le=100, description="Page limit (max 100)")] = 25,
    offset: Annotated[int, Query(ge=0, description="Page offset")] = 0,
    channel_id: Annotated[UUID | None, Query(description="Filter by channel ID")] = None,
) -> ManualHoldListResponse:
    """Return publication attempts in provider recovery MANUAL_HOLD status."""
    try:
        data = await PublisherOperationsQueryService.get_manual_holds(
            session, limit=limit, offset=offset, channel_id=channel_id
        )
        return ManualHoldListResponse.model_validate(data)
    except Exception as exc:
        logger.error("Failed to query manual holds", error=_safe_error_detail(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query manual holds: {_safe_error_detail(exc)}",
        ) from exc


@router.get("/dead-letters", response_model=DeadLetterListResponse)
async def get_dead_letters(
    session: DBSession,
    limit: Annotated[int, Query(ge=1, le=100, description="Page limit (max 100)")] = 25,
    offset: Annotated[int, Query(ge=0, description="Page offset")] = 0,
    channel_id: Annotated[UUID | None, Query(description="Filter by channel ID")] = None,
) -> DeadLetterListResponse:
    """Return exhausted handoffs in DEAD_LETTER status."""
    try:
        data = await PublisherOperationsQueryService.get_dead_letters(
            session, limit=limit, offset=offset, channel_id=channel_id
        )
        return DeadLetterListResponse.model_validate(data)
    except Exception as exc:
        logger.error("Failed to query dead letters", error=_safe_error_detail(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query dead letters: {_safe_error_detail(exc)}",
        ) from exc


@router.get("/history", response_model=PublicationHistoryListResponse)
async def get_publication_history(
    session: DBSession,
    limit: Annotated[int, Query(ge=1, le=100, description="Page limit (max 100)")] = 25,
    offset: Annotated[int, Query(ge=0, description="Page offset")] = 0,
    channel_id: Annotated[UUID | None, Query(description="Filter by channel ID")] = None,
    state: Annotated[str | None, Query(description="Filter by attempt state")] = None,
    search: Annotated[str | None, Query(description="Search by title or video ID")] = None,
) -> PublicationHistoryListResponse:
    """Return publication attempt history with safe identifiers and sanitized errors."""
    try:
        data = await PublisherOperationsQueryService.get_recent_history(
            session,
            limit=limit,
            offset=offset,
            channel_id=channel_id,
            state=state,
            search=search,
        )
        return PublicationHistoryListResponse.model_validate(data)
    except Exception as exc:
        logger.error("Failed to query publication history", error=_safe_error_detail(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query publication history: {_safe_error_detail(exc)}",
        ) from exc


@router.get("/publications/{intent_id}", response_model=PublicationDetailResponse)
async def get_publication_detail(
    intent_id: UUID,
    session: DBSession,
) -> PublicationDetailResponse:
    """Return the detailed view model for one PublishIntent."""
    try:
        data = await PublisherOperationsQueryService.get_publication_detail(session, intent_id)
        if not data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"PublishIntent {intent_id} not found",
            )
        return PublicationDetailResponse.model_validate(data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to query publication detail", error=_safe_error_detail(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query publication detail: {_safe_error_detail(exc)}",
        ) from exc


# ── Controlled Operator Action Endpoints ──


@router.post(
    "/manual-holds/{attempt_id}/reconcile",
    response_model=ReconcileManualHoldResponse,
)
async def reconcile_manual_hold(
    attempt_id: UUID,
    payload: OperatorActionRequest,
    session: DBSession,
) -> ReconcileManualHoldResponse:
    """Trigger explicit external provider reconciliation for a MANUAL_HOLD attempt.

    CLASSIFICATION: EXPLICIT_EXTERNAL_RECONCILIATION
    Requires human operator confirmation. May query provider resumable session offset.
    """
    if not payload.actor.strip() or not payload.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="explicit actor and reason are required",
        )

    try:
        new_status = await PublisherRecoveryOperationsService.reconcile_again(
            session=session,
            attempt_id=attempt_id,
            actor=payload.actor,
            reason=payload.reason,
        )
        return ReconcileManualHoldResponse(
            attempt_id=attempt_id,
            reconciliation_status=new_status.value if hasattr(new_status, "value") else str(new_status),
            operation="EXPLICIT_EXTERNAL_RECONCILIATION",
            actor=payload.actor,
            reason=payload.reason,
        )
    except RecoveryOperationRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_safe_error_detail(exc),
        ) from exc
    except Exception as exc:
        logger.error("Failed to reconcile manual hold", error=_safe_error_detail(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_safe_error_detail(exc),
        ) from exc


@router.post(
    "/dead-letters/{handoff_id}/requeue",
    response_model=RequeueDeadLetterResponse,
)
async def requeue_dead_letter(
    handoff_id: UUID,
    payload: OperatorActionRequest,
    session: DBSession,
) -> RequeueDeadLetterResponse:
    """Reset an exhausted DEAD_LETTER handoff outbox row back to PENDING for controlled retry."""
    if not payload.actor.strip() or not payload.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="explicit actor and reason are required",
        )

    try:
        handoff = await PublisherRecoveryOperationsService.requeue_dead_letter(
            session=session,
            handoff_id=handoff_id,
            actor=payload.actor,
            reason=payload.reason,
        )
        return RequeueDeadLetterResponse(
            handoff_id=handoff.id,
            status=handoff.status,
            next_attempt_at=handoff.next_attempt_at,
            attempt_count=handoff.attempt_count,
            actor=payload.actor,
            reason=payload.reason,
        )
    except RecoveryOperationRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_safe_error_detail(exc),
        ) from exc
    except Exception as exc:
        logger.error("Failed to requeue dead letter", error=_safe_error_detail(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_safe_error_detail(exc),
        ) from exc


@router.post(
    "/sessions/{attempt_id}/resume-authorize",
    response_model=AuthorizeSessionResumeResponse,
)
async def authorize_session_resume(
    attempt_id: UUID,
    payload: OperatorActionRequest,
    session: DBSession,
) -> AuthorizeSessionResumeResponse:
    """Authorize resuming an existing upload session without executing provider network transmission."""
    if not payload.actor.strip() or not payload.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="explicit actor and reason are required",
        )

    try:
        result = await PublisherRecoveryOperationsService.authorize_existing_session_resume(
            session=session,
            attempt_id=attempt_id,
            actor=payload.actor,
            reason=payload.reason,
        )
        return AuthorizeSessionResumeResponse(
            attempt_id=result["attempt_id"],
            upload_session_id=result["upload_session_id"],
            provider_offset=result["provider_offset"],
            total_bytes=result["total_bytes"],
            actor=payload.actor,
            reason=payload.reason,
        )
    except RecoveryOperationRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_safe_error_detail(exc),
        ) from exc
    except Exception as exc:
        logger.error("Failed to authorize session resume", error=_safe_error_detail(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_safe_error_detail(exc),
        ) from exc
