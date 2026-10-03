"""Narrative Director application component for P21-B.

Owns intelligent story structure selection, research grounding allocation,
promise/payoff construction, format profile adaptation, multi-candidate generation,
and deterministic candidate evaluation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from omega.application.narrative_plan_validator import NarrativePlanValidator
from omega.domain.channel_dna import ChannelDNA
from omega.domain.narrative_plan import (
    FORMAT_PROFILE_CONSTRAINTS,
    GroundingReference,
    GroundingType,
    InformationDensity,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativePlanValidationResult,
    NarrativeSection,
    NarrativeSectionRole,
)
from omega.domain.narrative_strategy import (
    STRATEGY_CATALOG,
    NarrativePlanDraft,
    NarrativeStrategy,
    NarrativeStrategyDefinition,
    NarrativeStrategySelector,
    StrategySelectionScore,
)
from omega.logging import get_logger

logger = get_logger("omega-narrative-director")


class NarrativeDirector(Protocol):
    """Protocol for intelligent Narrative Directors."""

    def generate_candidates(
        self,
        content_request_id: UUID,
        channel_dna: ChannelDNA | dict[str, Any],
        research_brief: dict[str, Any],
        content_intent: dict[str, Any],
        topic_title: str,
        topic_summary: str | None = None,
        format_profile: NarrativeFormatProfile = NarrativeFormatProfile.MEDIUM,
        target_duration_seconds: int | None = None,
        candidate_count: int = 3,
    ) -> list[NarrativePlanDraft]:
        """Generate multiple structured NarrativePlan candidate drafts."""
        ...


class CandidateEvaluation(Protocol):
    """Candidate evaluation result."""


class DeterministicNarrativeDirector:
    """Production-grade deterministic Narrative Director.

    Generates structured, research-grounded, format-compliant story structure drafts
    for multiple candidate strategies with full loop coherence and factual lineage.
    """

    DIRECTOR_VERSION = "p21-b-v1.0.0"

    def __init__(self, validator: NarrativePlanValidator | None = None) -> None:
        self.validator = validator or NarrativePlanValidator()

    def generate_candidates(
        self,
        content_request_id: UUID,
        channel_dna: ChannelDNA | dict[str, Any],
        research_brief: dict[str, Any],
        content_intent: dict[str, Any],
        topic_title: str,
        topic_summary: str | None = None,
        format_profile: NarrativeFormatProfile = NarrativeFormatProfile.MEDIUM,
        target_duration_seconds: int | None = None,
        candidate_count: int = 3,
    ) -> list[NarrativePlanDraft]:
        """Generate up to `candidate_count` distinct valid candidate plans."""
        dna_dict = channel_dna.model_dump() if isinstance(channel_dna, ChannelDNA) else dict(channel_dna)
        constraints = FORMAT_PROFILE_CONSTRAINTS[format_profile]
        target_dur = target_duration_seconds or constraints.default_duration_seconds

        # 1. Evaluate and rank all eligible strategies
        ranked_strategies = NarrativeStrategySelector.evaluate_strategies(
            topic_title=topic_title,
            topic_summary=topic_summary,
            brief_dict=research_brief,
            dna_dict=dna_dict,
            content_intent=content_intent,
            format_profile=format_profile,
        )
        eligible = [s for s in ranked_strategies if s.is_eligible]
        if not eligible:
            fallback = NarrativeStrategySelector.select_best_strategy(
                topic_title=topic_title,
                topic_summary=topic_summary,
                brief_dict=research_brief,
                dna_dict=dna_dict,
                content_intent=content_intent,
                format_profile=format_profile,
            )
            selected_strategies = [fallback]
        else:
            selected_strategies = [s.strategy for s in eligible[:candidate_count]]

        # Ensure at least 1 strategy is selected
        if not selected_strategies:
            selected_strategies = [NarrativeStrategy.HOW_IT_WORKS]

        candidates: list[NarrativePlanDraft] = []
        for strat in selected_strategies:
            draft = self._build_candidate_draft(
                strategy=strat,
                format_profile=format_profile,
                target_duration_seconds=target_dur,
                content_request_id=content_request_id,
                topic_title=topic_title,
                topic_summary=topic_summary,
                brief_dict=research_brief,
                dna_dict=dna_dict,
                content_intent=content_intent,
            )
            candidates.append(draft)

        return candidates

    def _build_candidate_draft(
        self,
        strategy: NarrativeStrategy,
        format_profile: NarrativeFormatProfile,
        target_duration_seconds: int,
        content_request_id: UUID,
        topic_title: str,
        topic_summary: str | None,
        brief_dict: dict[str, Any],
        dna_dict: dict[str, Any],
        content_intent: dict[str, Any],
    ) -> NarrativePlanDraft:
        """Construct a concrete, grounded NarrativePlanDraft for a specific strategy."""
        constraints = FORMAT_PROFILE_CONSTRAINTS[format_profile]
        strat_defn = STRATEGY_CATALOG[strategy]
        brief_id_str = brief_dict.get("id")
        brief_uuid = UUID(brief_id_str) if brief_id_str else uuid.uuid4()

        # Extract verified claims for grounding
        verified_claims = brief_dict.get("verified_claims", []) or []
        grounding_pool: list[GroundingReference] = []
        for c in verified_claims:
            claim_id_val = c.get("claim_id")
            if claim_id_val:
                try:
                    c_uuid = UUID(str(claim_id_val))
                    evidence_list = c.get("evidence", []) or []
                    ev_id_val = evidence_list[0].get("evidence_id") if evidence_list else None
                    ev_uuid = UUID(str(ev_id_val)) if ev_id_val else None
                    src_id_val = evidence_list[0].get("source_id") if evidence_list else None
                    src_uuid = UUID(str(src_id_val)) if src_id_val else None
                    grounding_pool.append(
                        GroundingReference(
                            research_brief_id=brief_uuid,
                            claim_id=c_uuid,
                            evidence_id=ev_uuid,
                            source_id=src_uuid,
                            grounding_type=GroundingType.FACTUAL,
                            description=c.get("claim_text", "")[:300],
                        )
                    )
                except (ValueError, TypeError):
                    continue

        # Determine roles based on format profile and strategy
        if format_profile == NarrativeFormatProfile.SHORT:
            roles = [
                NarrativeSectionRole.HOOK,
                NarrativeSectionRole.PAYOFF,
                NarrativeSectionRole.TAKEAWAY,
            ]
        elif format_profile == NarrativeFormatProfile.MEDIUM:
            if strat_defn.has_open_loop:
                roles = [
                    NarrativeSectionRole.HOOK,
                    NarrativeSectionRole.PROMISE,
                    NarrativeSectionRole.DEVELOPMENT,
                    NarrativeSectionRole.PAYOFF,
                    NarrativeSectionRole.TAKEAWAY,
                    NarrativeSectionRole.CLOSING,
                ]
            else:
                roles = [
                    NarrativeSectionRole.HOOK,
                    NarrativeSectionRole.CONTEXT,
                    NarrativeSectionRole.DEVELOPMENT,
                    NarrativeSectionRole.PAYOFF,
                    NarrativeSectionRole.TAKEAWAY,
                    NarrativeSectionRole.CLOSING,
                ]
        else:  # LONG
            roles = [
                NarrativeSectionRole.HOOK,
                NarrativeSectionRole.PROMISE,
                NarrativeSectionRole.CONTEXT,
                NarrativeSectionRole.DEVELOPMENT,
                NarrativeSectionRole.ESCALATION,
                NarrativeSectionRole.PAYOFF,
                NarrativeSectionRole.TAKEAWAY,
                NarrativeSectionRole.CLOSING,
            ]

        # Allocate durations proportionally to satisfy constraints
        num_sections = len(roles)
        hook_duration = min(constraints.max_hook_duration_seconds, max(target_duration_seconds // (num_sections + 1), 5))
        remaining_duration = target_duration_seconds - hook_duration
        other_durations = [max(remaining_duration // (num_sections - 1), 5) for _ in range(num_sections - 1)]
        # Adjust last section to match target_duration_seconds exactly
        other_durations[-1] += target_duration_seconds - (hook_duration + sum(other_durations))
        durations = [hook_duration] + other_durations

        # Set up curiosity loop / promise if applicable
        has_promise = strat_defn.has_open_loop or (format_profile == NarrativeFormatProfile.LONG)
        active_promise_id = None
        open_loop_intent = None
        if has_promise:
            active_promise_id = f"loop_{strategy.value.lower()}_{uuid.uuid4().hex[:6]}"
            open_loop_intent = f"Investigating whether {topic_title} satisfies the predicted mechanical outcome."

        sections: list[NarrativeSection] = []
        grounding_idx = 0
        promise_placed = False

        for order, (role, dur) in enumerate(zip(roles, durations, strict=True), start=1):
            sec_grounding: list[GroundingReference] = []
            sec_promise = None
            sec_payoff = None

            # Attach loop hook / promise and corresponding payoff
            if active_promise_id:
                should_place_promise = (
                    not promise_placed
                    and (
                        role == NarrativeSectionRole.PROMISE
                        or (role == NarrativeSectionRole.HOOK and NarrativeSectionRole.PROMISE not in roles)
                    )
                )
                if should_place_promise:
                    sec_promise = active_promise_id
                    promise_placed = True
                elif promise_placed and role == NarrativeSectionRole.PAYOFF and sec_payoff is None:
                    sec_payoff = active_promise_id

            # Ground factual sections
            if role in (NarrativeSectionRole.CONTEXT, NarrativeSectionRole.DEVELOPMENT, NarrativeSectionRole.PAYOFF):
                if grounding_pool:
                    sec_grounding.append(grounding_pool[grounding_idx % len(grounding_pool)])
                    grounding_idx += 1

            objective = self._generate_section_objective(role, strategy, topic_title, content_intent)
            key_info = [
                f"Factual detail for {role.value} under {strategy.value}",
                f"Core mechanism of {topic_title}",
            ]

            density = InformationDensity.HIGH if role in (NarrativeSectionRole.HOOK, NarrativeSectionRole.DEVELOPMENT) else InformationDensity.MEDIUM

            sections.append(
                NarrativeSection(
                    id=uuid.uuid4(),
                    section_order=order,
                    role=role,
                    objective=objective,
                    key_information=key_info,
                    grounding_references=sec_grounding,
                    target_duration_seconds=dur,
                    target_information_density=density,
                    open_loop_intent=open_loop_intent if sec_promise else None,
                    promise_id=sec_promise,
                    payoff_reference=sec_payoff,
                    notes=f"Generated via strategy {strategy.value}",
                )
            )

        rationale = f"Strategy '{strat_defn.name}' matches topic intent '{content_intent.get('primary_goal', topic_title)}' with {len(sections)} structured sections."

        return NarrativePlanDraft(
            strategy=strategy,
            format_profile=format_profile,
            target_duration_seconds=target_duration_seconds,
            estimated_duration_seconds=sum(s.target_duration_seconds for s in sections),
            sections=sections,
            rationale=rationale,
            metadata={
                "strategy": strategy.value,
                "strategy_name": strat_defn.name,
                "director_version": self.DIRECTOR_VERSION,
                "content_generation_request_id": str(content_request_id),
                "generated_at": datetime.now(UTC).isoformat(),
            },
        )

    def _generate_section_objective(
        self,
        role: NarrativeSectionRole,
        strategy: NarrativeStrategy,
        topic_title: str,
        content_intent: dict[str, Any],
    ) -> str:
        """Create a descriptive editorial objective for each section role."""
        if role == NarrativeSectionRole.HOOK:
            return f"Capture attention by highlighting the critical question regarding {topic_title}."
        if role == NarrativeSectionRole.PROMISE:
            return f"Establish the viewer contract regarding what insights will be revealed about {topic_title}."
        if role == NarrativeSectionRole.CONTEXT:
            return f"Provide foundational historical and technical background for understanding {topic_title}."
        if role == NarrativeSectionRole.DEVELOPMENT:
            return f"Progressively unpack the core operational mechanisms of {topic_title}."
        if role == NarrativeSectionRole.ESCALATION:
            return f"Raise the stakes and examine severe failure modes or bottlenecks in {topic_title}."
        if role == NarrativeSectionRole.PAYOFF:
            return f"Deliver the empirical resolution and payoff explaining the reality of {topic_title}."
        if role == NarrativeSectionRole.TAKEAWAY:
            return f"Synthesize actionable engineering conclusions from {topic_title}."
        if role == NarrativeSectionRole.CLOSING:
            return f"Conclude with a high-impact summary of {topic_title}."
        if role == NarrativeSectionRole.CTA:
            return f"Direct viewers to explore further technical documentation on {topic_title}."
        return f"Examine {role.value} for {topic_title}."


class CandidateSelectionEngine:
    """Deterministic selection layer that evaluates and ranks candidate NarrativePlanDrafts."""

    def __init__(self, validator: NarrativePlanValidator | None = None) -> None:
        self.validator = validator or NarrativePlanValidator()

    def select_best_candidate(
        self,
        candidates: list[NarrativePlanDraft],
        content_generation_request_id: UUID,
        channel_dna_revision_id: UUID,
        topic_candidate_id: UUID | None = None,
        research_brief_id: UUID | None = None,
    ) -> tuple[NarrativePlan, NarrativePlanValidationResult, dict[str, Any]]:
        """Select the highest-quality candidate plan that satisfies all P21-A validation checks."""
        if not candidates:
            raise ValueError("No candidate NarrativePlanDrafts provided for selection.")

        scored_candidates: list[tuple[float, NarrativePlan, NarrativePlanValidationResult, dict[str, Any]]] = []

        for draft in candidates:
            plan = NarrativePlan(
                id=uuid.uuid4(),
                content_generation_request_id=content_generation_request_id,
                channel_dna_revision_id=channel_dna_revision_id,
                topic_candidate_id=topic_candidate_id,
                research_brief_id=research_brief_id,
                version=1,
                status=NarrativePlanStatus.DRAFT,
                format_profile=draft.format_profile,
                target_duration_seconds=draft.target_duration_seconds,
                estimated_duration_seconds=draft.estimated_duration_seconds,
                is_current=True,
                schema_version=1,
                sections=draft.sections,
                metadata={
                    **draft.metadata,
                    "selection_candidate_strategy": draft.strategy.value,
                },
            )

            # Validate against P21-A deterministic rules
            val_result = self.validator.validate(plan)
            if not val_result.is_valid:
                continue  # Disqualify invalid candidates

            # Score based on grounding, loop coherence, and section balance
            grounding_count = sum(len(s.grounding_references) for s in plan.sections)
            grounding_score = min(grounding_count * 15.0, 45.0)

            # Promise/payoff loop score
            loop_score = 25.0 if any(s.promise_id for s in plan.sections) and any(s.payoff_reference for s in plan.sections) else 10.0

            # Duration fidelity score
            dur_diff = abs(plan.target_duration_seconds - plan.estimated_duration_seconds)
            dur_score = max(0.0, 30.0 - dur_diff)

            total_score = grounding_score + loop_score + dur_score
            rationale = {
                "strategy": draft.strategy.value,
                "total_score": total_score,
                "grounding_count": grounding_count,
                "has_promise_payoff": loop_score == 25.0,
                "duration_delta_seconds": dur_diff,
            }
            scored_candidates.append((total_score, plan, val_result, rationale))

        if not scored_candidates:
            raise ValueError("All candidate NarrativePlanDrafts failed P21-A deterministic validation.")

        # Pick highest scoring plan
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        winner_score, winning_plan, winning_val, winning_rationale = scored_candidates[0]

        winning_plan.status = NarrativePlanStatus.VALIDATED
        winning_plan.metadata["selection_rationale"] = winning_rationale
        winning_plan.metadata["selected_score"] = winner_score

        return winning_plan, winning_val, winning_rationale
