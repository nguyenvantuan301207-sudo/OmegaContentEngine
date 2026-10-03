"""NarrativePlan to Script and Storyboard integration seam.

Provides adaptation contracts translating NarrativePlan structure into
script generation context, outline representations, and downstream
storyboard lineage tracking.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from omega.domain.channel_dna import ChannelDNA
from omega.domain.content import ContentIntentResponse
from omega.domain.narrative_plan import (
    NarrativePlan,
    NarrativeSection,
    NarrativeSectionRole,
)


class ScriptGenerationContext(BaseModel):
    """Context bundle supplied to future AI and deterministic script generators."""

    content_generation_request_id: UUID
    target_duration_seconds: int
    channel_dna: dict[str, Any]
    research_brief: dict[str, Any]
    content_intent: dict[str, Any]
    narrative_plan: NarrativePlan | None = None
    selected_hook_text: str | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class NarrativePlanScriptAdapter:
    """Adapts NarrativePlan structures into legacy and future script generation interfaces."""

    @staticmethod
    def prepare_generation_context(
        content_generation_request_id: UUID,
        target_duration_seconds: int,
        channel_dna: ChannelDNA | dict[str, Any],
        research_brief: dict[str, Any],
        content_intent: ContentIntentResponse | dict[str, Any],
        narrative_plan: NarrativePlan | None = None,
        selected_hook_text: str | None = None,
    ) -> ScriptGenerationContext:
        """Construct the canonical script generation context."""
        dna_dict = (
            channel_dna.model_dump()
            if isinstance(channel_dna, ChannelDNA)
            else dict(channel_dna)
        )
        intent_dict = (
            content_intent.model_dump()
            if isinstance(content_intent, ContentIntentResponse)
            else dict(content_intent)
        )
        return ScriptGenerationContext(
            content_generation_request_id=content_generation_request_id,
            target_duration_seconds=target_duration_seconds,
            channel_dna=dna_dict,
            research_brief=research_brief,
            content_intent=intent_dict,
            narrative_plan=narrative_plan,
            selected_hook_text=selected_hook_text,
        )

    @staticmethod
    def map_plan_to_script_outline(plan: NarrativePlan) -> dict[str, Any]:
        """Convert a NarrativePlan into the structured outline consumed by script generators.

        This maintains full backward-compatibility with ContentOutline while carrying
        the authoritative semantic roles, objectives, and promise/payoff connections.
        """
        outline_sections = []
        for s in sorted(plan.sections, key=lambda x: x.section_order):
            # Translate section role and promise/payoff into retention hints
            retention_goal = f"ROLE_{s.role.value}"
            if s.promise_id:
                retention_goal += f":OPEN_LOOP({s.promise_id})"
            elif s.payoff_reference:
                retention_goal += f":PAYOFF({s.payoff_reference})"

            claim_refs = [str(c) for c in s.get_grounding_claim_ids()]

            outline_sections.append(
                {
                    "section_id": f"sec_{s.section_order}",
                    "title": f"[{s.role.value}] {s.objective[:40]}",
                    "objective": s.objective,
                    "key_points": list(s.key_information),
                    "claim_refs": claim_refs,
                    "estimated_duration_seconds": s.target_duration_seconds,
                    "transition": f"Proceeding to section {s.section_order + 1}" if s.section_order < len(plan.sections) else "Closing",
                    "retention_goal": retention_goal,
                    "narrative_role": s.role.value,
                    "information_density": s.target_information_density.value,
                    "promise_id": s.promise_id,
                    "payoff_reference": s.payoff_reference,
                }
            )

        hook_sections = plan.get_sections_by_role(NarrativeSectionRole.HOOK)
        closing_sections = plan.get_sections_by_role(NarrativeSectionRole.CLOSING)

        return {
            "narrative_plan_id": str(plan.id),
            "narrative_plan_version": plan.version,
            "format_profile": plan.format_profile.value,
            "opening_description": hook_sections[0].objective if hook_sections else "Introductory hook",
            "closing_description": closing_sections[0].objective if closing_sections else "Concluding summary",
            "sections": outline_sections,
        }

    @staticmethod
    def verify_script_narrative_conformance(
        script_dict: dict[str, Any],
        plan: NarrativePlan,
    ) -> list[str]:
        """Verify whether a generated script honors the section count and duration targets of the NarrativePlan."""
        deviations: list[str] = []
        sections = script_dict.get("sections", [])
        if len(sections) != len(plan.sections):
            deviations.append(
                f"Section count mismatch: Script has {len(sections)} sections, plan requires {len(plan.sections)}."
            )

        script_dur = script_dict.get("estimated_duration_seconds", 0)
        dur_diff = abs(script_dur - plan.target_duration_seconds)
        if dur_diff > (plan.target_duration_seconds * 0.25):
            deviations.append(
                f"Duration divergence: Script duration {script_dur}s diverges from plan target {plan.target_duration_seconds}s by {dur_diff}s."
            )

        return deviations
