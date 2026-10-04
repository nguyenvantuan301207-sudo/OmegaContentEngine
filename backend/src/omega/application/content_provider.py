"""Content generation provider protocol and deterministic template implementation."""

from __future__ import annotations

from typing import Any, Protocol

from omega.application.content_pacing import (
    DEFAULT_PACE,
    estimate_duration_seconds,
    estimate_target_word_count,
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

        direction_note = f" Emphasis: {creative_direction}." if creative_direction else ""

        return {
            "primary_goal": f"Deliver a clear, evidence-based breakdown of {topic_title}.{direction_note}",
            "audience_intent": f"Understand core principles, verified mechanisms, and empirical findings regarding {topic_title}.",
            "viewer_promise": f"By the end of this video, you will understand the evidence-backed reality behind {topic_title}.",
            "central_question": f"What are the verified facts and critical mechanisms behind {topic_title}?",
            "core_takeaway": brief_dict.get("summary") or topic_summary or f"Key takeaways on {topic_title}.",
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
                "text": f"What really causes {topic_title} when conventional assumptions fail?",
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
                "text": f"Here is the empirical reality of {topic_title} backed by research evidence.",
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
            claim_text = top_claim.get("text") or top_claim.get("claim_text", "")
            citations = [
                {
                    "research_brief_id": brief_dict.get("id"),
                    "claim_id": top_claim.get("claim_id"),
                    "evidence_id": cit.get("evidence_id"),
                    "source_id": cit.get("source_id"),
                }
                for cit in top_claim.get("citations", [])
            ]
            hooks.append(
                {
                    "hook_variant_index": 2,
                    "text": f"Proven finding: {claim_text}",
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
                    "text": f"Why common assumptions about {topic_title} break down in practice.",
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
        verified_claims = brief_dict.get("verified_claims", [])
        if target_duration_seconds < 240:
            num_sections = 4
            sec_duration = max(20, target_duration_seconds // num_sections)
            sections = [
                {
                    "section_id": "sec_intro",
                    "title": f"Context & Core Concepts of {topic_title}",
                    "objective": f"Establish the context, foundational principles, and core challenge of {topic_title}.",
                    "key_points": [f"Background context for {topic_title}", "Foundational principles"],
                    "claim_refs": [],
                    "estimated_duration_seconds": sec_duration,
                    "transition": "Let us examine the foundational mechanisms.",
                    "retention_goal": "Maintain opening viewer interest.",
                },
                {
                    "section_id": "sec_core",
                    "title": f"Core Mechanisms & Operating Principles",
                    "objective": f"Break down verified mechanics and core principles of {topic_title}.",
                    "key_points": [f"Primary mechanisms of {topic_title}", "Operating dynamics"],
                    "claim_refs": [str(verified_claims[0]["claim_id"])] if verified_claims else [],
                    "estimated_duration_seconds": sec_duration,
                    "transition": "Now look at the empirical evidence.",
                    "retention_goal": "Deliver primary analytical depth.",
                },
                {
                    "section_id": "sec_evidence",
                    "title": f"Empirical Evidence & Verified Findings",
                    "objective": f"Present validated findings and research evidence on {topic_title}.",
                    "key_points": [f"Verified data on {topic_title}", "Research observations"],
                    "claim_refs": [str(c["claim_id"]) for c in verified_claims[1:2]] if len(verified_claims) > 1 else ([str(verified_claims[0]["claim_id"])] if verified_claims else []),
                    "estimated_duration_seconds": sec_duration,
                    "transition": "Here is how to apply this in practice.",
                    "retention_goal": "Resolve open questions with solid data.",
                },
                {
                    "section_id": "sec_conclusion",
                    "title": f"Practical Takeaways & Summary",
                    "objective": f"Actionable recommendations and synthesis for {topic_title}.",
                    "key_points": [f"Key takeaways for {topic_title}", "Recommended practices"],
                    "claim_refs": [],
                    "estimated_duration_seconds": sec_duration,
                    "transition": "Final takeaway.",
                    "retention_goal": "High satisfaction and call-to-action follow-through.",
                },
            ]
        else:
            num_sections = min(7, max(3, 3 + (target_duration_seconds - 480) // 240))
            sec_duration = target_duration_seconds // num_sections
            dynamic_sections = [
                {
                    "section_id": "sec_hook_setup",
                    "title": f"Problem Context & Fundamental Challenges of {topic_title}",
                    "objective": f"Establish the context, foundational principles, and core challenge of {topic_title}.",
                    "key_points": [
                        f"Fundamental challenges of {topic_title}",
                        "Common misconceptions and failure points",
                        "Roadmap of evidence-backed analysis",
                    ],
                    "claim_refs": [],
                    "estimated_duration_seconds": sec_duration,
                    "transition": "Let us examine the underlying mechanics.",
                    "retention_goal": "Engage audience and establish concrete value promise.",
                },
                {
                    "section_id": "sec_ch1_mechanisms",
                    "title": f"Foundational Mechanisms & Driving Factors",
                    "objective": f"Explain the core mechanisms, driving dynamics, and verified behavior of {topic_title}.",
                    "key_points": [
                        f"Primary mechanisms governing {topic_title}",
                        "Governing factors and variable interactions",
                        "Observable conditions and behaviors",
                    ],
                    "claim_refs": [str(verified_claims[0]["claim_id"])] if verified_claims else [],
                    "estimated_duration_seconds": sec_duration,
                    "transition": "Next, let us evaluate empirical research and measured findings.",
                    "retention_goal": "Deep analytical foundation with clear conceptual clarity.",
                },
                {
                    "section_id": "sec_ch2_evidence",
                    "title": f"Empirical Findings & Verified Research Data",
                    "objective": f"Examine verified research data, controlled observations, and evidence on {topic_title}.",
                    "key_points": [
                        f"Empirical observations on {topic_title}",
                        "Verified research findings and measured data",
                        "Data consistency across field and lab studies",
                    ],
                    "claim_refs": [str(c["claim_id"]) for c in verified_claims[1:2]] if len(verified_claims) > 1 else ([str(verified_claims[0]["claim_id"])] if verified_claims else []),
                    "estimated_duration_seconds": sec_duration,
                    "transition": "Now let us examine practical applications and prevention strategies.",
                    "retention_goal": "Authoritative research backing up foundational claims.",
                },
                {
                    "section_id": "sec_ch3_practice",
                    "title": f"Practical Application & Risk Mitigation",
                    "objective": f"Analyze preventative techniques, mitigation strategies, and practical execution for {topic_title}.",
                    "key_points": [
                        f"Mitigation strategies for {topic_title}",
                        "Best practices and standard procedures",
                        "Critical inspection and quality criteria",
                    ],
                    "claim_refs": [],
                    "estimated_duration_seconds": sec_duration,
                    "transition": "With these practices established, we examine critical trade-offs.",
                    "retention_goal": "Practical practitioner value via actionable techniques.",
                },
                {
                    "section_id": "sec_ch4_tradeoffs",
                    "title": f"Critical Trade-offs & Boundary Conditions",
                    "objective": f"Explore boundary conditions, secondary factors, and real-world trade-offs in {topic_title}.",
                    "key_points": [
                        f"Boundary limits for {topic_title}",
                        "Balancing competing constraints and demands",
                        "Risk containment and edge cases",
                    ],
                    "claim_refs": [],
                    "estimated_duration_seconds": sec_duration,
                    "transition": "Let us synthesize our findings into an actionable summary.",
                    "retention_goal": "Disciplined evaluation of constraints and limits.",
                },
                {
                    "section_id": "sec_recap_outro",
                    "title": f"Synthesis, Key Takeaways & Practical Recommendations",
                    "objective": f"Synthesize core principles and deliver clear, actionable recommendations for {topic_title}.",
                    "key_points": [
                        f"Summary of primary findings on {topic_title}",
                        "Core checklist before execution",
                        "Closing perspective and discussion",
                    ],
                    "claim_refs": [],
                    "estimated_duration_seconds": sec_duration,
                    "transition": "Final summary.",
                    "retention_goal": "High satisfaction, clear next actions, and community discussion.",
                },
            ]
            sections = dynamic_sections[:num_sections]

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

        hook_text = (
            selected_hook.get("text")
            or selected_hook.get("hook_text")
            or f"Let us explore {topic_title}."
        )
        outline_sections = outline_dict.get("sections", [])
        num_sections = max(1, len(outline_sections))
        sec_duration = target_duration_seconds // num_sections
        beats = plan_retention_beats(target_duration_seconds, num_sections)

        target_words = estimate_target_word_count(target_duration_seconds, pace)
        words_per_section = max(20, target_words // num_sections)

        domain_neutral_lenses = (
            "the primary operational factors and observed boundary conditions",
            "observable indicators and variables that practitioners should monitor",
            "the causal progression and preventative safeguards",
            "trade-offs between immediate intervention and long-term stability",
            "structured inspection criteria based on verified evidence",
            "connecting these documented observations to disciplined decision-making",
            "the underlying stages, physical dynamics, and material relationships",
            "synthesizing the evidence-backed priorities for execution",
        )

        claim_map = {str(c.get("claim_id")): c for c in verified_claims if c.get("claim_id")}

        sections: list[dict[str, Any]] = []

        for idx, outline_sec in enumerate(outline_sections):
            heading = outline_sec.get("title", f"Section {idx + 1}")
            objective = outline_sec.get("objective", f"Examine {heading}.")
            key_points = outline_sec.get("key_points") or [heading]
            claim_refs = set(outline_sec.get("claim_refs") or [])

            sec_verified_claims = [claim_map[cid] for cid in claim_refs if cid in claim_map]
            if not sec_verified_claims and verified_claims:
                assigned_idx = idx - 1 if idx > 0 else 0
                if 0 <= assigned_idx < len(verified_claims) and idx not in (0, len(outline_sections) - 1):
                    sec_verified_claims = [verified_claims[assigned_idx]]

            raw_stmts: list[tuple[str, ContentStatementType, str | None, list[dict[str, Any]]]] = []

            # 1. Orientation / Transition
            if idx == 0:
                trans_text = f"In this analysis, we examine {objective.lower().rstrip('.')}."
            else:
                trans_text = (
                    outline_sec.get("transition")
                    or f"Moving forward, we examine {objective.lower().rstrip('.')}."
                )
            raw_stmts.append((trans_text, ContentStatementType.TRANSITION, None, []))

            # 2. Verified factual claims with exact citations
            for vc in sec_verified_claims:
                claim_text = vc.get("text") or vc.get("claim_text", "")
                cits = [
                    {
                        "research_brief_id": brief_dict.get("id"),
                        "claim_id": vc.get("claim_id"),
                        "evidence_id": cit.get("evidence_id"),
                        "source_id": cit.get("source_id"),
                    }
                    for cit in vc.get("citations", [])
                ]
                raw_stmts.append(
                    (f"Research confirms: {claim_text}", ContentStatementType.FACTUAL, None, cits)
                )

            # 3. Grounded key points
            for kp in key_points:
                kp_clean = kp.rstrip(".")
                text = (
                    f"Regarding {kp_clean.lower()}, practitioners must evaluate the operational process, "
                    f"foundational mechanisms, and core components that govern {topic_title}."
                )
                raw_stmts.append((text, ContentStatementType.INTERPRETIVE, None, []))

            # 4. Word count & depth scaling
            current_words = sum(len(t.split()) for t, *_ in raw_stmts)
            lens_index = 0
            while current_words < words_per_section and lens_index < len(domain_neutral_lenses):
                kp_clean = key_points[lens_index % len(key_points)].rstrip(".")
                lens = domain_neutral_lenses[lens_index]
                text = (
                    f"For {kp_clean.lower()}, this section examines {lens}. "
                    f"The analysis connects the specific behavior of {topic_title} to verified "
                    "principles, identifies what would challenge working assumptions, "
                    "and explains how practitioners can apply the finding with rigorous care."
                )
                raw_stmts.append((text, ContentStatementType.INTERPRETIVE, None, []))
                current_words += len(text.split())
                lens_index += 1

            statements = []
            for s_idx, (st_text, s_type, q_note, cits) in enumerate(raw_stmts):
                statements.append(
                    {
                        "statement_order": s_idx + 1,
                        "statement_text": st_text,
                        "statement_type": s_type.value,
                        "qualification_note": q_note,
                        "citations": cits,
                    }
                )

            narration = " ".join(s["statement_text"] for s in statements)
            section_duration = (
                outline_sec.get("estimated_duration_seconds", sec_duration)
                if outline_dict.get("narrative_plan_id")
                else sec_duration
            )

            sections.append(
                {
                    "section_order": idx + 1,
                    "heading": heading,
                    "narration_text": narration,
                    "estimated_duration_seconds": section_duration,
                    "transition_text": outline_sec.get("transition"),
                    "retention_beat": beats[idx] if idx < len(beats) else None,
                    "statements": statements,
                }
            )

        closing_text = (
            f"Thank you for exploring this detailed analysis of {topic_title}. "
            "Achieving consistent results requires continuous observation, disciplined methodology, and reliance on verified evidence."
        )
        cta_text = (
            f"If you found this breakdown of {topic_title} valuable, subscribe to the channel, "
            "share your perspective in the comments, and review the referenced research in the description."
        )

        all_words = (
            f"{hook_text} {' '.join(s['narration_text'] for s in sections)} {closing_text} {cta_text}".split()
        )
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
