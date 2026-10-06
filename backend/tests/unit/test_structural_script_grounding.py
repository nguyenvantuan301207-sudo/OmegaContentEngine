"""Unit tests for structural script grounding policies (G1-G8).

Verifies semantic role-based grounding for structural sections (HOOK, PROMISE,
TAKEAWAY, CLOSING, CTA) while enforcing strict evidence grounding for body sections
and preventing generic meta-template filler.
"""

import uuid
from typing import Any
import pytest

from omega.application.content_pacing import DEFAULT_PACE
from omega.application.content_provider import (
    TemplateContentProvider,
    is_body_role,
    is_concluding_structural_role,
    is_intro_structural_role,
    resolve_section_role,
)
from omega.application.content_qa import run_content_qa_checks
from omega.application.script_meta_guard import is_meta_content, script_meta_evidence
from omega.domain.content import ContentStatementType, ScriptQAStatus
from omega.domain.narrative_plan import NarrativeSectionRole

CANONICAL_TOPIC = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"

CLAIM_SHRINKAGE = {
    "claim_id": "133372f7-18b7-48e1-ad43-fbd5540cb569",
    "text": "Plastic shrinkage cracks are caused by a rapid loss of water from the surface of concrete before it has set",
    "citations": [
        {
            "evidence_id": "8905359a-14d4-4f24-9b2f-92931a293674",
            "source_id": "76934c9c-b17b-4b11-a877-bb8968ea15a9",
        }
    ],
}

CLAIM_DRYING = {
    "claim_id": "dffabb3a-1a69-41de-b31d-ae0da51ad355",
    "text": "Most hardened concrete shows evidence of drying shrinkage over its service life",
    "citations": [
        {
            "evidence_id": "8b5fd3ea-ee82-411a-ae57-a9a2a9a7a922",
            "source_id": "b1ecf87e-d007-4e78-953e-5818987b7db0",
        }
    ],
}

CANONICAL_BRIEF = {
    "id": "8f8c8c92-a1d5-4dd7-b86c-ca04ae51254a",
    "topic_title": CANONICAL_TOPIC,
    "title": f"Research Brief: {CANONICAL_TOPIC} (v2)",
    "summary": f"Research briefing for '{CANONICAL_TOPIC}'. Analyzed qualifying sources. Verified 2 factual claims.",
    "verified_claims": [CLAIM_SHRINKAGE, CLAIM_DRYING],
    "contradictions": [],
}


def test_section_role_resolution_and_classification():
    """Verify semantic role resolution from outline attributes and title tags."""
    assert resolve_section_role({"narrative_role": "HOOK"}, 0, 5) == "HOOK"
    assert resolve_section_role({"title": "[HOOK] Question framing"}, 0, 5) == "HOOK"
    assert resolve_section_role({"title": "[PROMISE] Viewer contract"}, 1, 5) == "PROMISE"
    assert resolve_section_role({"title": "[DEVELOPMENT] Core mechanism"}, 2, 5) == "DEVELOPMENT"
    assert resolve_section_role({"title": "[TAKEAWAY] Synthesis"}, 3, 5) == "TAKEAWAY"
    assert resolve_section_role({"title": "[CLOSING] Final thoughts"}, 4, 5) == "CLOSING"

    # Semantic role checks
    assert is_intro_structural_role("HOOK")
    assert is_intro_structural_role("PROMISE")
    assert not is_intro_structural_role("DEVELOPMENT")

    assert is_concluding_structural_role("TAKEAWAY")
    assert is_concluding_structural_role("CLOSING")
    assert is_concluding_structural_role("CTA")
    assert not is_concluding_structural_role("CONTEXT")

    assert is_body_role("DEVELOPMENT")
    assert is_body_role("CONTEXT")
    assert not is_body_role("HOOK")
    assert not is_body_role("CLOSING")


# G1: HOOK WITHOUT DIRECT CLAIM_REFS BUT WITH BRIEF AUTHORITY
def test_g1_hook_without_direct_claims_grounded_via_brief_authority():
    """G1: A HOOK section without direct claim citations succeeds via legitimate topic/brief authority."""
    provider = TemplateContentProvider()
    hook = {
        "text": f"What really causes {CANONICAL_TOPIC} when conventional assumptions fail?",
        "citations": [],
    }
    outline = {
        "sections": [
            {
                "narrative_role": "HOOK",
                "title": "[HOOK] Attention and central question",
                "objective": "Frame the primary dilemma of concrete cracking",
                "key_points": ["Questioning initial assumptions in engineering"],
                "claim_refs": [],
            },
            {
                "narrative_role": "DEVELOPMENT",
                "title": "[DEVELOPMENT] Plastic shrinkage mechanism",
                "objective": "Detail the physics of plastic shrinkage",
                "key_points": ["Rapid moisture evaporation causes tensile distress in fresh concrete"],
                "claim_refs": [str(CLAIM_SHRINKAGE["claim_id"])],
            },
        ]
    }

    script = provider.generate_script(
        topic_title=CANONICAL_TOPIC,
        brief_dict=CANONICAL_BRIEF,
        dna_dict={},
        intent_dict={"pace": DEFAULT_PACE},
        selected_hook=hook,
        outline_dict=outline,
        target_duration_seconds=120,
    )

    assert len(script["sections"]) == 2
    hook_sec = script["sections"][0]
    assert hook_sec["section_order"] == 1
    assert len(hook_sec["statements"]) >= 1
    stmt = hook_sec["statements"][0]
    assert "concrete" in stmt["statement_text"].lower()
    assert stmt["statement_type"] == ContentStatementType.CREATIVE.value
    assert stmt["qualification_note"] is not None
    assert not is_meta_content(stmt["statement_text"])


# G2: HOOK WITH NO AUTHORITY FAILS CLOSED
def test_g2_hook_with_no_authority_fails_closed():
    """G2: A HOOK section with no verified claims, no brief summary, and no hook citations fails closed."""
    provider = TemplateContentProvider()
    empty_brief = {
        "id": str(uuid.uuid4()),
        "verified_claims": [],
        "summary": "",
    }
    unauthorized_hook = {"text": "Why does concrete crack?", "citations": []}
    outline = {
        "sections": [
            {
                "narrative_role": "HOOK",
                "title": "Hook",
                "objective": "Capture attention",
                "key_points": ["Factual detail for HOOK under QUESTION_ANSWER"],
                "claim_refs": [],
            }
        ]
    }

    with pytest.raises(ValueError, match="INSUFFICIENT_GROUNDED_SCRIPT_CONTENT: section 1"):
        provider.generate_script(
            topic_title=CANONICAL_TOPIC,
            brief_dict=empty_brief,
            dna_dict={},
            intent_dict={"pace": DEFAULT_PACE},
            selected_hook=unauthorized_hook,
            outline_dict=outline,
            target_duration_seconds=60,
        )


# G3: BODY WITHOUT GROUNDED CONTENT FAILS CLOSED
def test_g3_body_without_grounded_content_fails_closed():
    """G3: A body section without direct claim_refs, verified claims, or substantive propositions fails closed."""
    provider = TemplateContentProvider()
    hook = {
        "text": f"What really causes {CANONICAL_TOPIC}?",
        "citations": [],
    }
    brief_without_body_claims = {
        "id": CANONICAL_BRIEF["id"],
        "summary": CANONICAL_BRIEF["summary"],
        "verified_claims": [],
    }
    outline = {
        "sections": [
            {
                "narrative_role": "HOOK",
                "title": "[HOOK] Intro",
                "key_points": [],
                "claim_refs": [],
            },
            {
                "narrative_role": "DEVELOPMENT",
                "title": "[DEVELOPMENT] Mechanism",
                "objective": "Explain mechanism",
                # Placeholders only:
                "key_points": ["Factual detail for DEVELOPMENT regarding process"],
                "claim_refs": [],
            },
        ]
    }

    with pytest.raises(ValueError, match="INSUFFICIENT_GROUNDED_SCRIPT_CONTENT: section 2"):
        provider.generate_script(
            topic_title=CANONICAL_TOPIC,
            brief_dict=brief_without_body_claims,
            dna_dict={},
            intent_dict={"pace": DEFAULT_PACE},
            selected_hook=hook,
            outline_dict=outline,
            target_duration_seconds=120,
        )


# G4: CONCLUSION GROUNDED SYNTHESIS PASSES
def test_g4_conclusion_synthesizes_grounded_body_propositions():
    """G4: A concluding section synthesizes previously grounded propositions and passes QA."""
    provider = TemplateContentProvider()
    hook = {"text": f"What really causes {CANONICAL_TOPIC}?", "citations": []}
    outline = {
        "sections": [
            {
                "narrative_role": "HOOK",
                "title": "[HOOK] Introduction",
                "key_points": [],
                "claim_refs": [],
            },
            {
                "narrative_role": "DEVELOPMENT",
                "title": "[DEVELOPMENT] Plastic shrinkage",
                "key_points": [],
                "claim_refs": [str(CLAIM_SHRINKAGE["claim_id"])],
            },
            {
                "narrative_role": "TAKEAWAY",
                "title": "[TAKEAWAY] Engineering synthesis",
                "key_points": [],
                "claim_refs": [],
            },
            {
                "narrative_role": "CLOSING",
                "title": "[CLOSING] Practical resolution",
                "key_points": [],
                "claim_refs": [],
            },
        ]
    }

    topic = "Why Concrete Cracks: Mechanical Degradation and Prevention"
    brief = dict(CANONICAL_BRIEF)
    brief["topic_title"] = topic
    brief["title"] = f"Research Brief: {topic} (v2)"

    script = provider.generate_script(
        topic_title=topic,
        brief_dict=brief,
        dna_dict={},
        intent_dict={"pace": DEFAULT_PACE},
        selected_hook=hook,
        outline_dict=outline,
        target_duration_seconds=240,
    )

    takeaway_sec = script["sections"][2]
    assert len(takeaway_sec["statements"]) >= 1
    t_stmt = takeaway_sec["statements"][0]
    assert "plastic shrinkage" in t_stmt["statement_text"].lower()
    assert len(t_stmt["citations"]) > 0

    closing_sec = script["sections"][3]
    assert len(closing_sec["statements"]) >= 2
    c_stmt = closing_sec["statements"][0]
    cta_stmt = closing_sec["statements"][1]
    assert cta_stmt["statement_type"] == ContentStatementType.CTA.value

    # Validate against canonical QA checks
    status, findings = run_content_qa_checks(
        script_data=script,
        target_duration_seconds=240,
        dna_dict={},
        brief_dict=brief,
        topic_title=topic,
        narrative_plan_dict=outline,
    )
    assert status in (ScriptQAStatus.PASSED, ScriptQAStatus.PASSED_WITH_WARNINGS)
    blocking = [f for f in findings if f.get("severity") in ("BLOCKING", "ERROR")]
    assert not blocking


# G5: CONCLUSION WITH NEW UNSUPPORTED FACT IS BLOCKED
def test_g5_conclusion_with_unsupported_stat_fails_qa():
    """G5: Attempting to introduce an unsupported numerical statistic in conclusion fails QA."""
    script = {
        "title": CANONICAL_TOPIC,
        "sections": [
            {
                "heading": "Recap and Findings",
                "narration_text": "Over 95% of concrete cracks occur due to unmitigated tensile stress.",
                "statements": [
                    {
                        "statement_order": 1,
                        "statement_text": "Over 95% of concrete cracks occur due to unmitigated tensile stress.",
                        "statement_type": ContentStatementType.INTERPRETIVE.value,
                        "citations": [],
                    }
                ],
            }
        ],
    }

    status, findings = run_content_qa_checks(
        script_data=script,
        target_duration_seconds=60,
        dna_dict={},
        brief_dict=CANONICAL_BRIEF,
        topic_title=CANONICAL_TOPIC,
    )
    assert status == ScriptQAStatus.BLOCKED
    assert any(f["rule_code"] == "UNSUPPORTED_STATISTIC" for f in findings)


# G6: META FILLER REMAINS BLOCKED
def test_g6_structural_sections_with_meta_filler_fail():
    """G6: Meta-template filler phrases in structural sections are detected and blocked."""
    meta_phrases = [
        "This analysis examines the problem.",
        "This section explores foundational mechanisms.",
        "Progressively unpack the operational process.",
        "Deliver the empirical resolution.",
    ]
    for phrase in meta_phrases:
        assert is_meta_content(phrase)

    script_with_meta = {
        "title": CANONICAL_TOPIC,
        "hook_text": "This section examines foundational mechanisms.",
        "sections": [
            {
                "heading": "Deliver the empirical resolution",
                "narration_text": "This section examines foundational mechanisms.",
                "statements": [
                    {
                        "statement_order": 1,
                        "statement_text": "This section examines foundational mechanisms.",
                        "statement_type": "INTERPRETIVE",
                        "citations": [],
                    }
                ],
            }
        ],
    }
    evidence = script_meta_evidence(script_with_meta)
    assert len(evidence) > 0


# G7: HISTORICAL P0 SCRIPT META REGRESSION
def test_g7_historical_p0_meta_depth_padding_rejected():
    """G7: Historical 52-phrase failure classes and repetitive prose remain impossible."""
    historical_padding = (
        "Progressively unpack the operational process and deliver the empirical resolution "
        "under foundational mechanisms."
    )
    assert is_meta_content(historical_padding)

    provider = TemplateContentProvider()
    hook = {"text": f"What really causes {CANONICAL_TOPIC}?", "citations": []}
    outline = {
        "sections": [
            {
                "narrative_role": "HOOK",
                "title": "[HOOK] Intro",
                "key_points": [historical_padding],
                "claim_refs": [],
            },
            {
                "narrative_role": "DEVELOPMENT",
                "title": "[DEVELOPMENT] Plastic shrinkage",
                "key_points": [historical_padding],
                "claim_refs": [str(CLAIM_SHRINKAGE["claim_id"])],
            },
        ]
    }
    script = provider.generate_script(
        topic_title=CANONICAL_TOPIC,
        brief_dict=CANONICAL_BRIEF,
        dna_dict={},
        intent_dict={"pace": DEFAULT_PACE},
        selected_hook=hook,
        outline_dict=outline,
        target_duration_seconds=120,
    )
    evidence = script_meta_evidence(script)
    assert not evidence


def test_g8_exact_canary2_structure_succeeds_without_manual_edit():
    """G8: Reproduces the exact Canary #2 flow with NarrativeDirector plan and fresh script generation."""
    from omega.application.narrative_director import DeterministicNarrativeDirector
    from omega.application.narrative_script_adapter import NarrativePlanScriptAdapter
    from omega.domain.narrative_plan import NarrativeFormatProfile, NarrativePlan

    director = DeterministicNarrativeDirector()
    candidates = director.generate_candidates(
        content_request_id=uuid.uuid4(),
        channel_dna={},
        research_brief=CANONICAL_BRIEF,
        content_intent={"primary_goal": "Explain concrete cracking mechanisms", "pace": "MODERATE"},
        topic_title=CANONICAL_TOPIC,
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=360,
    )
    draft = candidates[0]
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        strategy=draft.strategy,
        format_profile=draft.format_profile,
        target_duration_seconds=draft.target_duration_seconds,
        estimated_duration_seconds=draft.estimated_duration_seconds,
        sections=draft.sections,
        rationale=draft.rationale,
    )
    canary2_outline = NarrativePlanScriptAdapter.map_plan_to_script_outline(plan)

    provider = TemplateContentProvider()
    hook = {
        "hook_variant_index": 0,
        "text": f"What really causes {CANONICAL_TOPIC} when conventional assumptions fail?",
        "hook_type": "QUESTION",
        "selected": True,
        "citations": [],
    }

    script = provider.generate_script(
        topic_title=CANONICAL_TOPIC,
        brief_dict=CANONICAL_BRIEF,
        dna_dict={},
        intent_dict={"pace": DEFAULT_PACE},
        selected_hook=hook,
        outline_dict=canary2_outline,
        target_duration_seconds=360,
    )

    # 1. Section 1 (HOOK) produced valid statements
    assert len(script["sections"]) == 6
    sec1 = script["sections"][0]
    assert sec1["section_order"] == 1
    assert len(sec1["statements"]) >= 1
    sec1_text = sec1["statements"][0]["statement_text"]
    assert "concrete" in sec1_text.lower()

    # 2. Section 1 has no meta filler
    assert not is_meta_content(sec1_text)

    # 3. Overall script has zero meta evidence
    assert not script_meta_evidence(script)

    # 4. Content QA fails closed under P0.2 numeric promise contract
    status, findings = run_content_qa_checks(
        script_data=script,
        target_duration_seconds=360,
        dna_dict={},
        brief_dict=CANONICAL_BRIEF,
        topic_title=CANONICAL_TOPIC,
        narrative_plan_dict=canary2_outline,
    )
    # Canary #2 has 2 mechanisms for a title promising 5; P0.2 enforces BLOCKED status
    assert status == ScriptQAStatus.BLOCKED
    promise_finding = [f for f in findings if f["rule_code"] == "NUMERIC_PROMISE_UNFULFILLED"]
    assert len(promise_finding) == 1
    # Verify no structural or meta-filler blocking findings exist
    other_blocking = [
        f for f in findings
        if f["rule_code"] != "NUMERIC_PROMISE_UNFULFILLED" and f.get("severity") in ("BLOCKING", "ERROR")
    ]
    assert not other_blocking, f"Unexpected structural/meta blocking findings: {other_blocking}"


def test_reordered_sections_do_not_rely_on_index():
    """Verify that reordered sections (e.g. body first or hook in different position) follow semantic roles."""
    provider = TemplateContentProvider()
    hook = {"text": f"What really causes {CANONICAL_TOPIC}?", "citations": []}
    reordered_outline = {
        "sections": [
            {
                "narrative_role": "DEVELOPMENT",
                "title": "[DEVELOPMENT] Initial mechanism",
                "key_points": [],
                "claim_refs": [str(CLAIM_SHRINKAGE["claim_id"])],
            },
            {
                "narrative_role": "HOOK",
                "title": "[HOOK] Delayed hook question",
                "key_points": [],
                "claim_refs": [],
            },
        ]
    }
    script = provider.generate_script(
        topic_title=CANONICAL_TOPIC,
        brief_dict=CANONICAL_BRIEF,
        dna_dict={},
        intent_dict={"pace": DEFAULT_PACE},
        selected_hook=hook,
        outline_dict=reordered_outline,
        target_duration_seconds=120,
    )
    assert len(script["sections"]) == 2
    assert script["sections"][0]["statements"][0]["statement_type"] == ContentStatementType.FACTUAL.value
    assert script["sections"][1]["statements"][0]["statement_type"] == ContentStatementType.CREATIVE.value
