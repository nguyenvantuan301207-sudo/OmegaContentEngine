"""REST API endpoints for OMEGA-014 Autonomous Loop."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application.autonomy.approval_service import AutonomyApprovalService
from omega.application.autonomy.loop_service import (
    AutonomyLoopService,
    IllegalStateTransitionError,
)
from omega.domain.autonomy import (
    ApprovalActorType,
    ApprovalDecisionValue,
    AutonomyLevel,
    AutonomyLoopState,
)
from omega.infrastructure.models import (
    AutonomyActionAttempt,
    AutonomyActionPlan,
    AutonomyApprovalDecision,
    AutonomyApprovalRequest,
    AutonomyLoop,
    AutonomyLoopIteration,
    AutonomyLoopLatestPointer,
    AutonomyObservationSnapshot,
)

router = APIRouter(prefix="/api/v1/autonomy", tags=["Autonomy Loop"])


# ── Pydantic Request / Response Schemas ──


class CreateLoopRequest(BaseModel):
    mission_id: UUID
    channel_id: UUID
    autonomy_level: AutonomyLevel = AutonomyLevel.SUPERVISED
    daily_cap_usd: Decimal = Field(default=Decimal("10.0000"), ge=Decimal("0.0100"))
    lifetime_cap_usd: Decimal = Field(default=Decimal("100.0000"), ge=Decimal("0.0100"))


class LoopStatusResponse(BaseModel):
    id: UUID
    mission_id: UUID
    channel_id: UUID
    autonomy_level: str
    operational_state: str
    current_iteration_sequence: int
    current_iteration_id: UUID | None = None
    created_at: str
    updated_at: str


class IterationSummary(BaseModel):
    id: UUID
    iteration_sequence: int
    state: str
    started_at: str
    completed_at: str | None = None
    stop_reason: str | None = None


class IterationDetailResponse(BaseModel):
    id: UUID
    loop_id: UUID
    iteration_sequence: int
    state: str
    started_at: str
    completed_at: str | None = None
    stop_reason: str | None = None
    observation: dict[str, Any]
    action_plan: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = Field(default_factory=list)


class DecisionPayload(BaseModel):
    review_reason: str
    reviewer_user_id: str | None = None


class ApprovalResponse(BaseModel):
    id: UUID
    loop_id: UUID
    action_plan_id: UUID
    semantic_action_key: str
    action_checksum: str
    guardian_epoch: int
    mission_state: str
    requested_at: str
    expires_at: str
    latest_decision: str | None = None


# ── Loop Endpoints ──


@router.post("/loops", response_model=LoopStatusResponse, status_code=status.HTTP_201_CREATED)
async def create_loop(
    payload: CreateLoopRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LoopStatusResponse:
    """Create or return existing autonomous loop for mission and channel."""
    try:
        loop = await AutonomyLoopService.create_loop(
            session=session,
            mission_id=payload.mission_id,
            channel_id=payload.channel_id,
            autonomy_level=payload.autonomy_level,
            daily_cap_usd=payload.daily_cap_usd,
            lifetime_cap_usd=payload.lifetime_cap_usd,
        )
        await session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    stmt_ptr = select(AutonomyLoopLatestPointer).where(AutonomyLoopLatestPointer.loop_id == loop.id)
    pointer = (await session.execute(stmt_ptr)).scalar_one()

    return LoopStatusResponse(
        id=loop.id,
        mission_id=loop.mission_id,
        channel_id=loop.channel_id,
        autonomy_level=loop.autonomy_level,
        operational_state=pointer.operational_state,
        current_iteration_sequence=pointer.current_iteration_sequence,
        current_iteration_id=pointer.current_iteration_id,
        created_at=loop.created_at.isoformat(),
        updated_at=pointer.updated_at.isoformat(),
    )


@router.get("/loops/{id}", response_model=LoopStatusResponse)
async def get_loop(
    id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> LoopStatusResponse:
    """Retrieve current operational status of an autonomous loop."""
    stmt_loop = select(AutonomyLoop).where(AutonomyLoop.id == id)
    loop = (await session.execute(stmt_loop)).scalar_one_or_none()
    if not loop:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Loop not found")

    stmt_ptr = select(AutonomyLoopLatestPointer).where(AutonomyLoopLatestPointer.loop_id == id)
    pointer = (await session.execute(stmt_ptr)).scalar_one()

    return LoopStatusResponse(
        id=loop.id,
        mission_id=loop.mission_id,
        channel_id=loop.channel_id,
        autonomy_level=loop.autonomy_level,
        operational_state=pointer.operational_state,
        current_iteration_sequence=pointer.current_iteration_sequence,
        current_iteration_id=pointer.current_iteration_id,
        created_at=loop.created_at.isoformat(),
        updated_at=pointer.updated_at.isoformat(),
    )


@router.get("/loops/{id}/iterations", response_model=list[IterationSummary])
async def list_loop_iterations(
    id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, le=100),
) -> list[IterationSummary]:
    """List iteration history for a loop in descending order."""
    stmt = (
        select(AutonomyLoopIteration)
        .where(AutonomyLoopIteration.loop_id == id)
        .order_by(desc(AutonomyLoopIteration.iteration_sequence))
        .limit(limit)
    )
    iterations = (await session.execute(stmt)).scalars().all()
    return [
        IterationSummary(
            id=it.id,
            iteration_sequence=it.iteration_sequence,
            state=it.state,
            started_at=it.started_at.isoformat(),
            completed_at=it.completed_at.isoformat() if it.completed_at else None,
            stop_reason=it.stop_reason,
        )
        for it in iterations
    ]


@router.get("/iterations/{id}", response_model=IterationDetailResponse)
async def get_iteration_details(
    id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> IterationDetailResponse:
    """Retrieve full audit detail for a single iteration."""
    stmt_it = select(AutonomyLoopIteration).where(AutonomyLoopIteration.id == id)
    it = (await session.execute(stmt_it)).scalar_one_or_none()
    if not it:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Iteration not found")

    stmt_obs = select(AutonomyObservationSnapshot).where(
        AutonomyObservationSnapshot.id == it.observation_snapshot_id
    )
    obs = (await session.execute(stmt_obs)).scalar_one()

    stmt_plan = select(AutonomyActionPlan).where(AutonomyActionPlan.iteration_id == it.id)
    plan = (await session.execute(stmt_plan)).scalar_one_or_none()

    attempts_data: list[dict[str, Any]] = []
    if plan:
        stmt_att = select(AutonomyActionAttempt).where(
            AutonomyActionAttempt.action_plan_id == plan.id
        )
        attempts = (await session.execute(stmt_att)).scalars().all()
        for att in attempts:
            attempts_data.append(
                {
                    "attempt_id": str(att.id),
                    "attempt_sequence": att.attempt_sequence,
                    "correlation_key": att.subsystem_correlation_key,
                }
            )

    return IterationDetailResponse(
        id=it.id,
        loop_id=it.loop_id,
        iteration_sequence=it.iteration_sequence,
        state=it.state,
        started_at=it.started_at.isoformat(),
        completed_at=it.completed_at.isoformat() if it.completed_at else None,
        stop_reason=it.stop_reason,
        observation=obs.raw_observation,
        action_plan={
            "action_type": plan.action_type,
            "risk_class": plan.risk_class,
            "target_subsystem": plan.target_subsystem,
            "estimated_cost_usd": str(plan.estimated_cost_usd),
            "requires_approval": plan.requires_approval,
            "advisory_rationale": plan.advisory_rationale,
        }
        if plan
        else None,
        attempts=attempts_data,
    )


@router.post("/loops/{id}/pause")
async def pause_loop(
    id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    reason: str = Query(default="Manual user pause"),
) -> dict[str, str]:
    """User command: pause an active autonomous loop."""
    try:
        await AutonomyLoopService.update_operational_state(
            session=session,
            loop_id=id,
            new_state=AutonomyLoopState.PAUSED,
            stop_reason=reason,
        )
        await session.commit()
    except IllegalStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "PAUSED", "loop_id": str(id)}


@router.post("/loops/{id}/resume")
async def resume_loop(
    id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """User command: resume a paused loop back to IDLE."""
    try:
        await AutonomyLoopService.update_operational_state(
            session=session,
            loop_id=id,
            new_state=AutonomyLoopState.IDLE,
        )
        await session.commit()
    except IllegalStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "RESUMED", "loop_id": str(id)}


@router.post("/loops/{id}/cancel")
async def cancel_loop(
    id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    reason: str = Query(default="Manual user cancellation"),
) -> dict[str, str]:
    """User command: terminate a loop permanently."""
    try:
        await AutonomyLoopService.update_operational_state(
            session=session,
            loop_id=id,
            new_state=AutonomyLoopState.CANCELLED,
            stop_reason=reason,
        )
        await session.commit()
    except IllegalStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "CANCELLED", "loop_id": str(id)}


@router.post("/loops/{id}/reset-failure")
async def reset_loop_failure(
    id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    reason: str = Query(default="Human diagnosis and failure reset"),
) -> dict[str, str]:
    """User command: reset a FAILED loop back to IDLE."""
    try:
        await AutonomyLoopService.reset_failure(
            session=session,
            loop_id=id,
            reset_reason=reason,
        )
        await session.commit()
    except IllegalStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"status": "RESET", "loop_id": str(id)}


# ── Approval Endpoints ──


@router.get("/approvals", response_model=list[ApprovalResponse])
async def list_approvals(
    session: Annotated[AsyncSession, Depends(get_db)],
    loop_id: UUID | None = None,
) -> list[ApprovalResponse]:
    """List pending human approval requests."""
    stmt = (
        select(AutonomyApprovalRequest)
        .order_by(desc(AutonomyApprovalRequest.requested_at))
        .limit(20)
    )
    if loop_id:
        stmt = stmt.where(AutonomyApprovalRequest.loop_id == loop_id)

    requests = (await session.execute(stmt)).scalars().all()
    results: list[ApprovalResponse] = []
    for req in requests:
        stmt_dec = (
            select(AutonomyApprovalDecision.decision)
            .where(AutonomyApprovalDecision.approval_request_id == req.id)
            .order_by(desc(AutonomyApprovalDecision.decision_sequence))
            .limit(1)
        )
        latest_dec = (await session.execute(stmt_dec)).scalar_one_or_none()
        results.append(
            ApprovalResponse(
                id=req.id,
                loop_id=req.loop_id,
                action_plan_id=req.action_plan_id,
                semantic_action_key=req.semantic_action_key,
                action_checksum=req.action_checksum,
                guardian_epoch=req.guardian_epoch,
                mission_state=req.mission_state,
                requested_at=req.requested_at.isoformat(),
                expires_at=req.expires_at.isoformat(),
                latest_decision=latest_dec,
            )
        )
    return results


@router.get("/approvals/{id}", response_model=ApprovalResponse)
async def get_approval(
    id: UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ApprovalResponse:
    """Retrieve single approval request ticket."""
    stmt = select(AutonomyApprovalRequest).where(AutonomyApprovalRequest.id == id)
    req = (await session.execute(stmt)).scalar_one_or_none()
    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Approval request not found"
        )

    stmt_dec = (
        select(AutonomyApprovalDecision.decision)
        .where(AutonomyApprovalDecision.approval_request_id == req.id)
        .order_by(desc(AutonomyApprovalDecision.decision_sequence))
        .limit(1)
    )
    latest_dec = (await session.execute(stmt_dec)).scalar_one_or_none()
    return ApprovalResponse(
        id=req.id,
        loop_id=req.loop_id,
        action_plan_id=req.action_plan_id,
        semantic_action_key=req.semantic_action_key,
        action_checksum=req.action_checksum,
        guardian_epoch=req.guardian_epoch,
        mission_state=req.mission_state,
        requested_at=req.requested_at.isoformat(),
        expires_at=req.expires_at.isoformat(),
        latest_decision=latest_dec,
    )


@router.post("/approvals/{id}/approve")
async def approve_request(
    id: UUID,
    payload: DecisionPayload,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """Human reviewer approves action for execution."""
    dec = await AutonomyApprovalService.decide_request(
        session=session,
        approval_request_id=id,
        decision=ApprovalDecisionValue.APPROVED,
        actor_type=ApprovalActorType.HUMAN,
        reviewer_user_id=payload.reviewer_user_id or "HUMAN_OPERATOR",
        review_reason=payload.review_reason,
    )
    await session.commit()
    return {"status": dec.decision, "approval_request_id": str(id)}


@router.post("/approvals/{id}/reject")
async def reject_request(
    id: UUID,
    payload: DecisionPayload,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """Human reviewer rejects action."""
    dec = await AutonomyApprovalService.decide_request(
        session=session,
        approval_request_id=id,
        decision=ApprovalDecisionValue.REJECTED,
        actor_type=ApprovalActorType.HUMAN,
        reviewer_user_id=payload.reviewer_user_id or "HUMAN_OPERATOR",
        review_reason=payload.review_reason,
    )
    await session.commit()
    return {"status": dec.decision, "approval_request_id": str(id)}
