"""Narrative Planning application service orchestrating P21-B Narrative Director lifecycle.

Coordinates authority loading, candidate generation, candidate selection, P21-A validation,
atomic PostgreSQL persistence, and downstream script generation handoff.
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from uuid import UUID

from omega.application.narrative_director import (
    CandidateSelectionEngine,
    DeterministicNarrativeDirector,
    ModelBackedNarrativeDirector,
    NarrativeDirector,
)
from omega.application.narrative_plan_service import (
    NarrativePlanRepository,
    NarrativePlanService,
    get_narrative_plan_repository,
)
from omega.application.narrative_plan_validator import NarrativePlanValidator
from omega.application.narrative_script_adapter import (
    NarrativePlanScriptAdapter,
    ScriptGenerationContext,
)
from omega.domain.channel_dna import ChannelDNA
from omega.domain.narrative_plan import (
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanValidationResult,
    NarrativeSection,
)
from omega.domain.narrative_strategy import NarrativePlanDraft
from omega.logging import get_logger

logger = get_logger("omega-narrative-planning-service")


class NarrativePlanningService:
    """Application orchestration service for NarrativePlan generation, selection, and handoff."""

    def __init__(
        self,
        director: NarrativeDirector | None = None,
        plan_service: NarrativePlanService | None = None,
        validator: NarrativePlanValidator | None = None,
        selection_engine: CandidateSelectionEngine | None = None,
        max_retries: int = 2,
    ) -> None:
        self.validator = validator or NarrativePlanValidator()
        self.fallback_director = DeterministicNarrativeDirector(validator=self.validator)
        if director is not None:
            self.director = director
        elif os.getenv("OMEGA_NARRATIVE_DIRECTOR_MODE", "").lower() == "model" and os.getenv("GEMINI_API_KEY"):
            self.director = ModelBackedNarrativeDirector(validator=self.validator)
        else:
            self.director = self.fallback_director
        self.plan_service = plan_service or NarrativePlanService(validator=self.validator)
        self.selection_engine = selection_engine or CandidateSelectionEngine(validator=self.validator)
        self.max_retries = max_retries

    def plan_narrative_for_request(
        self,
        content_generation_request_id: UUID,
        channel_dna_revision_id: UUID,
        channel_dna: ChannelDNA | dict[str, Any],
        research_brief: dict[str, Any],
        content_intent: dict[str, Any],
        topic_title: str,
        topic_summary: str | None = None,
        topic_candidate_id: UUID | None = None,
        research_brief_id: UUID | None = None,
        format_profile: NarrativeFormatProfile = NarrativeFormatProfile.MEDIUM,
        target_duration_seconds: int | None = None,
        candidate_count: int = 3,
    ) -> tuple[NarrativePlan, NarrativePlanValidationResult, ScriptGenerationContext]:
        """Orchestrate intelligent generation of candidate plans, selection, validation, persistence, and script handoff."""
        dna_dict = channel_dna.model_dump() if isinstance(channel_dna, ChannelDNA) else dict(channel_dna)
        attempts = 0
        candidates: list[NarrativePlanDraft] = []
        last_error = None

        # Step 1: Bounded retry candidate generation
        while attempts <= self.max_retries and not candidates:
            try:
                candidates = self.director.generate_candidates(
                    content_request_id=content_generation_request_id,
                    channel_dna=dna_dict,
                    research_brief=research_brief,
                    content_intent=content_intent,
                    topic_title=topic_title,
                    topic_summary=topic_summary,
                    format_profile=format_profile,
                    target_duration_seconds=target_duration_seconds,
                    candidate_count=candidate_count,
                )
            except Exception as e:
                attempts += 1
                last_error = e
                logger.warning(
                    f"Candidate generation attempt {attempts} failed: {e}. Retrying up to {self.max_retries}..."
                )

        # Step 2: Deterministic fallback if retries exhausted or no candidates returned
        if not candidates:
            logger.warning(
                "All candidate generation attempts failed or returned empty; engaging deterministic fallback director."
            )
            fallback_director = DeterministicNarrativeDirector(validator=self.validator)
            candidates = fallback_director.generate_candidates(
                content_request_id=content_generation_request_id,
                channel_dna=dna_dict,
                research_brief=research_brief,
                content_intent=content_intent,
                topic_title=topic_title,
                topic_summary=topic_summary,
                format_profile=format_profile,
                target_duration_seconds=target_duration_seconds,
                candidate_count=1,
            )

        if not candidates:
            raise RuntimeError(f"Failed to generate NarrativePlan candidates: {last_error}")

        # Step 3: Candidate Selection
        winning_candidate_plan, val_result, selection_rationale = self.selection_engine.select_best_candidate(
            candidates=candidates,
            content_generation_request_id=content_generation_request_id,
            channel_dna_revision_id=channel_dna_revision_id,
            topic_candidate_id=topic_candidate_id,
            research_brief_id=research_brief_id,
        )

        # Step 4: Persist the selected plan via P21-A NarrativePlanService authority
        persisted_plan, _ = self.plan_service.create_plan(
            content_generation_request_id=content_generation_request_id,
            channel_dna_revision_id=channel_dna_revision_id,
            topic_candidate_id=topic_candidate_id,
            research_brief_id=research_brief_id,
            format_profile=winning_candidate_plan.format_profile,
            target_duration_seconds=winning_candidate_plan.target_duration_seconds,
            sections=winning_candidate_plan.sections,
            metadata=winning_candidate_plan.metadata,
            auto_validate=True,
        )

        logger.info(
            "Selected and persisted authoritative NarrativePlan",
            plan_id=str(persisted_plan.id),
            version=persisted_plan.version,
            strategy=persisted_plan.metadata.get("strategy"),
            format_profile=persisted_plan.format_profile.value,
            sections_count=len(persisted_plan.sections),
        )

        # Step 5: Downstream Script Generation Handoff
        script_context = NarrativePlanScriptAdapter.prepare_generation_context(
            content_generation_request_id=content_generation_request_id,
            target_duration_seconds=persisted_plan.target_duration_seconds,
            channel_dna=dna_dict,
            research_brief=research_brief,
            content_intent=content_intent,
            narrative_plan=persisted_plan,
        )

        return persisted_plan, val_result, script_context

    def prepare_script_outline(self, plan: NarrativePlan) -> dict[str, Any]:
        """Convert authoritative NarrativePlan into the structured outline consumed by script generators."""
        return NarrativePlanScriptAdapter.map_plan_to_script_outline(plan)
