"""Content generation provider protocol and deterministic template implementation."""

from __future__ import annotations

from typing import Any, Protocol

from omega.application.content_pacing import (
    DEFAULT_PACE,
    estimate_duration_seconds,
    plan_retention_beats,
)
from omega.domain.content import ContentStatementType, HookType


class ContentGenerationProvider(Protocol):
    """Protocol for content generation providers."""

    def generate_intent(
        self,
        topic_title: str,
        topic_summary: str | None,
        brief_dict: dict[str, Any],
        dna_dict: dict[str, Any],
        creative_direction: str | None = None,
    ) -> dict[str, Any]:
        """Generate editorial intent driving the content piece."""
        ...

    def generate_hooks(
        self,
        topic_title: str,
        brief_dict: dict[str, Any],
        dna_dict: dict[str, Any],
        intent_dict: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Generate hook variants with provenance citations for factual assertions."""
        ...

    def generate_outline(
        self,
        topic_title: str,
        brief_dict: dict[str, Any],
        dna_dict: dict[str, Any],
        intent_dict: dict[str, Any],
        selected_hook: dict[str, Any] | None,
        target_duration_seconds: int,
    ) -> dict[str, Any]:
        """Generate structured section outline."""
        ...

    def generate_script(
        self,
        topic_title: str,
        brief_dict: dict[str, Any],
        dna_dict: dict[str, Any],
        intent_dict: dict[str, Any],
        selected_hook: dict[str, Any],
        outline_dict: dict[str, Any],
        target_duration_seconds: int,
    ) -> dict[str, Any]:
        """Generate full script structure with classified statements and citations."""
        ...


class TemplateContentProvider:
    """Deterministic, test-stable content generator with 100% factual claim provenance."""

    def generate_intent(
        self,
        topic_title: str,
        topic_summary: str | None,
        brief_dict: dict[str, Any],
        dna_dict: dict[str, Any],
        creative_direction: str | None = None,
    ) -> dict[str, Any]:
        brand = dna_dict.get("brand_voice", {})
        raw_tone = brand.get("tone", "AUTHORITATIVE")
        tone = (
            ", ".join(raw_tone) if isinstance(raw_tone, list) else str(raw_tone or "AUTHORITATIVE")
        )

        raw_pace = brand.get("pace", DEFAULT_PACE)
        pace = raw_pace[0] if isinstance(raw_pace, list) else str(raw_pace or DEFAULT_PACE)

        raw_comp = brand.get("complexity", "INTERMEDIATE")
        complexity = (
            ", ".join(raw_comp) if isinstance(raw_comp, list) else str(raw_comp or "INTERMEDIATE")
        )

        return {
            "primary_goal": f"Deliver a high-clarity technical breakdown of {topic_title}.",
            "audience_intent": "Understand real-world architectural principles and empirical trade-offs.",
            "viewer_promise": f"By the end of this video, you will understand the evidence-backed reality behind {topic_title}.",
            "central_question": f"What is the proven technical approach to {topic_title}?",
            "core_takeaway": brief_dict.get("summary") or f"Key takeaways on {topic_title}.",
            "tone": tone[:100],
            "pace": pace[:100],
            "complexity": complexity[:100],
            "desired_emotion": "Empowered and technically informed",
            "call_to_action_type": "SUBSCRIBE_AND_COMMENT",
        }

    def generate_hooks(
        self,
        topic_title: str,
        brief_dict: dict[str, Any],
        dna_dict: dict[str, Any],
        intent_dict: dict[str, Any],
    ) -> list[dict[str, Any]]:
        verified_claims = brief_dict.get("verified_claims", [])

        hooks: list[dict[str, Any]] = []

        # Hook 1: Curiosity Question (Creative)
        hooks.append(
            {
                "hook_variant_index": 0,
                "text": f"Is {topic_title} truly the right architectural decision for your stack?",
                "hook_type": HookType.QUESTION.value,
                "score": 85.0,
                "reason_codes": ["PROVOKES_CURIOSITY", "TARGETS_PRACTITIONERS"],
                "selected": True,  # default selected
                "citations": [],
            }
        )

        # Hook 2: Result-First (Creative/Framing)
        hooks.append(
            {
                "hook_variant_index": 1,
                "text": f"Here is the empirical reality of {topic_title} without the hype.",
                "hook_type": HookType.RESULT_FIRST.value,
                "score": 88.0,
                "reason_codes": ["DIRECT_VALUE_PROMISE", "NO_FLUFF"],
                "selected": False,
                "citations": [],
            }
        )

        # Hook 3: Statistical / Factual Hook (with provenance if claims exist)
        if verified_claims:
            top_claim = verified_claims[0]
            citations = [
                {
                    "research_brief_id": brief_dict["id"],
                    "claim_id": top_claim["claim_id"],
                    "evidence_id": cit["evidence_id"],
                    "source_id": cit["source_id"],
                }
                for cit in top_claim.get("citations", [])
            ]
            hooks.append(
                {
                    "hook_variant_index": 2,
                    "text": f"Proven finding: {top_claim['text']}",
                    "hook_type": HookType.STATISTIC.value,
                    "score": 92.0,
                    "reason_codes": ["GROUNDED_IN_VERIFIED_DATA", "HIGH_CREDIBILITY"],
                    "selected": False,
                    "citations": citations,
                }
            )
        else:
            hooks.append(
                {
                    "hook_variant_index": 2,
                    "text": f"Why most teams get {topic_title} wrong in production.",
                    "hook_type": HookType.PROBLEM.value,
                    "score": 82.0,
                    "reason_codes": ["ADDRESSES_COMMON_PITFALLS"],
                    "selected": False,
                    "citations": [],
                }
            )

        return hooks

    def generate_outline(
        self,
        topic_title: str,
        brief_dict: dict[str, Any],
        dna_dict: dict[str, Any],
        intent_dict: dict[str, Any],
        selected_hook: dict[str, Any] | None,
        target_duration_seconds: int,
    ) -> dict[str, Any]:
        num_sections = 4
        sec_duration = max(30, target_duration_seconds // num_sections)

        sections = [
            {
                "section_id": "sec_intro",
                "title": "Context & Problem Definition",
                "objective": "Establish the stakes and introduce the core challenge.",
                "key_points": [
                    f"Current state of {topic_title}",
                    "Why traditional assumptions fail",
                ],
                "claim_refs": [],
                "estimated_duration_seconds": sec_duration,
                "transition": "Let's examine the foundational architecture.",
                "retention_goal": "Maintain opening viewer interest.",
            },
            {
                "section_id": "sec_core",
                "title": "Architectural Deep Dive",
                "objective": "Break down verified mechanics and technical trade-offs.",
                "key_points": ["Core system mechanics", "Execution benchmarks"],
                "claim_refs": [],
                "estimated_duration_seconds": sec_duration,
                "transition": "Now look at the empirical evidence.",
                "retention_goal": "Deliver primary technical depth.",
            },
            {
                "section_id": "sec_evidence",
                "title": "Empirical Evidence & Findings",
                "objective": "Present validated facts and benchmark citations.",
                "key_points": ["Verified research data", "Contradictions and caveats"],
                "claim_refs": [],
                "estimated_duration_seconds": sec_duration,
                "transition": "Here is how to apply this in practice.",
                "retention_goal": "Resolve open questions with solid data.",
            },
            {
                "section_id": "sec_conclusion",
                "title": "Production Recommendations & Summary",
                "objective": "Actionable steps for engineers and summary.",
                "key_points": ["Key takeaways", "Recommended deployment pattern"],
                "claim_refs": [],
                "estimated_duration_seconds": sec_duration,
                "transition": "Final takeaway.",
                "retention_goal": "High satisfaction and call-to-action follow-through.",
            },
        ]

        return {
            "opening_description": f"Introduction establishing viewer promise: {intent_dict.get('viewer_promise', '')}",
            "sections": sections,
            "closing_description": "Clear recap and structured call-to-action.",
        }

    def generate_script(
        self,
        topic_title: str,
        brief_dict: dict[str, Any],
        dna_dict: dict[str, Any],
        intent_dict: dict[str, Any],
        selected_hook: dict[str, Any],
        outline_dict: dict[str, Any],
        target_duration_seconds: int,
    ) -> dict[str, Any]:
        pace = intent_dict.get("pace", DEFAULT_PACE)
        verified_claims = brief_dict.get("verified_claims", [])
        uncertain_claims = brief_dict.get("uncertain_claims", [])

        hook_text = (
            selected_hook.get("text") if selected_hook else f"Let's dive into {topic_title}."
        )
        outline_sections = outline_dict.get("sections", [])
        num_sections = max(1, len(outline_sections))
        sec_duration = target_duration_seconds // num_sections

        beats = plan_retention_beats(target_duration_seconds, num_sections)

        sections: list[dict[str, Any]] = []

        for idx, outline_sec in enumerate(outline_sections):
            heading = outline_sec.get("title", f"Section {idx + 1}")
            statements: list[dict[str, Any]] = []

            # 1. Transition / Opener statement (Creative / Transition)
            statements.append(
                {
                    "statement_order": 1,
                    "statement_text": f"In this section, we explore {heading.lower()}.",
                    "statement_type": ContentStatementType.TRANSITION.value,
                    "qualification_note": None,
                    "citations": [],
                }
            )

            # 2. Section-specific factual content
            if idx == 1 and verified_claims:
                # Architectural facts from first verified claim
                claim = verified_claims[0]
                citations = [
                    {
                        "research_brief_id": brief_dict["id"],
                        "claim_id": claim["claim_id"],
                        "evidence_id": cit["evidence_id"],
                        "source_id": cit["source_id"],
                    }
                    for cit in claim.get("citations", [])
                ]
                statements.append(
                    {
                        "statement_order": 2,
                        "statement_text": f"Research confirms: {claim['text']}",
                        "statement_type": ContentStatementType.FACTUAL.value,
                        "qualification_note": None,
                        "citations": citations,
                    }
                )
            elif idx == 2 and len(verified_claims) > 1:
                # Empirical findings from second verified claim
                claim = verified_claims[1]
                citations = [
                    {
                        "research_brief_id": brief_dict["id"],
                        "claim_id": claim["claim_id"],
                        "evidence_id": cit["evidence_id"],
                        "source_id": cit["source_id"],
                    }
                    for cit in claim.get("citations", [])
                ]
                statements.append(
                    {
                        "statement_order": 2,
                        "statement_text": f"Furthermore, empirical analysis demonstrates: {claim['text']}",
                        "statement_type": ContentStatementType.FACTUAL.value,
                        "qualification_note": None,
                        "citations": citations,
                    }
                )
            elif idx == 2 and uncertain_claims:
                # Qualified statement for uncertain claim
                u_claim = uncertain_claims[0]
                statements.append(
                    {
                        "statement_order": 2,
                        "statement_text": f"Current analysis suggests that {u_claim['text']}, though ongoing verification is advised.",
                        "statement_type": ContentStatementType.INTERPRETIVE.value,
                        "qualification_note": u_claim.get("uncertainty_reason"),
                        "citations": [],
                    }
                )
            else:
                statements.append(
                    {
                        "statement_order": 2,
                        "statement_text": f"Building resilient systems around {topic_title} requires balancing throughput, isolation, and maintainability.",
                        "statement_type": ContentStatementType.CREATIVE.value,
                        "qualification_note": None,
                        "citations": [],
                    }
                )

            # 3. Section synthesis statement
            statements.append(
                {
                    "statement_order": 3,
                    "statement_text": "Understanding these details allows engineering teams to make disciplined architectural choices.",
                    "statement_type": ContentStatementType.CREATIVE.value,
                    "qualification_note": None,
                    "citations": [],
                }
            )

            # Build section narration text from statements
            narration = " ".join(s["statement_text"] for s in statements)
            sec_beat = beats[idx] if idx < len(beats) else None

            sections.append(
                {
                    "section_order": idx + 1,
                    "heading": heading,
                    "narration_text": narration,
                    "estimated_duration_seconds": sec_duration,
                    "transition_text": outline_sec.get("transition"),
                    "retention_beat": sec_beat,
                    "statements": statements,
                }
            )

        closing_text = (
            f"To wrap up our analysis on {topic_title}: prioritizing verifiable architectural patterns "
            "delivers reliable, high-performance systems."
        )
        cta_text = "If you found this engineering breakdown valuable, subscribe for more architecture deep dives."

        # Compute total words and duration
        all_words = f"{hook_text} {' '.join(s['narration_text'] for s in sections)} {closing_text} {cta_text}".split()
        word_count = len(all_words)
        duration = estimate_duration_seconds(word_count, pace)

        return {
            "title": topic_title,
            "hook_id": selected_hook.get("id"),
            "hook_text": hook_text,
            "closing_text": closing_text,
            "cta_text": cta_text,
            "estimated_word_count": word_count,
            "estimated_duration_seconds": duration,
            "style_snapshot": {
                "tone": intent_dict.get("tone"),
                "pace": pace,
                "complexity": intent_dict.get("complexity"),
            },
            "sections": sections,
        }
