"""Content generation provider protocol and deterministic template implementation."""

from __future__ import annotations

import re
from typing import Any, Protocol

from omega.application.content_pacing import (
    DEFAULT_PACE,
    estimate_duration_seconds,
    plan_retention_beats,
)
from omega.application.content_qa import NUMERICAL_REGEX
from omega.application.script_meta_guard import is_meta_content, is_source_proposition
from omega.application.semantic_asset_query import subject_tokens
from omega.domain.content import ContentStatementType, HookType


def resolve_section_role(outline_sec: dict[str, Any], idx: int, total_sections: int) -> str:
    role_val = outline_sec.get("narrative_role") or outline_sec.get("role")
    if role_val:
        return str(role_val).upper()
    title = str(outline_sec.get("title") or "")
    match = re.match(r"^\[([A-Z_]+)\]", title)
    if match:
        return match.group(1).upper()
    title_lower = title.lower()
    if any(k in title_lower for k in ("hook", "intro", "opening")):
        return "HOOK"
    if any(k in title_lower for k in ("promise", "contract")):
        return "PROMISE"
    if any(k in title_lower for k in ("closing", "conclusion", "wrap", "recap", "outro", "summary")):
        return "CLOSING"
    if any(k in title_lower for k in ("takeaway", "synthesis", "actionable")):
        return "TAKEAWAY"
    if "cta" in title_lower:
        return "CTA"
    if "context" in title_lower or "background" in title_lower:
        return "CONTEXT"
    if "payoff" in title_lower or "resolution" in title_lower:
        return "PAYOFF"
    if any(k in title_lower for k in ("development", "mechanism", "finding", "evidence", "practice", "trade-off")):
        return "DEVELOPMENT"
    if total_sections > 1 and idx == 0:
        return "HOOK"
    return "BODY"


def is_intro_structural_role(role: str) -> bool:
    return role in ("HOOK", "PROMISE", "INTRO", "INTRODUCTION")


def is_concluding_structural_role(role: str) -> bool:
    return role in ("TAKEAWAY", "CLOSING", "CTA", "CONCLUSION", "SYNTHESIS")


def is_body_role(role: str) -> bool:
    return not is_intro_structural_role(role) and not is_concluding_structural_role(role)


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
        verified_claims = brief_dict.get("verified_claims", []) or []
        brief_summary = brief_dict.get("summary") or ""
        brief_id = brief_dict.get("id")

        hook_text = (
            selected_hook.get("text")
            or selected_hook.get("hook_text")
            or f"Let us explore {topic_title}."
        )
        outline_sections = outline_dict.get("sections", [])
        num_sections = max(1, len(outline_sections))
        sec_duration = target_duration_seconds // num_sections
        beats = plan_retention_beats(target_duration_seconds, num_sections)

        claim_map = {str(c.get("claim_id")): c for c in verified_claims if c.get("claim_id")}

        sections: list[dict[str, Any]] = []
        grounded_body_claims: list[tuple[str, list[dict[str, Any]]]] = []
        topic_words = set(subject_tokens(topic_title))

        body_section_indices = [
            i for i, s in enumerate(outline_sections)
            if is_body_role(resolve_section_role(s, i, len(outline_sections)))
        ]

        used_body_claim_ids: set[str] = set()

        for idx, outline_sec in enumerate(outline_sections):
            heading = outline_sec.get("title", f"Section {idx + 1}")
            key_points = outline_sec.get("key_points") or [heading]
            claim_refs = set(outline_sec.get("claim_refs") or [])
            role = resolve_section_role(outline_sec, idx, len(outline_sections))

            if is_meta_content(heading):
                role_label = role.title() if role else f"Section {idx + 1}"
                heading = f"{role_label} {idx + 1}: {topic_title}"

            sec_verified_claims = [claim_map[cid] for cid in claim_refs if cid in claim_map]

            raw_stmts: list[tuple[str, ContentStatementType, str | None, list[dict[str, Any]]]] = []

            if is_body_role(role):
                # ── B1: Body / Explanatory Sections ──
                if not sec_verified_claims and verified_claims:
                    body_pos = body_section_indices.index(idx) if idx in body_section_indices else 0
                    if 0 <= body_pos < len(verified_claims):
                        sec_verified_claims = [verified_claims[body_pos]]

                sec_verified_claims = [
                    vc for vc in sec_verified_claims
                    if str(vc.get("claim_id")) not in used_body_claim_ids
                ]

                # Preserve exact claims and citations. Never increase duration by padding.
                for vc in sec_verified_claims:
                    claim_text = vc.get("text") or vc.get("claim_text", "")
                    if not claim_text.strip() or is_meta_content(claim_text):
                        continue
                    cits = [{
                        "research_brief_id": brief_id,
                        "claim_id": vc.get("claim_id"),
                        "evidence_id": cit.get("evidence_id"),
                        "source_id": cit.get("source_id"),
                    } for cit in vc.get("citations", []) if isinstance(cit, dict)]
                    raw_stmts.append((claim_text, ContentStatementType.FACTUAL, None, cits))
                    grounded_body_claims.append((claim_text, cits))
                    if vc.get("claim_id"):
                        used_body_claim_ids.add(str(vc.get("claim_id")))

                # Source key points may be used verbatim only when they are substantive
                # subject-specific prose, not plan role placeholders or instructions.
                for kp in key_points:
                    if not is_source_proposition(kp) or not topic_words.intersection(subject_tokens(kp)):
                        continue
                    if len(kp.split()) < 6 or kp.lower().startswith(("factual detail", "core mechanism", "key detail")):
                        continue
                    if kp.strip() not in [t for t, *_ in raw_stmts]:
                        raw_stmts.append((kp.strip(), ContentStatementType.INTERPRETIVE, None, []))
                        grounded_body_claims.append((kp.strip(), []))

                if not raw_stmts:
                    raise ValueError(f"INSUFFICIENT_GROUNDED_SCRIPT_CONTENT: section {idx + 1}")

            elif is_intro_structural_role(role):
                # ── B2: Hook / Intro Structural Sections ──
                has_claim_auth = bool(verified_claims)
                has_summary_auth = bool(
                    brief_summary
                    and len(brief_summary.strip()) >= 10
                    and (topic_words & set(subject_tokens(brief_summary)))
                )
                hook_cand = (selected_hook.get("text") or selected_hook.get("hook_text") or "").strip()
                hook_cits = [{
                    "research_brief_id": brief_id,
                    "claim_id": c.get("claim_id"),
                    "evidence_id": c.get("evidence_id"),
                    "source_id": c.get("source_id"),
                } for c in selected_hook.get("citations", []) if isinstance(c, dict)]
                has_hook_auth = bool(
                    hook_cand
                    and (topic_words & set(subject_tokens(hook_cand)))
                    and (has_claim_auth or has_summary_auth or hook_cits)
                )

                if not (has_claim_auth or has_summary_auth or has_hook_auth):
                    raise ValueError(f"INSUFFICIENT_GROUNDED_SCRIPT_CONTENT: section {idx + 1}")

                for vc in sec_verified_claims:
                    claim_text = vc.get("text") or vc.get("claim_text", "")
                    if not claim_text.strip() or is_meta_content(claim_text):
                        continue
                    cits = [{
                        "research_brief_id": brief_id,
                        "claim_id": vc.get("claim_id"),
                        "evidence_id": cit.get("evidence_id"),
                        "source_id": cit.get("source_id"),
                    } for cit in vc.get("citations", []) if isinstance(cit, dict)]
                    raw_stmts.append((claim_text, ContentStatementType.FACTUAL, None, cits))

                if role in ("HOOK", "INTRO", "INTRODUCTION") and not raw_stmts:
                    if has_hook_auth and not is_meta_content(hook_cand) and not NUMERICAL_REGEX.search(hook_cand):
                        s_type = ContentStatementType.FACTUAL if hook_cits else ContentStatementType.CREATIVE
                        qual = f"Hook authority grounded in '{topic_title}'"
                        raw_stmts.append((hook_cand, s_type, qual, hook_cits))
                    elif has_claim_auth:
                        top_c = verified_claims[0]
                        c_text = top_c.get("text") or top_c.get("claim_text", "")
                        if c_text and not is_meta_content(c_text):
                            cits = [{
                                "research_brief_id": brief_id,
                                "claim_id": top_c.get("claim_id"),
                                "evidence_id": cit.get("evidence_id"),
                                "source_id": cit.get("source_id"),
                            } for cit in top_c.get("citations", []) if isinstance(cit, dict)]
                            raw_stmts.append((
                                f"Understanding {topic_title} begins with verified research: {c_text.rstrip('.')}.",
                                ContentStatementType.FACTUAL,
                                f"Opening hook derived from research brief {brief_id}",
                                cits,
                            ))

                elif role == "PROMISE" and not raw_stmts:
                    promise_stmt = (
                        f"We will examine the foundational factors behind {topic_title} "
                        "through engineering observations and field analysis."
                    )
                    raw_stmts.append((
                        promise_stmt,
                        ContentStatementType.INTERPRETIVE,
                        f"Grounded promise contract for topic '{topic_title}'",
                        [],
                    ))

                for kp in key_points:
                    if not is_source_proposition(kp) or not topic_words.intersection(subject_tokens(kp)):
                        continue
                    if len(kp.split()) < 6 or kp.lower().startswith(("factual detail", "core mechanism", "key detail")):
                        continue
                    if kp.strip() not in [t for t, *_ in raw_stmts]:
                        raw_stmts.append((kp.strip(), ContentStatementType.INTERPRETIVE, None, []))

                if not raw_stmts:
                    raise ValueError(f"INSUFFICIENT_GROUNDED_SCRIPT_CONTENT: section {idx + 1}")

            elif is_concluding_structural_role(role):
                # ── B3: Conclusion / CTA Structural Sections ──
                has_claim_auth = bool(verified_claims or grounded_body_claims)
                has_summary_auth = bool(
                    brief_summary
                    and len(brief_summary.strip()) >= 10
                    and (topic_words & set(subject_tokens(brief_summary)))
                )
                if not (has_claim_auth or has_summary_auth):
                    raise ValueError(f"INSUFFICIENT_GROUNDED_SCRIPT_CONTENT: section {idx + 1}")

                for vc in sec_verified_claims:
                    claim_text = vc.get("text") or vc.get("claim_text", "")
                    if not claim_text.strip() or is_meta_content(claim_text):
                        continue
                    cits = [{
                        "research_brief_id": brief_id,
                        "claim_id": vc.get("claim_id"),
                        "evidence_id": cit.get("evidence_id"),
                        "source_id": cit.get("source_id"),
                    } for cit in vc.get("citations", []) if isinstance(cit, dict)]
                    raw_stmts.append((claim_text, ContentStatementType.FACTUAL, None, cits))

                if role in ("TAKEAWAY", "SYNTHESIS") and not raw_stmts:
                    source_claims = grounded_body_claims or [
                        (c.get("text") or c.get("claim_text", ""), [
                            {
                                "research_brief_id": brief_id,
                                "claim_id": c.get("claim_id"),
                                "evidence_id": cit.get("evidence_id"),
                                "source_id": cit.get("source_id"),
                            }
                            for cit in c.get("citations", [])
                            if isinstance(cit, dict)
                        ])
                        for c in verified_claims
                    ]
                    if source_claims:
                        synth_claims = [c_text.rstrip(".") for c_text, _ in source_claims[:2] if c_text]
                        if synth_claims:
                            synth_stmt = (
                                f"Analyzing {topic_title} requires accounting for verified mechanisms: "
                                + "; ".join(synth_claims)
                                + "."
                            )
                            synth_cits = [cit for _, cits in source_claims[:2] for cit in cits]
                            raw_stmts.append((
                                synth_stmt,
                                ContentStatementType.INTERPRETIVE,
                                f"Synthesis of grounded mechanisms for '{topic_title}'",
                                synth_cits,
                            ))

                elif role in ("CLOSING", "CONCLUSION") and not raw_stmts:
                    closing_stmt = (
                        f"Rigorous analysis requires identifying these specific factors early "
                        f"and applying verified controls to {topic_title}."
                    )
                    raw_stmts.append((
                        closing_stmt,
                        ContentStatementType.INTERPRETIVE,
                        f"Closing synthesis for '{topic_title}'",
                        [],
                    ))
                    cta_stmt = (
                        "Review the research brief citations in the description, inspect evidence carefully, "
                        "and subscribe for further detailed breakdowns."
                    )
                    raw_stmts.append((
                        cta_stmt,
                        ContentStatementType.CTA,
                        f"Viewer call-to-action for '{topic_title}'",
                        [],
                    ))

                elif role == "CTA" and not raw_stmts:
                    cta_stmt = (
                        "Review the research brief citations in the description, inspect evidence carefully, "
                        "and subscribe for further detailed breakdowns."
                    )
                    raw_stmts.append((
                        cta_stmt,
                        ContentStatementType.CTA,
                        f"Viewer call-to-action for '{topic_title}'",
                        [],
                    ))

                for kp in key_points:
                    if not is_source_proposition(kp) or not topic_words.intersection(subject_tokens(kp)):
                        continue
                    if len(kp.split()) < 6 or kp.lower().startswith(("factual detail", "core mechanism", "key detail")):
                        continue
                    if kp.strip() not in [t for t, *_ in raw_stmts]:
                        raw_stmts.append((kp.strip(), ContentStatementType.INTERPRETIVE, None, []))

                if not raw_stmts:
                    raise ValueError(f"INSUFFICIENT_GROUNDED_SCRIPT_CONTENT: section {idx + 1}")

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
