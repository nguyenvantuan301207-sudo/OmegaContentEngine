"""FastAPI router for OMEGA P20-A Durable Recurring Schedules.

Mounted at /api/v1/schedules.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application.scheduler.recurring_scheduler_service import (
    RecurringSchedulerService,
    SchedulerFeatureDisabledError,
    SchedulerRuntimeError,
)
from omega.domain.recurring_schedule import (
    RecurringScheduleCreate,
    RecurringScheduleResponse,
    RecurringScheduleUpdate,
    TimelinePreviewRequest,
    TimelinePreviewResponse,
)

router = APIRouter(prefix="/api/v1/schedules", tags=["recurring-schedules"])

DBSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=list[RecurringScheduleResponse])
async def list_schedules(
    session: DBSession,
    status_filter: Annotated[str | None, Query(alias="status", description="Filter by status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RecurringScheduleResponse]:
    """List recurring schedules."""
    return await RecurringSchedulerService.list_schedules(
        session, status=status_filter, limit=limit, offset=offset
    )


@router.post("", response_model=RecurringScheduleResponse, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    session: DBSession,
    payload: RecurringScheduleCreate,
) -> RecurringScheduleResponse:
    """Create a new recurring schedule in safe DRAFT status."""
    try:
        return await RecurringSchedulerService.create_draft_schedule(session, payload)
    except SchedulerRuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/preview", response_model=TimelinePreviewResponse)
async def preview_schedule_timeline(
    payload: TimelinePreviewRequest,
) -> TimelinePreviewResponse:
    """Preview next N occurrence timestamps without persisting anything."""
    try:
        return RecurringSchedulerService.preview_timeline(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{schedule_id}", response_model=RecurringScheduleResponse)
async def get_schedule(
    schedule_id: UUID,
    session: DBSession,
) -> RecurringScheduleResponse:
    """Retrieve a single recurring schedule by ID."""
    res = await RecurringSchedulerService.get_schedule(session, schedule_id)
    if res is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return res


@router.patch("/{schedule_id}", response_model=RecurringScheduleResponse)
async def edit_schedule(
    schedule_id: UUID,
    payload: RecurringScheduleUpdate,
    session: DBSession,
) -> RecurringScheduleResponse:
    """Edit a schedule by creating a new immutable version snapshot."""
    try:
        return await RecurringSchedulerService.edit_schedule(session, schedule_id, payload)
    except SchedulerRuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{schedule_id}/activate", response_model=RecurringScheduleResponse)
async def activate_schedule(
    schedule_id: UUID,
    session: DBSession,
) -> RecurringScheduleResponse:
    """Activate a schedule. Blocked with 409 Conflict if RECURRING_SCHEDULER_ENABLED=false."""
    try:
        return await RecurringSchedulerService.activate_schedule(session, schedule_id)
    except SchedulerFeatureDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SchedulerRuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{schedule_id}/pause", response_model=RecurringScheduleResponse)
async def pause_schedule(
    schedule_id: UUID,
    session: DBSession,
) -> RecurringScheduleResponse:
    """Pause an active schedule."""
    try:
        return await RecurringSchedulerService.pause_schedule(session, schedule_id)
    except SchedulerRuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{schedule_id}/resume", response_model=RecurringScheduleResponse)
async def resume_schedule(
    schedule_id: UUID,
    session: DBSession,
) -> RecurringScheduleResponse:
    """Resume a paused schedule forward from resume time."""
    try:
        return await RecurringSchedulerService.resume_schedule(session, schedule_id)
    except SchedulerFeatureDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SchedulerRuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{schedule_id}/cancel", response_model=RecurringScheduleResponse)
async def cancel_schedule(
    schedule_id: UUID,
    session: DBSession,
) -> RecurringScheduleResponse:
    """Cancel a schedule permanently."""
    try:
        return await RecurringSchedulerService.cancel_schedule(session, schedule_id)
    except SchedulerRuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
