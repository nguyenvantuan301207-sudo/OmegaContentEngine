"""Bind content generation to the existing PostgreSQL narrative authority."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from omega.application.narrative_plan_service import (
    NarrativePlanService,
    PostgresNarrativePlanRepository,
)
from omega.application.narrative_plan_validator import NarrativePlanValidator
from omega.application.narrative_planning_service import NarrativePlanningService
from omega.application.narrative_qa_service import NarrativeQAService, StructuralQAEvaluator
from omega.application.narrative_script_adapter import NarrativePlanScriptAdapter
from omega.domain.narrative_plan import NarrativeFormatProfile, NarrativePlan, NarrativePlanStatus
from omega.domain.narrative_qa import NarrativeQAGateError
from omega.infrastructure.models import ContentGenerationRequest
from omega.infrastructure.models import NarrativePlan as PlanRecord


def _check_authority(
    plan: NarrativePlan, request: ContentGenerationRequest, brief: dict[str, Any]
) -> None:
    if not plan.is_current or plan.status not in (
        NarrativePlanStatus.VALIDATED,
        NarrativePlanStatus.APPROVED,
    ):
        raise ValueError(
            "NarrativePlan must be current and VALIDATED or APPROVED before script generation."
        )
    if (
        plan.content_generation_request_id != request.id
        or plan.topic_candidate_id != request.topic_candidate_id
        or plan.research_brief_id != request.research_brief_id
        or plan.channel_dna_revision_id != request.channel_dna_revision_id
    ):
        raise ValueError("NarrativePlan does not match the exact pinned content authorities.")
    claims = {str(c["claim_id"]): c for c in brief["verified_claims"]}
    for ref in plan.get_all_grounding_references():
        claim = claims.get(str(ref.claim_id))
        if ref.research_brief_id != request.research_brief_id or claim is None:
            raise ValueError(
                "NarrativePlan grounding must use verified claims in the pinned ResearchBrief."
            )
        if not any(
            str(c.get("evidence_id")) == str(ref.evidence_id)
            and str(c.get("source_id")) == str(ref.source_id)
            for c in claim.get("citations", [])
        ):
            raise ValueError(
                "NarrativePlan grounding evidence is not in the pinned verified claim."
            )


async def ensure_script_plan(
    session: AsyncSession,
    request: ContentGenerationRequest,
    brief: dict[str, Any],
    intent: dict[str, Any],
    target_plan_id: UUID | None = None,
) -> NarrativePlan:
    """Serialize first-plan creation and keep the authority lock until script commit."""

    def prepare(sync: Session) -> tuple[NarrativePlan, str | None]:
        sync.execute(
            select(ContentGenerationRequest.id)
            .where(ContentGenerationRequest.id == request.id)
            .with_for_update()
        )
        repository = PostgresNarrativePlanRepository(session=sync)
        validator = NarrativePlanValidator(strict_grounding=True)
        plan_service = NarrativePlanService(repository=repository, validator=validator)
        plan = repository.get_current_for_request(request.id)
        if target_plan_id is not None and (plan is None or plan.id != target_plan_id):
            raise ValueError(
                "Requested NarrativePlan is not the current authority for this content request."
            )
        if plan is None:
            if repository.list_by_request(request.id):
                raise ValueError(
                    "No current NarrativePlan; restore authority through explicit narrative revision."
                )
            duration = request.target_duration_seconds
            profile = (
                NarrativeFormatProfile.SHORT
                if duration <= 60
                else NarrativeFormatProfile.MEDIUM
                if duration <= 480
                else NarrativeFormatProfile.LONG
            )
            plan, _, _ = NarrativePlanningService(
                plan_service=plan_service, validator=validator
            ).plan_narrative_for_request(
                content_generation_request_id=request.id,
                channel_dna_revision_id=request.channel_dna_revision_id,
                channel_dna=request.channel_dna_revision.snapshot,
                research_brief=brief,
                content_intent=intent,
                topic_title=request.topic_candidate.title,
                topic_summary=request.topic_candidate.summary,
                topic_candidate_id=request.topic_candidate_id,
                research_brief_id=request.research_brief_id,
                format_profile=profile,
                target_duration_seconds=duration,
            )
        _check_authority(plan, request, brief)
        validation = plan_service.validator.validate(plan)
        qa = NarrativeQAService(
            structural_evaluator=StructuralQAEvaluator(validator)
        ).evaluate_plan(
            plan, research_brief=brief, channel_dna=request.channel_dna_revision.snapshot
        )
        record = sync.get(PlanRecord, plan.id)
        record.metadata_ = {**plan.metadata, "narrative_qa": qa.model_dump(mode="json")}
        error = None
        try:
            NarrativePlanScriptAdapter.enforce_script_gate(plan, qa_result=qa)
        except NarrativeQAGateError as exc:
            error = str(exc)
        if not validation.is_valid or error:
            record.status = NarrativePlanStatus.REJECTED.value
            error = error or f"NarrativePlan {plan.id} blocked by narrative validation."
        sync.flush()
        return plan, error

    plan, error = await session.run_sync(prepare)
    if error:
        # Preserve the rejected plan/findings for inspection; no script has been created.
        await session.commit()
        raise ValueError(error)
    return plan


async def get_current_plan(
    session: AsyncSession, channel_id: UUID, request_id: UUID
) -> NarrativePlan | None:
    request = await session.scalar(
        select(ContentGenerationRequest).where(
            ContentGenerationRequest.id == request_id,
            ContentGenerationRequest.channel_id == channel_id,
        )
    )
    if request is None:
        raise ValueError("Content request not found.")
    return await session.run_sync(
        lambda sync: PostgresNarrativePlanRepository(session=sync).get_current_for_request(
            request_id
        )
    )
