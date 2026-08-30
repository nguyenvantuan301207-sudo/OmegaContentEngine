"""FastAPI router for OMEGA-010 Smart Scheduler.

Exposes REST endpoints for policy management, schedule evaluations,
reservation queries, manual release, and channel schedule timelines.
Mounted at /api/v1/scheduler.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application.scheduler.evaluation_engine import ScheduleEvaluationEngine
from omega.application.scheduler.policy_service import SchedulePolicyService
from omega.domain.scheduler import (
    ChannelTimelineResponse,
    ReservationState,
    ScheduleDecisionResponse,
    SchedulePolicyCreate,
    SchedulePolicyResponse,
    ScheduleRequest,
    ScheduleReservationResponse,
)
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ScheduleDecision,
    ScheduleReservation,
    ScheduleStateTransition,
)
from omega.logging import get_logger

logger = get_logger(service="omega-scheduler-api")

router = APIRouter(prefix="/api/v1/scheduler", tags=["scheduler"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


# ── 1. Policy Endpoints ──


@router.get("/policies", response_model=list[SchedulePolicyResponse])
async def list_policies(
    session: DBSession,
    workload_category: Annotated[
        str | None, Query(description="Filter by workload category")
    ] = None,
) -> list[SchedulePolicyResponse]:
    """List all schedule policies, optionally filtered by workload category."""
    policies = await SchedulePolicyService.list_policies(session, workload_category)
    return [SchedulePolicyResponse.model_validate(p) for p in policies]


@router.post(
    "/policies", response_model=SchedulePolicyResponse, status_code=status.HTTP_201_CREATED
)
async def create_policy(
    payload: SchedulePolicyCreate,
    session: DBSession,
) -> SchedulePolicyResponse:
    """Create a new schedule policy in DRAFT status (or optionally activate immediately)."""
    try:
        policy = await SchedulePolicyService.create_policy(
            session,
            workload_category=payload.workload_category.value,
            version=payload.version,
            policy_config=payload.policy_config,
            activate=payload.activate,
        )
        await session.commit()
        await session.refresh(policy)
        return SchedulePolicyResponse.model_validate(policy)
    except Exception as exc:
        await session.rollback()
        logger.error("Failed to create schedule policy", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Policy creation failed: {str(exc)}",
        ) from exc


@router.post("/policies/{policy_id}/activate", response_model=SchedulePolicyResponse)
async def activate_policy(
    policy_id: UUID,
    session: DBSession,
) -> SchedulePolicyResponse:
    """Activate a DRAFT schedule policy, retiring any currently active policy for the category."""
    try:
        policy = await SchedulePolicyService.activate_policy(session, policy_id)
        await session.commit()
        await session.refresh(policy)
        return SchedulePolicyResponse.model_validate(policy)
    except Exception as exc:
        await session.rollback()
        logger.error("Failed to activate schedule policy", policy_id=str(policy_id), error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Policy activation failed: {str(exc)}",
        ) from exc


# ── 2. Evaluation Endpoints ──


@router.post("/evaluate", response_model=ScheduleDecisionResponse)
async def evaluate_schedule(
    request: ScheduleRequest,
    session: DBSession,
) -> ScheduleDecisionResponse:
    """Evaluate a schedule request using the authoritative ScheduleEvaluationEngine."""
    try:
        decision = await ScheduleEvaluationEngine.evaluate_schedule(session, request)
        return decision
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Schedule evaluation error", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Schedule evaluation failed",
        ) from exc


@router.get("/decisions/{decision_id}", response_model=ScheduleDecisionResponse)
async def get_decision(
    decision_id: UUID,
    session: DBSession,
) -> ScheduleDecisionResponse:
    """Retrieve an immutable schedule decision by ID."""
    res = await session.execute(select(ScheduleDecision).where(ScheduleDecision.id == decision_id))
    decision = res.scalar_one_or_none()
    if not decision:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision not found")
    return ScheduleDecisionResponse.model_validate(decision)


# ── 3. Reservation Endpoints ──


@router.get("/reservations", response_model=list[ScheduleReservationResponse])
async def list_reservations(
    session: DBSession,
    channel_id: Annotated[UUID | None, Query(description="Filter by channel ID")] = None,
    state: Annotated[str | None, Query(description="Filter by reservation state")] = None,
    workload_category: Annotated[
        str | None, Query(description="Filter by workload category")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ScheduleReservationResponse]:
    """List schedule reservations with optional filters."""
    stmt = (
        select(ScheduleReservation)
        .order_by(ScheduleReservation.scheduled_start_at.desc())
        .limit(limit)
    )
    if channel_id:
        stmt = stmt.where(ScheduleReservation.channel_id == channel_id)
    if state:
        stmt = stmt.where(ScheduleReservation.state == state)
    if workload_category:
        stmt = stmt.where(ScheduleReservation.workload_category == workload_category)

    res = await session.execute(stmt)
    reservations = list(res.scalars().all())
    return [ScheduleReservationResponse.model_validate(r) for r in reservations]


@router.get("/reservations/{reservation_id}", response_model=ScheduleReservationResponse)
async def get_reservation(
    reservation_id: UUID,
    session: DBSession,
) -> ScheduleReservationResponse:
    """Get reservation details by ID."""
    res = await session.execute(
        select(ScheduleReservation).where(ScheduleReservation.id == reservation_id)
    )
    reservation = res.scalar_one_or_none()
    if not reservation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reservation not found")
    return ScheduleReservationResponse.model_validate(reservation)


@router.post("/reservations/{reservation_id}/release", response_model=ScheduleReservationResponse)
async def release_reservation(
    reservation_id: UUID,
    session: DBSession,
    reason: Annotated[
        str, Query(description="Reason for releasing reservation")
    ] = "Manual operator release",
) -> ScheduleReservationResponse:
    """Manually release an ACTIVE or DISPATCHING reservation with audit trail."""
    res = await session.execute(
        select(ScheduleReservation)
        .where(ScheduleReservation.id == reservation_id)
        .with_for_update()
    )
    reservation = res.scalar_one_or_none()
    if not reservation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reservation not found")

    if reservation.state in (
        ReservationState.CONSUMED.value,
        ReservationState.RELEASED.value,
        ReservationState.EXPIRED.value,
        ReservationState.CANCELLED.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot release reservation in {reservation.state} state",
        )

    now = datetime.now(UTC)
    from_state = reservation.state
    reservation.state = ReservationState.RELEASED.value
    reservation.released_at = now
    reservation.updated_at = now

    transition = ScheduleStateTransition(
        id=uuid4(),
        reservation_id=reservation.id,
        from_state=from_state,
        to_state=ReservationState.RELEASED.value,
        reason=reason,
        actor="OPERATOR",
        created_at=now,
    )
    session.add(transition)
    await session.commit()
    await session.refresh(reservation)

    logger.info(
        "reservation_released",
        reservation_id=str(reservation.id),
        from_state=from_state,
        reason=reason,
    )
    return ScheduleReservationResponse.model_validate(reservation)


# ── 4. Channel Schedule Timeline ──


@router.get("/channels/{channel_id}/timeline", response_model=ChannelTimelineResponse)
async def get_channel_timeline(
    channel_id: UUID,
    session: DBSession,
) -> ChannelTimelineResponse:
    """Get upcoming schedule reservations and daily throughput stats for a channel."""
    # Load channel
    chan_res = await session.execute(select(Channel).where(Channel.id == channel_id))
    channel = chan_res.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")

    # Load Channel DNA for timezone and daily limit
    dna_res = await session.execute(
        select(ChannelDNARevision)
        .where(ChannelDNARevision.channel_id == channel_id)
        .order_by(ChannelDNARevision.version.desc())
        .limit(1)
    )
    latest_dna = dna_res.scalar_one_or_none()
    target_timezone = "UTC"
    if latest_dna and latest_dna.snapshot:
        target_timezone = latest_dna.snapshot.get("publishing_preferences", {}).get(
            "target_timezone", "UTC"
        )

    # Load active policy for daily limit
    policy = await SchedulePolicyService.get_active_policy(session, "EXTERNAL_PUBLISH")
    limit_today = policy.policy_config.get("max_scheduled_items_per_day", 5) if policy else 5

    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Count today's items
    count_res = await session.execute(
        select(func.count(ScheduleReservation.id)).where(
            ScheduleReservation.channel_id == channel_id,
            ScheduleReservation.state.in_(
                [
                    ReservationState.ACTIVE.value,
                    ReservationState.DISPATCHING.value,
                    ReservationState.CONSUMED.value,
                ]
            ),
            ScheduleReservation.scheduled_start_at >= day_start,
            ScheduleReservation.scheduled_start_at < day_start + timedelta(days=1),
        )
    )
    used_today = count_res.scalar() or 0

    # Fetch upcoming reservations
    res_stmt = (
        select(ScheduleReservation)
        .where(
            ScheduleReservation.channel_id == channel_id,
            ScheduleReservation.scheduled_start_at >= now - timedelta(hours=2),
        )
        .order_by(ScheduleReservation.scheduled_start_at.asc())
        .limit(30)
    )
    res_items = await session.execute(res_stmt)
    reservations = list(res_items.scalars().all())

    return ChannelTimelineResponse(
        channel_id=channel_id,
        timezone=target_timezone,
        reservations=[ScheduleReservationResponse.model_validate(r) for r in reservations],
        capacity_used_today=used_today,
        capacity_limit_today=limit_today,
    )
