"""Missions REST API endpoints.

Handles CRUD, planning, execution lifecycle, and inspection for Missions.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application.mission_service import (
    cancel_mission,
    create_mission,
    get_mission,
    get_mission_decisions,
    get_mission_tasks,
    list_missions,
    pause_mission,
    plan_mission,
    resume_mission,
    start_mission,
    update_mission,
)
from omega.domain.decision import DecisionLogResponse
from omega.domain.mission import (
    InvalidStateTransitionError,
    MissionCreate,
    MissionResponse,
    MissionUpdate,
)
from omega.domain.task import TaskResponse

router = APIRouter(prefix="/api/v1/missions", tags=["Missions"])


@router.post("", response_model=MissionResponse, status_code=status.HTTP_201_CREATED)
async def create_new_mission(
    payload: MissionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MissionResponse:
    """Create a new mission in DRAFT state."""
    try:
        return await create_mission(db, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get("", response_model=list[MissionResponse])
async def get_all_missions(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MissionResponse]:
    """List all missions with pagination."""
    return await list_missions(db, limit=limit, offset=offset)


@router.get("/{mission_id}", response_model=MissionResponse)
async def get_mission_by_id(
    mission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MissionResponse:
    """Get a mission by its ID."""
    mission = await get_mission(db, mission_id)
    if not mission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
    return mission


@router.patch("/{mission_id}", response_model=MissionResponse)
async def update_draft_mission(
    mission_id: UUID,
    payload: MissionUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MissionResponse:
    """Update a mission in DRAFT state."""
    try:
        mission = await update_mission(db, mission_id, payload)
        if not mission:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
        return mission
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{mission_id}/plan", response_model=MissionResponse)
async def plan_mission_dag(
    mission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MissionResponse:
    """Plan a mission: generate DAG, create planned execution, transition DRAFT -> READY."""
    try:
        mission = await plan_mission(db, mission_id)
        if not mission:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
        return mission
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{mission_id}/start", response_model=MissionResponse)
async def start_planned_mission(
    mission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MissionResponse:
    """Start a planned mission: activate execution, transition READY -> RUNNING, trigger orchestrator."""
    try:
        mission = await start_mission(db, mission_id)
        if not mission:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
        return mission
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/{mission_id}/pause", response_model=MissionResponse)
async def pause_active_mission(
    mission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MissionResponse:
    """Pause an active mission in RUNNING state."""
    try:
        mission = await pause_mission(db, mission_id)
        if not mission:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
        return mission
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{mission_id}/resume", response_model=MissionResponse)
async def resume_paused_mission(
    mission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MissionResponse:
    """Resume a paused mission: PAUSED -> RUNNING, trigger orchestrator."""
    try:
        mission = await resume_mission(db, mission_id)
        if not mission:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
        return mission
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{mission_id}/cancel", response_model=MissionResponse)
async def cancel_active_mission(
    mission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MissionResponse:
    """Cancel a mission and all its non-terminal tasks."""
    try:
        mission = await cancel_mission(db, mission_id)
        if not mission:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
        return mission
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{mission_id}/tasks", response_model=list[TaskResponse])
async def get_tasks_for_mission(
    mission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[TaskResponse]:
    """Retrieve all tasks in the mission DAG."""
    mission = await get_mission(db, mission_id)
    if not mission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
    return await get_mission_tasks(db, mission_id)


@router.get("/{mission_id}/decisions", response_model=list[DecisionLogResponse])
async def get_decisions_for_mission(
    mission_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[DecisionLogResponse]:
    """Retrieve all operational decision logs for a mission."""
    mission = await get_mission(db, mission_id)
    if not mission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
    return await get_mission_decisions(db, mission_id)
