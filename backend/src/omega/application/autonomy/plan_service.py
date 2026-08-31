"""Planning engine formulating deterministic, policy-bounded action plans for OMEGA-014."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.autonomy.helpers import (
    compute_action_checksum,
    compute_precondition_fingerprint,
)
from omega.domain.autonomy import (
    ActionRiskClass,
    ActionType,
    AutonomyLevel,
    AutonomyPlanProposal,
    StrategyMode,
)
from omega.infrastructure.models import (
    AutonomyActionPlan,
    AutonomyLoop,
    AutonomyLoopIteration,
    AutonomyObservationSnapshot,
    AutonomyPolicySnapshot,
)


class AutonomyPlanService:
    """Service formulating single authoritative action plans per iteration."""

    @classmethod
    async def create_plan(
        cls,
        session: AsyncSession,
        iteration_id: UUID,
        advisory_proposal: AutonomyPlanProposal | None = None,
    ) -> AutonomyActionPlan:
        """Formulate, validate, and persist an immutable action plan for an iteration."""
        # 1. Load iteration with observation snapshot and policy
        stmt_iter = select(AutonomyLoopIteration).where(AutonomyLoopIteration.id == iteration_id)
        iteration = (await session.execute(stmt_iter)).scalar_one()

        stmt_obs = select(AutonomyObservationSnapshot).where(
            AutonomyObservationSnapshot.id == iteration.observation_snapshot_id
        )
        obs = (await session.execute(stmt_obs)).scalar_one()

        stmt_pol = select(AutonomyPolicySnapshot).where(
            AutonomyPolicySnapshot.id == iteration.policy_snapshot_id
        )
        policy = (await session.execute(stmt_pol)).scalar_one()

        stmt_loop = select(AutonomyLoop).where(AutonomyLoop.id == iteration.loop_id)
        loop = (await session.execute(stmt_loop)).scalar_one()

        raw_obs = obs.raw_observation
        loop_level = AutonomyLevel(loop.autonomy_level)

        # 2. Determine Action via Deterministic Fallback Rules if no advisory proposal
        if advisory_proposal:
            action_type = advisory_proposal.action_type
            target_subsystem = advisory_proposal.target_subsystem
            target_entity_id = advisory_proposal.target_entity_id
            parameters = advisory_proposal.parameters
            rationale = advisory_proposal.rationale
            model_metadata = {
                "model_name": advisory_proposal.model_name,
                "strategy_mode": advisory_proposal.strategy_mode.value,
                "prompt_template_version": advisory_proposal.prompt_template_version,
                "prompt_checksum": advisory_proposal.prompt_checksum,
            }
        else:
            # Deterministic State Machine Heuristics
            gate_state = raw_obs.get("guardian_gate_state", "WAITING_GUARDIAN")
            if gate_state in ("BLOCKED", "WAITING_GUARDIAN"):
                action_type = ActionType.REQUEST_QA
                target_subsystem = "guardian"
                target_entity_id = loop.mission_id
                parameters = {"checkpoint": "PRE_TASK_DISPATCH"}
                rationale = "Guardian gate is not OPEN; requesting QA evaluation."
            elif raw_obs.get("ready_media_artifact_ids"):
                art_id = UUID(raw_obs["ready_media_artifact_ids"][0])
                action_type = ActionType.CREATE_PUBLISH_INTENT
                target_subsystem = "publisher"
                target_entity_id = art_id
                parameters = {"media_artifact_id": str(art_id)}
                rationale = "MediaArtifact is READY; creating PublishIntent."
            elif raw_obs.get("pending_publish_intent_ids"):
                intent_id = UUID(raw_obs["pending_publish_intent_ids"][0])
                action_type = ActionType.REQUEST_SCHEDULE
                target_subsystem = "scheduler"
                target_entity_id = intent_id
                parameters = {"publish_intent_id": str(intent_id)}
                rationale = "PublishIntent pending; allocating scheduled slot."
            elif raw_obs.get("current_brief_ids"):
                brief_id = UUID(raw_obs["current_brief_ids"][0])
                action_type = ActionType.GENERATE_CONTENT
                target_subsystem = "content"
                target_entity_id = brief_id
                parameters = {"research_brief_id": str(brief_id)}
                rationale = "ResearchBrief available; generating structured content."
            elif raw_obs.get("approved_topic_candidate_ids"):
                topic_id = UUID(raw_obs["approved_topic_candidate_ids"][0])
                action_type = ActionType.REQUEST_RESEARCH
                target_subsystem = "research"
                target_entity_id = topic_id
                parameters = {"topic_candidate_id": str(topic_id)}
                rationale = "Approved topic candidate found; requesting research brief."
            else:
                action_type = ActionType.WAIT
                target_subsystem = "autonomy"
                target_entity_id = None
                parameters = {"wait_duration_seconds": 60}
                rationale = "Pipeline has no ready items; waiting for upstream signals."

            model_metadata = {
                "model_name": "DETERMINISTIC_RULES",
                "strategy_mode": StrategyMode.EXPLOIT.value,
            }

        # 3. Categorize Risk Class
        if action_type in (
            ActionType.REFRESH_ANALYTICS,
            ActionType.SWEEP_LEARNING,
            ActionType.WAIT,
        ):
            risk_class = ActionRiskClass.READ_ONLY
        elif action_type in (
            ActionType.EVALUATE_TOPICS,
            ActionType.REQUEST_RESEARCH,
            ActionType.GENERATE_CONTENT,
            ActionType.REQUEST_PRODUCTION,
            ActionType.REQUEST_QA,
            ActionType.CREATE_PUBLISH_INTENT,
        ):
            risk_class = ActionRiskClass.REVERSIBLE_INTERNAL
        elif action_type in (
            ActionType.REQUEST_SCHEDULE,
            ActionType.REQUEST_PUBLISH,
            ActionType.COMPLETE_MISSION,
        ):
            risk_class = ActionRiskClass.EXTERNAL_SIDE_EFFECT
        else:
            risk_class = ActionRiskClass.IRREVERSIBLE_OR_HIGH_IMPACT

        # 4. Determine Approval Requirement
        if loop_level == AutonomyLevel.MANUAL:
            requires_approval = risk_class != ActionRiskClass.READ_ONLY
        elif loop_level == AutonomyLevel.SUPERVISED:
            requires_approval = risk_class == ActionRiskClass.EXTERNAL_SIDE_EFFECT
        else:  # BOUNDED_AUTONOMOUS
            requires_approval = False

        # 5. Estimate Cost
        if action_type == ActionType.GENERATE_CONTENT:
            estimated_cost = Decimal("0.5000")
        elif action_type == ActionType.REQUEST_PRODUCTION:
            estimated_cost = Decimal("1.0000")
        elif action_type == ActionType.REQUEST_RESEARCH:
            estimated_cost = Decimal("0.2500")
        else:
            estimated_cost = Decimal("0.0000")

        # 6. Cryptographic Action Checksum and Precondition Fingerprint
        action_checksum = compute_action_checksum(
            action_type=action_type.value,
            target_entity_id=target_entity_id,
            parameters=parameters,
            iteration_id=iteration.id,
        )

        entity_token = str(target_entity_id or "NONE")
        precondition_fingerprint = compute_precondition_fingerprint(
            action_type=action_type.value,
            target_entity_id=target_entity_id,
            entity_state_token=entity_token,
            guardian_context_checksum=obs.guardian_context_checksum,
            dna_revision_id=obs.dna_revision_id,
            policy_checksum=policy.policy_checksum,
            mission_state=raw_obs.get("mission_state", "ACTIVE"),
        )

        time_res = await session.execute(text("SELECT NOW()"))
        db_now = time_res.scalar()

        plan = AutonomyActionPlan(
            id=uuid4(),
            iteration_id=iteration.id,
            plan_sequence=1,
            action_type=action_type.value,
            risk_class=risk_class.value,
            target_subsystem=target_subsystem,
            target_entity_id=target_entity_id,
            parameters=parameters,
            estimated_cost_usd=estimated_cost,
            precondition_fingerprint=precondition_fingerprint,
            action_checksum=action_checksum,
            requires_approval=requires_approval,
            advisory_rationale=rationale,
            advisory_model_metadata=model_metadata,
            created_at=db_now,
        )
        session.add(plan)
        await session.flush()
        return plan
