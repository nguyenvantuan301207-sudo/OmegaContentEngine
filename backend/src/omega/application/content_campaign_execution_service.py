"""Canonical Content Campaign Execution Application Service.

Materializes immutable ContentCampaign authorities into bounded OMEGA Missions,
enforcing exact DNA pinning, atomic transaction boundaries, and historical lineage integrity.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.application.content_campaign_service import ContentCampaignNotFoundError
from omega.application.mission_service import (
    _create_mission_in_transaction,
    _plan_mission_in_transaction,
)
from omega.domain.content_campaign import normalize_planned_release_at
from omega.domain.content_campaign_execution import (
    FANOUT_DEPENDENCY_COUNT,
    FANOUT_MISSION_TRIGGER_TYPE,
    FANOUT_POLICY_NAME,
    FANOUT_POLICY_VERSION,
    FANOUT_PRIORITY_MAX,
    FANOUT_PRIORITY_MIN,
    FANOUT_STAGE_COUNT,
    ContentCampaignExecutionCreate,
    ContentCampaignExecutionResponse,
    ContentCampaignExecutionStatus,
    ContentCampaignItemExecutionResponse,
    compute_fanout_policy_checksum,
)
from omega.domain.mission import AutonomyLevel, MissionCreate, MissionTriggerType
from omega.infrastructure.models import (
    ContentCampaign,
    ContentCampaignExecution,
    ContentCampaignItemExecution,
    ContentSelectionDecision,
    Mission,
    MissionExecution,
    Task,
    TaskDependency,
)
from omega.logging import get_logger

logger = get_logger(service="omega-content-campaign-execution-service")

EXPECTED_STAGE_TYPES = {
    "strategy",
    "topic_discovery",
    "research",
    "content_generation",
    "production",
    "qa",
    "publish",
}


class ContentCampaignExecutionIntegrityError(Exception):
    """Raised when persisted campaign execution lineage or task handoff is corrupted."""


class ContentCampaignExecutionNotFoundError(Exception):
    """Raised when campaign execution does not exist."""


class ContentCampaignExecutionValidationError(ValueError):
    """Raised when execution parameters or campaign state fail validation."""


def _clamp_priority(priority: int) -> int:
    return max(FANOUT_PRIORITY_MIN, min(FANOUT_PRIORITY_MAX, priority))


async def _validate_historical_execution_integrity(
    session: AsyncSession,
    execution: ContentCampaignExecution,
    campaign: ContentCampaign,
) -> ContentCampaignExecutionResponse:
    """Validate full historical execution integrity and return canonical response.

    Validates:
    - Identity, channel lineage, DNA lineage, status, policy checksum
    - Exact binding set matching campaign items 1..N
    - Bound Mission and MissionExecution existence & ownership
    - Campaign lineage metadata matching item attributes
    - Planned task DAG seeds: topic_discovery candidate ID & content_generation content type
    Does NOT freeze runtime Mission/MissionExecution state to allow future lifecycle transitions.
    """
    if execution.campaign_id != campaign.id:
        raise ContentCampaignExecutionIntegrityError(
            f"Execution campaign_id '{execution.campaign_id}' does not match campaign '{campaign.id}'."
        )
    if execution.channel_id != campaign.channel_id:
        raise ContentCampaignExecutionIntegrityError(
            f"Execution channel_id '{execution.channel_id}' does not match campaign '{campaign.channel_id}'."
        )
    if execution.channel_dna_revision_id != campaign.channel_dna_revision_id:
        raise ContentCampaignExecutionIntegrityError(
            f"Execution channel_dna_revision_id '{execution.channel_dna_revision_id}' does not match campaign '{campaign.channel_dna_revision_id}'."
        )
    if execution.status != ContentCampaignExecutionStatus.MATERIALIZED.value:
        raise ContentCampaignExecutionIntegrityError(
            f"Execution status '{execution.status}' must be MATERIALIZED."
        )

    expected_checksum = compute_fanout_policy_checksum()
    if execution.fanout_policy_checksum != expected_checksum:
        raise ContentCampaignExecutionIntegrityError(
            f"Execution fanout_policy_checksum '{execution.fanout_policy_checksum}' does not match current policy '{expected_checksum}'."
        )

    # Load campaign items in position order
    sorted_campaign_items = sorted(campaign.items, key=lambda it: it.position)
    if execution.item_count != len(sorted_campaign_items):
        raise ContentCampaignExecutionIntegrityError(
            f"Execution item_count {execution.item_count} does not match campaign item count {len(sorted_campaign_items)}."
        )

    # Load bindings with mission and execution
    binding_res = await session.execute(
        select(ContentCampaignItemExecution)
        .where(ContentCampaignItemExecution.campaign_execution_id == execution.id)
        .order_by(ContentCampaignItemExecution.position.asc())
        .options(
            selectinload(ContentCampaignItemExecution.mission),
            selectinload(ContentCampaignItemExecution.mission_execution),
        )
    )
    bindings = list(binding_res.scalars().all())

    if len(bindings) != len(sorted_campaign_items):
        raise ContentCampaignExecutionIntegrityError(
            f"Persisted bindings count {len(bindings)} does not match campaign item count {len(sorted_campaign_items)}."
        )

    response_items: list[ContentCampaignItemExecutionResponse] = []

    for idx, (campaign_item, binding) in enumerate(zip(sorted_campaign_items, bindings, strict=True)):
        expected_pos = idx + 1
        if binding.position != expected_pos or campaign_item.position != expected_pos:
            raise ContentCampaignExecutionIntegrityError(
                f"Binding position mismatch at index {idx}: binding.position={binding.position}, campaign_item.position={campaign_item.position}, expected={expected_pos}."
            )
        if binding.campaign_item_id != campaign_item.id:
            raise ContentCampaignExecutionIntegrityError(
                f"Binding campaign_item_id '{binding.campaign_item_id}' does not match campaign item '{campaign_item.id}'."
            )

        mission = binding.mission
        mission_exec = binding.mission_execution

        if not mission:
            raise ContentCampaignExecutionIntegrityError(
                f"Bound Mission '{binding.mission_id}' not found for binding '{binding.id}'."
            )
        if not mission_exec:
            raise ContentCampaignExecutionIntegrityError(
                f"Bound MissionExecution '{binding.mission_execution_id}' not found for binding '{binding.id}'."
            )

        if mission.channel_id != campaign.channel_id:
            raise ContentCampaignExecutionIntegrityError(
                f"Mission '{mission.id}' channel_id '{mission.channel_id}' does not match campaign '{campaign.channel_id}'."
            )
        if mission_exec.mission_id != mission.id:
            raise ContentCampaignExecutionIntegrityError(
                f"MissionExecution '{mission_exec.id}' mission_id '{mission_exec.mission_id}' does not match mission '{mission.id}'."
            )
        if mission_exec.channel_dna_revision_id != campaign.channel_dna_revision_id:
            raise ContentCampaignExecutionIntegrityError(
                f"MissionExecution '{mission_exec.id}' channel_dna_revision_id '{mission_exec.channel_dna_revision_id}' does not match pinned campaign revision '{campaign.channel_dna_revision_id}'."
            )
        if mission_exec.trigger_type != FANOUT_MISSION_TRIGGER_TYPE:
            raise ContentCampaignExecutionIntegrityError(
                f"MissionExecution '{mission_exec.id}' trigger_type '{mission_exec.trigger_type}' must be {FANOUT_MISSION_TRIGGER_TYPE}."
            )

        # Validate mission campaign_lineage metadata
        meta = mission.metadata_ or {}
        lineage = meta.get("campaign_lineage")
        if not isinstance(lineage, dict):
            raise ContentCampaignExecutionIntegrityError(
                f"Mission '{mission.id}' missing campaign_lineage object in metadata."
            )

        norm_rel = normalize_planned_release_at(campaign_item.planned_release_at)
        expected_lineage = {
            "campaign_id": str(campaign.id),
            "campaign_item_id": str(campaign_item.id),
            "campaign_execution_id": str(execution.id),
            "position": campaign_item.position,
            "selection_run_id": str(campaign_item.selection_run_id),
            "selection_decision_id": str(campaign_item.selection_decision_id),
            "topic_candidate_id": str(campaign_item.topic_candidate_id),
            "target_content_type": campaign_item.target_content_type,
            "planned_release_at": norm_rel,
        }

        for key, expected_val in expected_lineage.items():
            actual_val = lineage.get(key)
            if actual_val != expected_val:
                raise ContentCampaignExecutionIntegrityError(
                    f"Mission '{mission.id}' campaign_lineage field '{key}' mismatch: expected '{expected_val}', got '{actual_val}'."
                )

        # Validate planned task seeds for this execution
        tasks_res = await session.execute(
            select(Task)
            .where(
                Task.execution_id == mission_exec.id,
                Task.mission_id == mission.id,
            )
        )
        tasks = list(tasks_res.scalars().all())

        topic_tasks = [t for t in tasks if t.task_type == "topic_discovery"]
        if len(topic_tasks) != 1:
            raise ContentCampaignExecutionIntegrityError(
                f"Mission '{mission.id}' execution '{mission_exec.id}' has {len(topic_tasks)} topic_discovery tasks; expected exactly 1."
            )
        td_input = topic_tasks[0].input or {}
        if td_input.get("topic_candidate_id") != str(campaign_item.topic_candidate_id):
            raise ContentCampaignExecutionIntegrityError(
                f"Mission '{mission.id}' topic_discovery task input topic_candidate_id '{td_input.get('topic_candidate_id')}' does not match campaign item candidate '{campaign_item.topic_candidate_id}'."
            )

        content_tasks = [t for t in tasks if t.task_type == "content_generation"]
        if len(content_tasks) != 1:
            raise ContentCampaignExecutionIntegrityError(
                f"Mission '{mission.id}' execution '{mission_exec.id}' has {len(content_tasks)} content_generation tasks; expected exactly 1."
            )
        cg_input = content_tasks[0].input or {}
        canonical_content = cg_input.get("canonical_content")
        if not isinstance(canonical_content, dict) or canonical_content.get("content_type") != campaign_item.target_content_type:
            raise ContentCampaignExecutionIntegrityError(
                f"Mission '{mission.id}' content_generation task input content_type '{canonical_content}' does not match campaign item target '{campaign_item.target_content_type}'."
            )

        response_items.append(
            ContentCampaignItemExecutionResponse(
                id=binding.id,
                campaign_execution_id=binding.campaign_execution_id,
                campaign_item_id=binding.campaign_item_id,
                position=binding.position,
                mission_id=binding.mission_id,
                mission_execution_id=binding.mission_execution_id,
                mission_state=mission.state,
                mission_execution_state=mission_exec.state,
                created_at=binding.created_at,
            )
        )

    return ContentCampaignExecutionResponse(
        id=execution.id,
        campaign_id=execution.campaign_id,
        channel_id=execution.channel_id,
        channel_dna_revision_id=execution.channel_dna_revision_id,
        status=execution.status,
        fanout_policy_name=execution.fanout_policy_name,
        fanout_policy_version=execution.fanout_policy_version,
        fanout_policy_checksum=execution.fanout_policy_checksum,
        item_count=execution.item_count,
        materialized_by=execution.materialized_by,
        created_at=execution.created_at,
        items=response_items,
    )


async def get_campaign_execution(
    session: AsyncSession,
    *,
    channel_id: UUID,
    campaign_id: UUID,
) -> ContentCampaignExecutionResponse:
    """Retrieve and validate historical campaign execution lineage."""
    campaign_res = await session.execute(
        select(ContentCampaign)
        .where(
            ContentCampaign.id == campaign_id,
            ContentCampaign.channel_id == channel_id,
        )
        .options(selectinload(ContentCampaign.items))
    )
    campaign = campaign_res.scalar_one_or_none()
    if not campaign:
        raise ContentCampaignNotFoundError(
            f"Campaign '{campaign_id}' not found for channel '{channel_id}'."
        )

    exec_res = await session.execute(
        select(ContentCampaignExecution).where(
            ContentCampaignExecution.campaign_id == campaign_id,
            ContentCampaignExecution.channel_id == channel_id,
        )
    )
    execution = exec_res.scalar_one_or_none()
    if not execution:
        raise ContentCampaignExecutionNotFoundError(
            f"No execution found for campaign '{campaign_id}'."
        )

    return await _validate_historical_execution_integrity(session, execution, campaign)


async def materialize_campaign(
    session: AsyncSession,
    *,
    channel_id: UUID,
    campaign_id: UUID,
    payload: ContentCampaignExecutionCreate,
) -> tuple[ContentCampaignExecutionResponse, bool]:
    """Materialize an immutable ContentCampaign into OMEGA Missions.

    Returns (response, created) tuple:
    - (response, True) if newly materialized
    - (response, False) if replayed existing materialization
    """
    # 1. Campaign row lock
    campaign_res = await session.execute(
        select(ContentCampaign)
        .where(
            ContentCampaign.id == campaign_id,
            ContentCampaign.channel_id == channel_id,
        )
        .options(selectinload(ContentCampaign.items))
        .with_for_update()
    )
    campaign = campaign_res.scalar_one_or_none()
    if not campaign:
        raise ContentCampaignNotFoundError(
            f"Campaign '{campaign_id}' not found for channel '{channel_id}'."
        )

    # 2. Check for existing materialization (replay)
    exec_res = await session.execute(
        select(ContentCampaignExecution).where(
            ContentCampaignExecution.campaign_id == campaign_id,
            ContentCampaignExecution.channel_id == channel_id,
        )
    )
    existing_execution = exec_res.scalar_one_or_none()
    if existing_execution:
        logger.info(
            "Campaign already materialized; validating historical integrity for replay",
            campaign_id=str(campaign_id),
            execution_id=str(existing_execution.id),
        )
        validated_resp = await _validate_historical_execution_integrity(
            session, existing_execution, campaign
        )
        return validated_resp, False

    # 3. Validate campaign items
    sorted_items = sorted(campaign.items, key=lambda it: it.position)
    if not sorted_items:
        raise ContentCampaignExecutionValidationError(
            f"Campaign '{campaign_id}' has no items to materialize."
        )

    # 4. Prepare materialization parameters
    execution_id = uuid4()
    policy_checksum = compute_fanout_policy_checksum()
    clamped_priority = _clamp_priority(campaign.priority)
    actor = payload.actor

    campaign_execution = ContentCampaignExecution(
        id=execution_id,
        campaign_id=campaign.id,
        channel_id=campaign.channel_id,
        channel_dna_revision_id=campaign.channel_dna_revision_id,
        status=ContentCampaignExecutionStatus.MATERIALIZED.value,
        fanout_policy_name=FANOUT_POLICY_NAME,
        fanout_policy_version=FANOUT_POLICY_VERSION,
        fanout_policy_checksum=policy_checksum,
        item_count=len(sorted_items),
        materialized_by=actor,
    )
    session.add(campaign_execution)
    await session.flush()

    # 5. Process items in position order
    item_executions: list[ContentCampaignItemExecution] = []
    created_missions: list[Mission] = []
    created_mission_executions: list[MissionExecution] = []

    for item in sorted_items:
        # Resolve candidate title snapshot from immutable decision
        decision_res = await session.execute(
            select(ContentSelectionDecision).where(
                ContentSelectionDecision.id == item.selection_decision_id
            )
        )
        decision = decision_res.scalar_one_or_none()
        if not decision:
            raise ContentCampaignExecutionIntegrityError(
                f"Selection decision '{item.selection_decision_id}' for item '{item.id}' not found."
            )
        candidate_title = decision.candidate_title_snapshot

        mission_title = f"[{campaign.title}] {candidate_title}"[:255]
        mission_objective = (
            f"Execute content pipeline for campaign '{campaign.title}' item #{item.position} "
            f"targeting {item.target_content_type} based on '{candidate_title}'."
        )[:2000]

        norm_rel = normalize_planned_release_at(item.planned_release_at)
        mission_metadata = {
            "campaign_lineage": {
                "campaign_id": str(campaign.id),
                "campaign_item_id": str(item.id),
                "campaign_execution_id": str(execution_id),
                "position": item.position,
                "selection_run_id": str(item.selection_run_id),
                "selection_decision_id": str(item.selection_decision_id),
                "topic_candidate_id": str(item.topic_candidate_id),
                "target_content_type": item.target_content_type,
                "planned_release_at": norm_rel,
            },
            "canonical_inputs": {
                "topic_candidate_id": str(item.topic_candidate_id),
                "content": {
                    "content_type": item.target_content_type,
                },
            },
        }

        mission_in = MissionCreate(
            title=mission_title,
            objective=mission_objective,
            channel_id=campaign.channel_id,
            description=f"Materialized mission for campaign {campaign.id} item #{item.position}",
            autonomy_level=AutonomyLevel.SUPERVISED,
            priority=clamped_priority,
            metadata=mission_metadata,
        )

        mission = await _create_mission_in_transaction(session, mission_in)
        mission, mission_exec = await _plan_mission_in_transaction(
            session,
            mission,
            channel_dna_revision_id=campaign.channel_dna_revision_id,
            trigger_type=MissionTriggerType.API,
        )

        # Verify initial materialization contract for each mission
        if mission.state != "READY" or mission.started_at is not None:
            raise ContentCampaignExecutionIntegrityError(
                f"Mission '{mission.id}' state must be READY with started_at=NULL at materialization; got state='{mission.state}'."
            )
        if mission_exec.state != "PLANNED" or mission_exec.started_at is not None:
            raise ContentCampaignExecutionIntegrityError(
                f"MissionExecution '{mission_exec.id}' state must be PLANNED with started_at=NULL at materialization; got state='{mission_exec.state}'."
            )

        # Verify planner structural integrity: 7 tasks, 6 dependencies, SUPERVISED QA approval
        tasks_res = await session.execute(
            select(Task).where(Task.execution_id == mission_exec.id)
        )
        plan_tasks = list(tasks_res.scalars().all())
        if len(plan_tasks) != FANOUT_STAGE_COUNT:
            raise ContentCampaignExecutionIntegrityError(
                f"Mission '{mission.id}' generated {len(plan_tasks)} tasks; expected {FANOUT_STAGE_COUNT}."
            )
        task_types = [t.task_type for t in plan_tasks]
        if set(task_types) != EXPECTED_STAGE_TYPES or len(task_types) != len(set(task_types)):
            raise ContentCampaignExecutionIntegrityError(
                f"Mission '{mission.id}' task types {task_types} violate 7-stage topology."
            )

        qa_task = next(t for t in plan_tasks if t.task_type == "qa")
        if not qa_task.requires_approval:
            raise ContentCampaignExecutionIntegrityError(
                f"Mission '{mission.id}' QA task must require approval in SUPERVISED autonomy."
            )

        deps_res = await session.execute(
            select(TaskDependency).where(TaskDependency.mission_id == mission.id)
        )
        plan_deps = list(deps_res.scalars().all())
        if len(plan_deps) != FANOUT_DEPENDENCY_COUNT:
            raise ContentCampaignExecutionIntegrityError(
                f"Mission '{mission.id}' generated {len(plan_deps)} dependencies; expected {FANOUT_DEPENDENCY_COUNT}."
            )

        # Verify no dependency crosses mission boundary
        task_ids = {t.id for t in plan_tasks}
        for dep in plan_deps:
            if dep.task_id not in task_ids or dep.depends_on_task_id not in task_ids:
                raise ContentCampaignExecutionIntegrityError(
                    f"Dependency '{dep.id}' crosses mission boundaries for mission '{mission.id}'."
                )

        item_exec = ContentCampaignItemExecution(
            id=uuid4(),
            campaign_execution_id=campaign_execution.id,
            campaign_item_id=item.id,
            position=item.position,
            mission_id=mission.id,
            mission_execution_id=mission_exec.id,
        )
        session.add(item_exec)
        item_executions.append(item_exec)
        created_missions.append(mission)
        created_mission_executions.append(mission_exec)

    await session.flush()

    # 6. Commit atomic materialization
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        logger.warning(
            "IntegrityError during campaign materialization; reconciling concurrent winner",
            campaign_id=str(campaign_id),
            error=str(exc),
        )
        winner_res = await session.execute(
            select(ContentCampaignExecution).where(
                ContentCampaignExecution.campaign_id == campaign_id,
                ContentCampaignExecution.channel_id == channel_id,
            )
        )
        winner = winner_res.scalar_one_or_none()
        if not winner:
            raise ContentCampaignExecutionIntegrityError(
                f"IntegrityError encountered but no winner execution found for campaign '{campaign_id}'."
            ) from exc

        validated_winner = await _validate_historical_execution_integrity(session, winner, campaign)
        return validated_winner, False

    logger.info(
        "ContentCampaign materialized successfully into OMEGA missions",
        campaign_id=str(campaign.id),
        execution_id=str(campaign_execution.id),
        item_count=len(item_executions),
        dna_revision_id=str(campaign.channel_dna_revision_id),
    )

    # 7. Reload and validate freshly committed execution
    return await get_campaign_execution(session, channel_id=channel_id, campaign_id=campaign_id), True
