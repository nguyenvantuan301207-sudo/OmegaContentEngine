"""Comprehensive unit tests for P21-A Narrative Architecture.

Covers domain models, format profiles, validation engine, promise/payoff contracts,
source grounding, versioning, serialization, backward compatibility, and script/storyboard integration.
"""

import json
import uuid
from datetime import UTC, datetime

import pytest

from omega.application.narrative_plan_service import (
    InMemoryNarrativePlanRepository,
    NarrativePlanService,
)
from omega.application.narrative_plan_validator import NarrativePlanValidator
from omega.application.narrative_script_adapter import (
    NarrativePlanScriptAdapter,
    ScriptGenerationContext,
)
from omega.application.storyboard_engine import StoryboardEngine, StoryboardPlan
from omega.domain.content import (
    ContentIntentResponse,
    ScriptQAStatus,
    ScriptSectionResponse,
    ScriptVersionResponse,
    ScriptVersionSummaryResponse,
)
from omega.domain.narrative_plan import (
    FORMAT_PROFILE_CONSTRAINTS,
    GroundingReference,
    GroundingType,
    InformationDensity,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativeRuleCode,
    NarrativeSection,
    NarrativeSectionRole,
    NarrativeSeverity,
    NarrativeValidationStatus,
)


# ── Helpers ──


def make_valid_short_sections() -> list[NarrativeSection]:
    return [
        NarrativeSection(
            section_order=1,
            role=NarrativeSectionRole.HOOK,
            objective="Hook the audience with immediate visual paradox",
            key_information=["Paradoxical metric"],
            target_duration_seconds=8,
            promise_id="p_short_1",
        ),
        NarrativeSection(
            section_order=2,
            role=NarrativeSectionRole.DEVELOPMENT,
            objective="Deliver immediate proof and mechanism",
            key_information=["Mechanism explanation"],
            target_duration_seconds=20,
        ),
        NarrativeSection(
            section_order=3,
            role=NarrativeSectionRole.PAYOFF,
            objective="Resolve the opening question conclusively",
            key_information=["Resolution"],
            target_duration_seconds=10,
            payoff_reference="p_short_1",
        ),
        NarrativeSection(
            section_order=4,
            role=NarrativeSectionRole.TAKEAWAY,
            objective="One-sentence actionable takeaway",
            key_information=["Actionable tip"],
            target_duration_seconds=7,
        ),
    ]


def make_valid_medium_sections() -> list[NarrativeSection]:
    brief_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    return [
        NarrativeSection(
            section_order=1,
            role=NarrativeSectionRole.HOOK,
            objective="Hook viewer curiosity with strange counter-intuitive event",
            target_duration_seconds=25,
            promise_id="med_main_loop",
        ),
        NarrativeSection(
            section_order=2,
            role=NarrativeSectionRole.CONTEXT,
            objective="Establish baseline environment and core historical context",
            target_duration_seconds=50,
            grounding_references=[
                GroundingReference(
                    research_brief_id=brief_id,
                    claim_id=claim_id,
                    grounding_type=GroundingType.BACKGROUND,
                )
            ],
        ),
        NarrativeSection(
            section_order=3,
            role=NarrativeSectionRole.DEVELOPMENT,
            objective="Unpack first major technical breakthrough",
            target_duration_seconds=80,
            grounding_references=[
                GroundingReference(
                    research_brief_id=brief_id,
                    claim_id=claim_id,
                    grounding_type=GroundingType.FACTUAL,
                )
            ],
        ),
        NarrativeSection(
            section_order=4,
            role=NarrativeSectionRole.PAYOFF,
            objective="Resolve the core question opened in the hook",
            target_duration_seconds=60,
            payoff_reference="med_main_loop",
            grounding_references=[
                GroundingReference(
                    research_brief_id=brief_id,
                    claim_id=claim_id,
                    grounding_type=GroundingType.DATA_POINT,
                )
            ],
        ),
        NarrativeSection(
            section_order=5,
            role=NarrativeSectionRole.TAKEAWAY,
            objective="Summarize key implications and principles",
            target_duration_seconds=50,
        ),
        NarrativeSection(
            section_order=6,
            role=NarrativeSectionRole.CLOSING,
            objective="Concluding thought and clean exit",
            target_duration_seconds=35,
        ),
    ]


def make_valid_long_sections() -> list[NarrativeSection]:
    return [
        NarrativeSection(
            section_order=1,
            role=NarrativeSectionRole.HOOK,
            objective="Cinematic cold open with dramatic mystery",
            target_duration_seconds=45,
            promise_id="long_mystery",
        ),
        NarrativeSection(
            section_order=2,
            role=NarrativeSectionRole.PROMISE,
            objective="Explicit viewer contract outlining journey ahead",
            target_duration_seconds=40,
            open_loop_intent="Promise deep dive into secret failure",
        ),
        NarrativeSection(
            section_order=3,
            role=NarrativeSectionRole.CONTEXT,
            objective="Deep foundational history and initial constraints",
            target_duration_seconds=90,
        ),
        NarrativeSection(
            section_order=4,
            role=NarrativeSectionRole.DEVELOPMENT,
            objective="First phase: experimentation and early success",
            target_duration_seconds=110,
        ),
        NarrativeSection(
            section_order=5,
            role=NarrativeSectionRole.ESCALATION,
            objective="Critical crisis: system breakdown and unexpected failure",
            target_duration_seconds=120,
        ),
        NarrativeSection(
            section_order=6,
            role=NarrativeSectionRole.PAYOFF,
            objective="Resolution: how the mystery was ultimately solved",
            target_duration_seconds=130,
            payoff_reference="long_mystery",
        ),
        NarrativeSection(
            section_order=7,
            role=NarrativeSectionRole.TAKEAWAY,
            objective="Systemic architectural lessons and takeaways",
            target_duration_seconds=100,
        ),
        NarrativeSection(
            section_order=8,
            role=NarrativeSectionRole.CLOSING,
            objective="Forward-looking synthesis and final sign-off",
            target_duration_seconds=85,
        ),
    ]


# ── Test Suite A: Domain Model & Format Profiles ──


def test_valid_short_plan():
    sections = make_valid_short_sections()
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        estimated_duration_seconds=sum(s.target_duration_seconds for s in sections),
        sections=sections,
    )
    validator = NarrativePlanValidator()
    result = validator.validate(plan)
    assert result.status == NarrativeValidationStatus.PASSED
    assert len(result.findings) == 0


def test_valid_medium_plan():
    sections = make_valid_medium_sections()
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=300,
        estimated_duration_seconds=sum(s.target_duration_seconds for s in sections),
        sections=sections,
    )
    validator = NarrativePlanValidator()
    result = validator.validate(plan)
    assert result.status == NarrativeValidationStatus.PASSED


def test_valid_long_plan():
    sections = make_valid_long_sections()
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.LONG,
        target_duration_seconds=720,
        estimated_duration_seconds=sum(s.target_duration_seconds for s in sections),
        sections=sections,
    )
    validator = NarrativePlanValidator()
    result = validator.validate(plan)
    assert result.status == NarrativeValidationStatus.PASSED


def test_ordering_gap_detection():
    sections = make_valid_short_sections()
    # Introduce gap: 1, 2, 4, 5
    sections[2].section_order = 4
    sections[3].section_order = 5
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        estimated_duration_seconds=45,
        sections=sections,
    )
    validator = NarrativePlanValidator()
    result = validator.validate(plan)
    assert result.status == NarrativeValidationStatus.BLOCKED
    assert any(f.rule_code == NarrativeRuleCode.ORDERING_GAP for f in result.findings)


def test_duplicate_section_order():
    sections = make_valid_short_sections()
    sections[1].section_order = 1  # Duplicate section_order 1
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        estimated_duration_seconds=45,
        sections=sections,
    )
    validator = NarrativePlanValidator()
    result = validator.validate(plan)
    assert result.status == NarrativeValidationStatus.BLOCKED
    assert any(f.rule_code == NarrativeRuleCode.DUPLICATE_SECTION_ORDER for f in result.findings)


def test_hook_not_first():
    sections = make_valid_short_sections()
    # Swap section 1 and 2
    sections[0].role = NarrativeSectionRole.DEVELOPMENT
    sections[1].role = NarrativeSectionRole.HOOK
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        estimated_duration_seconds=45,
        sections=sections,
    )
    validator = NarrativePlanValidator()
    result = validator.validate(plan)
    assert result.status == NarrativeValidationStatus.BLOCKED
    assert any(f.rule_code == NarrativeRuleCode.HOOK_NOT_FIRST for f in result.findings)


def test_missing_required_role():
    sections = make_valid_medium_sections()
    # Remove CLOSING role
    sections = [s for s in sections if s.role != NarrativeSectionRole.CLOSING]
    for idx, s in enumerate(sections, 1):
        s.section_order = idx

    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=265,
        estimated_duration_seconds=265,
        sections=sections,
    )
    validator = NarrativePlanValidator()
    result = validator.validate(plan)
    assert result.status == NarrativeValidationStatus.BLOCKED
    assert any(f.rule_code == NarrativeRuleCode.MISSING_REQUIRED_ROLE for f in result.findings)


def test_duration_bounds_and_tolerance():
    sections = make_valid_short_sections()
    # SHORT target must be <= 60
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=120,  # Invalid for SHORT
        estimated_duration_seconds=45,
        sections=sections,
    )
    validator = NarrativePlanValidator()
    result = validator.validate(plan)
    assert result.status == NarrativeValidationStatus.BLOCKED
    assert any(f.rule_code == NarrativeRuleCode.DURATION_OUT_OF_BOUNDS for f in result.findings)


def test_serialization_round_trip():
    sections = make_valid_medium_sections()
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=300,
        estimated_duration_seconds=300,
        sections=sections,
    )
    data = plan.to_serializable_dict()
    assert data["schema_version"] == 1
    assert data["format_profile"] == "MEDIUM"
    assert len(data["sections"]) == 6

    # Restore from dict
    restored = NarrativePlan.from_serializable_dict(data)
    assert restored.id == plan.id
    assert restored.format_profile == plan.format_profile
    assert len(restored.sections) == 6
    assert restored.sections[0].role == NarrativeSectionRole.HOOK


# ── Test Suite B: Promise / Payoff Integrity ──


def test_unresolved_promise_detected():
    sections = make_valid_short_sections()
    # Break payoff reference
    sections[2].payoff_reference = None
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        estimated_duration_seconds=45,
        sections=sections,
    )
    validator = NarrativePlanValidator()
    result = validator.validate(plan)
    assert result.status == NarrativeValidationStatus.BLOCKED
    assert any(f.rule_code == NarrativeRuleCode.UNRESOLVED_PROMISE for f in result.findings)


def test_orphan_payoff_detected():
    sections = make_valid_short_sections()
    # Payoff references non-existent promise
    sections[2].payoff_reference = "completely_unknown_loop"
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        estimated_duration_seconds=45,
        sections=sections,
    )
    validator = NarrativePlanValidator()
    result = validator.validate(plan)
    assert result.status == NarrativeValidationStatus.BLOCKED
    assert any(f.rule_code == NarrativeRuleCode.ORPHAN_PAYOFF for f in result.findings)


def test_payoff_before_promise_detected():
    sections = make_valid_short_sections()
    # Section 1 has payoff for promise declared in Section 2
    sections[0].payoff_reference = "loop_2"
    sections[0].promise_id = None
    sections[1].promise_id = "loop_2"
    sections[2].payoff_reference = None
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        estimated_duration_seconds=45,
        sections=sections,
    )
    validator = NarrativePlanValidator()
    result = validator.validate(plan)
    assert result.status == NarrativeValidationStatus.BLOCKED
    assert any(f.rule_code == NarrativeRuleCode.PAYOFF_BEFORE_PROMISE for f in result.findings)


def test_duplicate_promise_id_detected():
    sections = make_valid_short_sections()
    sections[1].promise_id = "p_short_1"  # Same as section 0
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        estimated_duration_seconds=45,
        sections=sections,
    )
    validator = NarrativePlanValidator()
    result = validator.validate(plan)
    assert result.status == NarrativeValidationStatus.BLOCKED
    assert any(f.rule_code == NarrativeRuleCode.DUPLICATE_PROMISE_ID for f in result.findings)


# ── Test Suite C: Source Grounding Contract ──


def test_grounding_references_lineage():
    brief_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    source_id = uuid.uuid4()

    g_ref = GroundingReference(
        research_brief_id=brief_id,
        claim_id=claim_id,
        evidence_id=evidence_id,
        source_id=source_id,
        grounding_type=GroundingType.FACTUAL,
        description="Core benchmark verification",
    )
    sec = NarrativeSection(
        section_order=1,
        role=NarrativeSectionRole.HOOK,
        objective="Introduce proven fact",
        target_duration_seconds=10,
        grounding_references=[g_ref],
    )
    assert sec.get_grounding_claim_ids() == [claim_id]


def test_missing_required_grounding_validation():
    sections = make_valid_medium_sections()
    # Clear grounding references on DEVELOPMENT section
    dev_sec = next(s for s in sections if s.role == NarrativeSectionRole.DEVELOPMENT)
    dev_sec.grounding_references = []

    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=300,
        estimated_duration_seconds=300,
        sections=sections,
    )
    validator = NarrativePlanValidator(strict_grounding=True)
    result = validator.validate(plan)
    assert result.status == NarrativeValidationStatus.BLOCKED
    assert any(f.rule_code == NarrativeRuleCode.MISSING_GROUNDING for f in result.findings)


# ── Test Suite D: Versioning and Immutability ──


def test_narrative_plan_versioning_lifecycle():
    repo = InMemoryNarrativePlanRepository()
    service = NarrativePlanService(repository=repo)
    req_id = uuid.uuid4()
    dna_id = uuid.uuid4()

    # Create v1
    v1_sections = make_valid_short_sections()
    v1_plan, v1_val = service.create_plan(
        content_generation_request_id=req_id,
        channel_dna_revision_id=dna_id,
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        sections=v1_sections,
    )
    assert v1_plan.version == 1
    assert v1_plan.is_current is True
    assert v1_val.is_valid is True
    assert v1_plan.status == NarrativePlanStatus.VALIDATED

    # Create v2
    v2_sections = make_valid_short_sections()
    v2_sections[1].objective = "Updated development objective with new nuance"
    v2_plan, v2_val = service.create_revision(
        plan_id=v1_plan.id,
        new_sections=v2_sections,
        notes="Tightened proof mechanism",
    )
    assert v2_plan.version == 2
    assert v2_plan.is_current is True
    assert v2_plan.supersedes_plan_id == v1_plan.id
    assert v2_val.is_valid is True

    # Check v1 status updated to superseded
    saved_v1 = service.get_plan(v1_plan.id)
    assert saved_v1.is_current is False
    assert saved_v1.status == NarrativePlanStatus.SUPERSEDED

    # Current plan query returns v2
    current = service.get_current_plan(req_id)
    assert current.id == v2_plan.id
    assert current.version == 2


# ── Test Suite E: Backward Compatibility ──


def test_legacy_script_version_backward_compatibility():
    # Historical ScriptVersion without narrative_plan fields
    summary = ScriptVersionSummaryResponse(
        id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        version=1,
        is_current=True,
        title="Historical Script",
        estimated_word_count=500,
        estimated_duration_seconds=300,
        qa_status=ScriptQAStatus.PASSED,
        created_at=datetime.now(UTC),
    )
    assert summary.narrative_plan_id is None
    assert summary.narrative_plan_version is None

    # Full ScriptVersion with narrative_plan fields attached
    plan_id = uuid.uuid4()
    p21_summary = ScriptVersionSummaryResponse(
        id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        narrative_plan_id=plan_id,
        narrative_plan_version=2,
        version=2,
        is_current=True,
        title="P21 Script with Plan",
        estimated_word_count=500,
        estimated_duration_seconds=300,
        qa_status=ScriptQAStatus.PASSED,
        created_at=datetime.now(UTC),
    )
    assert p21_summary.narrative_plan_id == plan_id
    assert p21_summary.narrative_plan_version == 2


# ── Test Suite F: Integration Seam & Storyboard Lineage ──


def test_script_generation_seam_and_outline_adapter():
    sections = make_valid_medium_sections()
    plan = NarrativePlan(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=300,
        estimated_duration_seconds=300,
        sections=sections,
    )

    outline_dict = NarrativePlanScriptAdapter.map_plan_to_script_outline(plan)
    assert outline_dict["narrative_plan_id"] == str(plan.id)
    assert outline_dict["narrative_plan_version"] == 1
    assert len(outline_dict["sections"]) == 6
    assert outline_dict["sections"][0]["narrative_role"] == "HOOK"
    assert "OPEN_LOOP(med_main_loop)" in outline_dict["sections"][0]["retention_goal"]
    assert "PAYOFF(med_main_loop)" in outline_dict["sections"][3]["retention_goal"]


def test_storyboard_lineage_preservation():
    plan_id = uuid.uuid4()
    engine = StoryboardEngine()

    script_dict = {
        "title": "Lineage Master Video",
        "narrative_plan_id": str(plan_id),
        "narrative_plan_version": 1,
        "estimated_duration_seconds": 60,
        "sections": [
            {
                "heading": "Intro",
                "statements": [
                    {"statement_text": "Did you know that databases can fail silently without warning?"},
                    {"statement_text": "Here is the exact mechanism that prevents it completely."},
                ],
            },
            {
                "heading": "Resolution",
                "statements": [
                    {"statement_text": "By enforcing strict mathematical invariant bounds, the system never halts."},
                ],
            },
        ],
    }

    storyboard = engine.generate_storyboard(script_dict, pacing="BALANCED")
    assert isinstance(storyboard, StoryboardPlan)
    assert storyboard.narrative_plan_id == str(plan_id)
    assert storyboard.narrative_plan_version == 1
    assert len(storyboard.scenes) >= 2
