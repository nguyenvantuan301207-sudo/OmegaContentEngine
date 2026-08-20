"""Tasks REST API endpoints.

Handles task inspection, pre-execution approval, and rejection.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from omega.api.dependencies import get_db
from omega.application.task_service import approve_task, get_task, reject_task
from omega.domain.mission import InvalidStateTransitionError
from omega.domain.task import TaskRejectRequest, TaskResponse

router = APIRouter(prefix="/api/v1/tasks", tags=["Tasks"])


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task_by_id(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TaskResponse:
    """Get a task by its ID."""
    task = await get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.post("/{task_id}/approve", response_model=TaskResponse)
async def approve_waiting_task(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TaskResponse:
    """Approve a task waiting at a pre-execution approval gate.

    Transitions WAITING_APPROVAL -> READY (unblocks task for orchestrator dispatch).
    """
    try:
        task = await approve_task(db, task_id)
        if not task:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        return task
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{task_id}/reject", response_model=TaskResponse)
async def reject_waiting_task(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    payload: TaskRejectRequest | None = None,
) -> TaskResponse:
    """Reject a task waiting at a pre-execution approval gate.

    Transitions WAITING_APPROVAL -> FAILED.
    """
    reason = payload.reason if payload else "Rejected by user"
    try:
        task = await reject_task(db, task_id, reason=reason)
        if not task:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        return task
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
