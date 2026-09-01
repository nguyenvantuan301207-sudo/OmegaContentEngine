"""Task Application Service.

Handles task retrieval and human approval/rejection gates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.orchestrator import evaluate_mission
from omega.domain.decision import Actor, DecisionType
from omega.domain.publisher import PublishIntentState
from omega.domain.task import TaskResponse, TaskState, validate_task_transition
from omega.infrastructure.models import DecisionLog, PublishIntent, PublishIntentTransition, Task
from omega.logging import get_logger

logger = get_logger(service="omega-task-service")


async def get_task(session: AsyncSession, task_id: UUID) -> TaskResponse | None:
    """Retrieve a task by ID."""
    res = await session.execute(select(Task).where(Task.id == task_id))
    task = res.scalar_one_or_none()
    return TaskResponse.model_validate(task) if task else None


async def approve_task(session: AsyncSession, task_id: UUID) -> TaskResponse | None:
    """Approve a task waiting at a pre-execution approval gate.

    Transitions WAITING_APPROVAL -> READY (unblocks for orchestrator dispatch).
    Does NOT mark task as succeeded.
    """
    res = await session.execute(select(Task).where(Task.id == task_id).with_for_update())
    task = res.scalar_one_or_none()
    if not task:
        return None

    if task.state != TaskState.WAITING_APPROVAL.value:
        raise ValueError(
            f"Cannot approve task in state '{task.state}'. Task must be in WAITING_APPROVAL state."
        )

    validate_task_transition(TaskState(task.state), TaskState.READY)

    task.state = TaskState.READY.value
    task.updated_at = datetime.now(UTC)

    session.add(
        DecisionLog(
            mission_id=task.mission_id,
            execution_id=task.execution_id,
            task_id=task.id,
            decision_type=DecisionType.TASK_APPROVED.value,
            decision=f"Approve task '{task.title}'",
            reason="Human pre-execution approval granted; task unblocked for dispatch",
            actor=Actor.USER.value,
        )
    )

    # Also transition any linked DRAFT PublishIntents for this task to APPROVED
    intents_res = await session.execute(
        select(PublishIntent)
        .where(
            PublishIntent.task_id == task_id,
            PublishIntent.state == PublishIntentState.DRAFT.value,
        )
        .with_for_update()
    )
    for intent in intents_res.scalars().all():
        intent.state = PublishIntentState.APPROVED.value
        intent.updated_at = datetime.now(UTC)
        session.add(
            PublishIntentTransition(
                id=uuid4(),
                publish_intent_id=intent.id,
                from_state=PublishIntentState.DRAFT.value,
                to_state=PublishIntentState.APPROVED.value,
                reason="Human pre-execution approval granted via task approval.",
                actor=Actor.USER.value,
            )
        )

    await session.commit()
    logger.info("Task approved by user", task_id=str(task.id), mission_id=str(task.mission_id))

    # Trigger orchestrator evaluation to dispatch the approved task
    await evaluate_mission(session, task.mission_id, task.execution_id)

    fresh_res = await session.execute(select(Task).where(Task.id == task_id))
    return TaskResponse.model_validate(fresh_res.scalar_one())


async def reject_task(
    session: AsyncSession, task_id: UUID, reason: str = "Rejected by user"
) -> TaskResponse | None:
    """Reject a task waiting at a pre-execution approval gate.

    Transitions WAITING_APPROVAL -> FAILED.
    """
    res = await session.execute(select(Task).where(Task.id == task_id).with_for_update())
    task = res.scalar_one_or_none()
    if not task:
        return None

    if task.state != TaskState.WAITING_APPROVAL.value:
        raise ValueError(
            f"Cannot reject task in state '{task.state}'. Task must be in WAITING_APPROVAL state."
        )

    validate_task_transition(TaskState(task.state), TaskState.FAILED)

    now = datetime.now(UTC)
    task.state = TaskState.FAILED.value
    task.error = f"Rejected by user: {reason}"
    task.completed_at = now
    task.updated_at = now

    session.add(
        DecisionLog(
            mission_id=task.mission_id,
            execution_id=task.execution_id,
            task_id=task.id,
            decision_type=DecisionType.TASK_REJECTED.value,
            decision=f"Reject task '{task.title}'",
            reason=f"Human pre-execution rejection: {reason}",
            actor=Actor.USER.value,
        )
    )

    await session.commit()
    logger.info("Task rejected by user", task_id=str(task.id), reason=reason)

    # Trigger orchestrator evaluation to handle failure / DAG state
    await evaluate_mission(session, task.mission_id, task.execution_id)

    fresh_res = await session.execute(select(Task).where(Task.id == task_id))
    return TaskResponse.model_validate(fresh_res.scalar_one())
