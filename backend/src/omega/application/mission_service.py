"""Mission Application Service.

Orchestrates Mission lifecycle, planning, start, pause, resume, cancel, and queries.
Integrates channel association, channel validation, and deterministic ChannelDNARevision pinning.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.orchestrator import evaluate_mission
from omega.application.planner import StaticMissionPlanner
from omega.domain.channel import ChannelState
from omega.domain.decision import Actor, DecisionLogResponse, DecisionType
from omega.domain.mission import (
    ExecutionState,
    MissionCreate,
    MissionResponse,
    MissionState,
    MissionTriggerType,
    MissionUpdate,
    validate_mission_transition,
)
from omega.domain.task import TaskResponse, TaskState
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    DecisionLog,
    Mission,
    MissionExecution,
    Task,
    TaskDependency,
)
from omega.logging import get_logger

logger = get_logger(service="omega-mission-service")


async def create_mission(session: AsyncSession, mission_in: MissionCreate) -> MissionResponse:
    """Create a new mission in DRAFT state with optional channel association."""
    # 1. Channel validation
    if mission_in.channel_id is not None:
        chan_res = await session.execute(select(Channel).where(Channel.id == mission_in.channel_id))
        channel = chan_res.scalar_one_or_none()
        if not channel:
            raise ValueError(f"Channel with ID '{mission_in.channel_id}' does not exist.")
        if channel.state == ChannelState.ARCHIVED.value:
            raise ValueError(f"Cannot create mission for archived channel '{channel.name}'.")

    mission = Mission(
        id=uuid4(),
        title=mission_in.title,
        objective=mission_in.objective,
        channel_id=mission_in.channel_id,
        description=mission_in.description,
        state=MissionState.DRAFT.value,
        autonomy_level=mission_in.autonomy_level.value,
        priority=mission_in.priority,
        metadata_=mission_in.metadata,
    )
    session.add(mission)
    await session.flush()

    # Record initial decision
    session.add(
        DecisionLog(
            mission_id=mission.id,
            decision_type=DecisionType.MISSION_PLAN.value,
            decision=f"Create draft mission: {mission.title}",
            reason="Mission initiated in DRAFT state by user",
            actor=Actor.USER.value,
        )
    )
    await session.commit()
    logger.info("Mission created", mission_id=str(mission.id), title=mission.title)
    return MissionResponse.model_validate(mission)


async def get_mission(session: AsyncSession, mission_id: UUID) -> MissionResponse | None:
    """Retrieve a mission by its ID."""
    res = await session.execute(select(Mission).where(Mission.id == mission_id))
    mission = res.scalar_one_or_none()
    return MissionResponse.model_validate(mission) if mission else None


async def list_missions(
    session: AsyncSession, limit: int = 100, offset: int = 0
) -> list[MissionResponse]:
    """List missions with pagination."""
    res = await session.execute(
        select(Mission).order_by(Mission.created_at.desc()).limit(limit).offset(offset)
    )
    missions = res.scalars().all()
    return [MissionResponse.model_validate(m) for m in missions]


async def update_mission(
    session: AsyncSession, mission_id: UUID, update_in: MissionUpdate
) -> MissionResponse | None:
    """Update a mission in DRAFT state. Enforces channel_id immutability once planned."""
    res = await session.execute(select(Mission).where(Mission.id == mission_id).with_for_update())
    mission = res.scalar_one_or_none()
    if not mission:
        return None

    if mission.state != MissionState.DRAFT.value:
        raise ValueError(f"Cannot update mission in state '{mission.state}'. Must be DRAFT.")

    # Enforce channel_id immutability if mission already has executions planned
    if update_in.channel_id != mission.channel_id and update_in.channel_id is not None:
        exec_check = await session.execute(
            select(MissionExecution).where(MissionExecution.mission_id == mission.id)
        )
        if exec_check.first() is not None:
            raise ValueError(
                "Mission channel_id cannot be modified after the mission has been planned or executed."
            )

        # Validate new channel
        chan_res = await session.execute(select(Channel).where(Channel.id == update_in.channel_id))
        channel = chan_res.scalar_one_or_none()
        if not channel:
            raise ValueError(f"Channel with ID '{update_in.channel_id}' does not exist.")
        if channel.state == ChannelState.ARCHIVED.value:
            raise ValueError(f"Cannot associate mission with archived channel '{channel.name}'.")
        mission.channel_id = update_in.channel_id

    if update_in.title is not None:
        mission.title = update_in.title
    if update_in.objective is not None:
        mission.objective = update_in.objective
    if update_in.description is not None:
        mission.description = update_in.description
    if update_in.autonomy_level is not None:
        mission.autonomy_level = update_in.autonomy_level.value
    if update_in.priority is not None:
        mission.priority = update_in.priority
    if update_in.metadata is not None:
        mission.metadata_ = update_in.metadata

    mission.updated_at = datetime.now(UTC)
    await session.commit()
    return MissionResponse.model_validate(mission)


async def plan_mission(session: AsyncSession, mission_id: UUID) -> MissionResponse | None:
    """Plan a mission: generate task DAG, freeze ChannelDNARevision, create planned MissionExecution, transition DRAFT -> READY."""
    res = await session.execute(select(Mission).where(Mission.id == mission_id).with_for_update())
    mission = res.scalar_one_or_none()
    if not mission:
        return None

    validate_mission_transition(MissionState(mission.state), MissionState.READY)

    # 1. Resolve Channel and latest ChannelDNARevision if linked to a channel
    channel_dna_revision_id = None
    if mission.channel_id is not None:
        chan_res = await session.execute(select(Channel).where(Channel.id == mission.channel_id))
        channel = chan_res.scalar_one_or_none()
        if not channel:
            raise ValueError(f"Linked channel with ID '{mission.channel_id}' does not exist.")
        if channel.state == ChannelState.ARCHIVED.value:
            raise ValueError(f"Cannot plan mission for archived channel '{channel.name}'.")

        rev_res = await session.execute(
            select(ChannelDNARevision)
            .where(ChannelDNARevision.channel_id == mission.channel_id)
            .order_by(ChannelDNARevision.version.desc())
        )
        latest_rev = rev_res.scalars().first()
        if latest_rev:
            channel_dna_revision_id = latest_rev.id

    # 2. Create the MissionExecution for this planned run, pinning the DNA revision
    execution = MissionExecution(
        id=uuid4(),
        mission_id=mission.id,
        channel_dna_revision_id=channel_dna_revision_id,
        state=ExecutionState.PLANNED.value,
        trigger_type=MissionTriggerType.MANUAL.value,
    )
    session.add(execution)
    await session.flush()

    # 3. Generate task DAG using StaticMissionPlanner
    planner = StaticMissionPlanner()
    plan = planner.plan(
        mission_title=mission.title,
        mission_objective=mission.objective,
        autonomy_level=mission.autonomy_level,
    )

    # 4. Persist planned tasks associated with this execution_id
    temp_id_to_real_id: dict[UUID, UUID] = {}
    for pt in plan.tasks:
        real_task_id = uuid4()
        temp_id_to_real_id[pt.temp_id] = real_task_id
        task_db = Task(
            id=real_task_id,
            mission_id=mission.id,
            execution_id=execution.id,
            task_type=pt.task_create.task_type,
            title=pt.task_create.title,
            description=pt.task_create.description,
            state=TaskState.PENDING.value,
            priority=pt.task_create.priority,
            requires_approval=pt.task_create.requires_approval,
            input=pt.task_create.input,
            max_retries=pt.task_create.max_retries,
        )
        session.add(task_db)

    await session.flush()

    # 5. Persist dependencies
    for pdep in plan.dependencies:
        dep_db = TaskDependency(
            id=uuid4(),
            mission_id=mission.id,
            task_id=temp_id_to_real_id[pdep.task_temp_id],
            depends_on_task_id=temp_id_to_real_id[pdep.depends_on_temp_id],
        )
        session.add(dep_db)

    # 6. Transition Mission DRAFT -> READY
    mission.state = MissionState.READY.value
    mission.updated_at = datetime.now(UTC)

    session.add(
        DecisionLog(
            mission_id=mission.id,
            execution_id=execution.id,
            decision_type=DecisionType.MISSION_PLAN.value,
            decision="Generate and validate 7-stage DAG",
            reason=f"StaticMissionPlanner generated {len(plan.tasks)} tasks and {len(plan.dependencies)} dependencies (pinned DNA rev: {channel_dna_revision_id})",
            actor=Actor.PLANNER.value,
        )
    )

    await session.commit()
    logger.info(
        "Mission planned successfully",
        mission_id=str(mission.id),
        tasks_count=len(plan.tasks),
        channel_dna_revision_id=str(channel_dna_revision_id) if channel_dna_revision_id else None,
    )
    return MissionResponse.model_validate(mission)


async def start_mission(session: AsyncSession, mission_id: UUID) -> MissionResponse | None:
    """Start a planned mission: activate planned MissionExecution, transition READY -> RUNNING, trigger orchestrator."""
    res = await session.execute(select(Mission).where(Mission.id == mission_id).with_for_update())
    mission = res.scalar_one_or_none()
    if not mission:
        return None

    validate_mission_transition(MissionState(mission.state), MissionState.RUNNING)

    # Channel check: verify linked channel is not archived
    if mission.channel_id is not None:
        chan_res = await session.execute(select(Channel).where(Channel.id == mission.channel_id))
        channel = chan_res.scalar_one_or_none()
        if channel and channel.state == ChannelState.ARCHIVED.value:
            raise ValueError(f"Cannot start mission for archived channel '{channel.name}'.")

    now = datetime.now(UTC)

    # 1. Retrieve the existing planned execution
    exec_res = await session.execute(
        select(MissionExecution)
        .where(
            MissionExecution.mission_id == mission.id,
            MissionExecution.state == ExecutionState.PLANNED.value,
        )
        .order_by(MissionExecution.created_at.desc())
        .with_for_update()
    )
    execution = exec_res.scalars().first()
    if execution:
        execution.state = ExecutionState.RUNNING.value
        execution.started_at = now
        execution.updated_at = now

    # 2. Transition Mission READY -> RUNNING
    mission.state = MissionState.RUNNING.value
    mission.started_at = now
    mission.updated_at = now

    session.add(
        DecisionLog(
            mission_id=mission.id,
            execution_id=execution.id if execution else None,
            decision_type=DecisionType.MISSION_START.value,
            decision="Start mission execution",
            reason="Mission initiated by user, activating planned execution DAG",
            actor=Actor.USER.value,
        )
    )

    await session.commit()
    logger.info("Mission started", mission_id=str(mission.id))

    # 3. Trigger initial Orchestrator evaluation
    await evaluate_mission(session, mission.id, execution.id if execution else None)

    # Reload fresh state
    fresh_res = await session.execute(select(Mission).where(Mission.id == mission_id))
    return MissionResponse.model_validate(fresh_res.scalar_one())


async def pause_mission(session: AsyncSession, mission_id: UUID) -> MissionResponse | None:
    """Pause an active mission."""
    res = await session.execute(select(Mission).where(Mission.id == mission_id).with_for_update())
    mission = res.scalar_one_or_none()
    if not mission:
        return None

    validate_mission_transition(MissionState(mission.state), MissionState.PAUSED)
    now = datetime.now(UTC)

    mission.guardian_epoch += 1
    mission.state = MissionState.PAUSED.value
    mission.paused_at = now
    mission.updated_at = now

    # Update active execution state
    exec_res = await session.execute(
        select(MissionExecution)
        .where(
            MissionExecution.mission_id == mission.id,
            MissionExecution.state == ExecutionState.RUNNING.value,
        )
        .with_for_update()
    )
    execution = exec_res.scalars().first()
    if execution:
        execution.state = ExecutionState.PAUSED.value
        execution.updated_at = now

    session.add(
        DecisionLog(
            mission_id=mission.id,
            execution_id=execution.id if execution else None,
            decision_type=DecisionType.MISSION_PAUSE.value,
            decision="Pause mission execution",
            reason="User requested execution pause; guardian epoch bumped to invalidate in-flight tasks",
            actor=Actor.USER.value,
        )
    )

    await session.commit()
    logger.info("Mission paused", mission_id=str(mission.id), guardian_epoch=mission.guardian_epoch)
    return MissionResponse.model_validate(mission)


async def resume_mission(
    session: AsyncSession,
    mission_id: UUID,
    actor: str = "OPERATOR",
    reason: str = "Operator requested safe resume",
) -> MissionResponse | None:
    """Resume a paused mission using two-phase safe resume protocol."""
    from uuid import uuid4

    from omega.application.guardian.engine import GuardianEngine
    from omega.domain.guardian import (
        CheckTriggerType,
        GuardianAction,
        GuardianCheckCreate,
        GuardianCheckpoint,
        GuardianGateState,
        GuardianResolutionType,
    )
    from omega.infrastructure.database import AsyncSessionLocal
    from omega.infrastructure.models import (
        GuardianCheck,
        GuardianDecision,
        GuardianResolutionEvent,
    )

    res = await session.execute(select(Mission).where(Mission.id == mission_id).with_for_update())
    mission = res.scalar_one_or_none()
    if not mission:
        return None

    validate_mission_transition(MissionState(mission.state), MissionState.RUNNING)

    # 1. Identify which checkpoints caused the block / pause
    stmt = (
        select(GuardianCheck.checkpoint, GuardianDecision.id)
        .join(GuardianDecision, GuardianDecision.guardian_check_id == GuardianCheck.id)
        .where(
            GuardianCheck.mission_id == mission_id,
            GuardianDecision.resulting_gate_state == GuardianGateState.BLOCKED.value,
        )
    )
    blocking_res = await session.execute(stmt)
    blocking_rows = list(blocking_res.all())
    blocking_cps = list({GuardianCheckpoint(r[0]) for r in blocking_rows})
    if not blocking_cps:
        blocking_cps = [GuardianCheckpoint.PRE_TASK_DISPATCH]

    # Find the latest decision for this mission to link to the resolution event
    dec_stmt = (
        select(GuardianDecision.id)
        .join(GuardianCheck, GuardianCheck.id == GuardianDecision.guardian_check_id)
        .where(GuardianCheck.mission_id == mission_id)
        .order_by(GuardianDecision.created_at.desc())
        .limit(1)
    )
    dec_res = await session.execute(dec_stmt)
    latest_decision_id = dec_res.scalar_one_or_none()

    # 2. Phase 1: Record and commit the resolution action BEFORE running resume re-checks
    now = datetime.now(UTC)
    if latest_decision_id:
        res_event = GuardianResolutionEvent(
            id=uuid4(),
            decision_id=latest_decision_id,
            resolution_type=GuardianResolutionType.OVERRIDE_APPROVED.value,
            actor=actor,
            reason=f"Resume requested: {reason}",
            created_at=now,
        )
        session.add(res_event)
    await session.commit()

    # 3. Phase 2: Run fresh Guardian checks for all blocking checkpoints
    guardian_engine = GuardianEngine(session_factory=AsyncSessionLocal)
    for cp in blocking_cps:
        check_res = await guardian_engine.execute_check(
            GuardianCheckCreate(
                mission_id=mission_id,
                checkpoint=cp,
                trigger_type=CheckTriggerType.RESUME_RECHECK,
                diagnostic_context={"resume_recheck": True, "actor": actor, "reason": reason},
                caller_key=f"resume:{str(uuid4())[:8]}",
            )
        )
        if not check_res.decision or check_res.decision.action not in (
            GuardianAction.ALLOW,
            GuardianAction.ALLOW_WITH_WARNING,
        ):
            # Record failed resume attempt
            if latest_decision_id:
                fail_event = GuardianResolutionEvent(
                    id=uuid4(),
                    decision_id=latest_decision_id,
                    resolution_type=GuardianResolutionType.OVERRIDE_APPROVED.value,
                    actor=actor,
                    reason=f"Resume recheck failed at {cp.value}: {check_res.decision.reason if check_res.decision else 'NO_DECISION'}",
                    created_at=datetime.now(UTC),
                )
                session.add(fail_event)
                await session.commit()
            raise ValueError(
                f"Safe resume blocked: checkpoint {cp.value} rejected with {check_res.decision.action.value if check_res.decision else 'NO_DECISION'}."
            )

    # 4. All rechecks passed: lock Mission and execute state transition under fresh epoch
    mission_res = await session.execute(
        select(Mission).where(Mission.id == mission_id).with_for_update()
    )
    mission = mission_res.scalar_one()

    now = datetime.now(UTC)
    mission.guardian_epoch += 1
    mission.state = MissionState.RUNNING.value
    mission.paused_at = None
    mission.updated_at = now

    exec_res = await session.execute(
        select(MissionExecution)
        .where(
            MissionExecution.mission_id == mission.id,
            MissionExecution.state == ExecutionState.PAUSED.value,
        )
        .with_for_update()
    )
    execution = exec_res.scalars().first()
    if execution:
        execution.state = ExecutionState.RUNNING.value
        execution.updated_at = now

    session.add(
        DecisionLog(
            mission_id=mission.id,
            execution_id=execution.id if execution else None,
            decision_type=DecisionType.MISSION_RESUME.value,
            decision=f"Resume mission execution at epoch {mission.guardian_epoch}",
            reason=f"All {len(blocking_cps)} blocking checkpoints verified. {reason}",
            actor=actor,
        )
    )

    await session.commit()
    logger.info(
        "Mission safely resumed", mission_id=str(mission.id), guardian_epoch=mission.guardian_epoch
    )

    # Trigger orchestrator evaluation
    await evaluate_mission(session, mission.id, execution.id if execution else None)

    fresh_res = await session.execute(select(Mission).where(Mission.id == mission_id))
    return MissionResponse.model_validate(fresh_res.scalar_one())


async def cancel_mission(session: AsyncSession, mission_id: UUID) -> MissionResponse | None:
    """Cancel a mission and all non-terminal tasks."""
    res = await session.execute(select(Mission).where(Mission.id == mission_id).with_for_update())
    mission = res.scalar_one_or_none()
    if not mission:
        return None

    validate_mission_transition(MissionState(mission.state), MissionState.CANCELLED)
    now = datetime.now(UTC)

    mission.state = MissionState.CANCELLED.value
    mission.cancelled_at = now
    mission.updated_at = now

    exec_res = await session.execute(
        select(MissionExecution)
        .where(
            MissionExecution.mission_id == mission.id,
            MissionExecution.state.in_(
                [
                    ExecutionState.PLANNED.value,
                    ExecutionState.RUNNING.value,
                    ExecutionState.PAUSED.value,
                ]
            ),
        )
        .with_for_update()
    )
    executions = list(exec_res.scalars().all())
    for exc in executions:
        exc.state = ExecutionState.CANCELLED.value
        exc.completed_at = now
        exc.updated_at = now

    # Cancel all active/pending tasks
    tasks_res = await session.execute(
        select(Task)
        .where(
            Task.mission_id == mission.id,
            Task.state.in_(
                [
                    TaskState.PENDING.value,
                    TaskState.BLOCKED.value,
                    TaskState.READY.value,
                    TaskState.WAITING_APPROVAL.value,
                    TaskState.QUEUED.value,
                ]
            ),
        )
        .with_for_update()
    )
    for t in tasks_res.scalars().all():
        t.state = TaskState.CANCELLED.value
        t.updated_at = now

    session.add(
        DecisionLog(
            mission_id=mission.id,
            decision_type=DecisionType.MISSION_CANCEL.value,
            decision="Cancel mission",
            reason="User cancelled mission; all non-terminal tasks cancelled",
            actor=Actor.USER.value,
        )
    )

    await session.commit()
    logger.info("Mission cancelled", mission_id=str(mission.id))
    return MissionResponse.model_validate(mission)


async def get_mission_tasks(session: AsyncSession, mission_id: UUID) -> list[TaskResponse]:
    """Retrieve all tasks for a mission ordered by priority/creation."""
    res = await session.execute(
        select(Task)
        .where(Task.mission_id == mission_id)
        .order_by(Task.priority.asc(), Task.created_at.asc())
    )
    tasks = res.scalars().all()
    return [TaskResponse.model_validate(t) for t in tasks]


async def get_mission_decisions(
    session: AsyncSession, mission_id: UUID
) -> list[DecisionLogResponse]:
    """Retrieve all decision logs for a mission."""
    res = await session.execute(
        select(DecisionLog)
        .where(DecisionLog.mission_id == mission_id)
        .order_by(DecisionLog.created_at.asc())
    )
    decisions = res.scalars().all()
    return [DecisionLogResponse.model_validate(d) for d in decisions]
