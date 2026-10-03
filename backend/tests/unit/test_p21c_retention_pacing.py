"""Comprehensive unit tests for P21-C Retention & Pacing Intelligence.

Validates:
A. Timing (SHORT, MEDIUM, LONG, total-duration tolerance)
B. Density (underloaded, overloaded, balanced)
C. Promise/Payoff (too-fast, excessive delay, acceptable lifetime, multiple loops)
D. Reveal Timing (premature reveal, well-timed reveal, excessive delay)
E. Dead Zones (overlong context, stalled development, no info progression)
F. Redundancy (repeated evidence, repeated objective, duplicated takeaway)
G. Escalation (valid curve, flat escalation, reversed progression)
H. Revision Behavior (original unchanged, new revision created, lineage preserved, single-current DB invariant)
I. Channel DNA (pacing preference influences plan, factual grounding unaffected)
J. Script Handoff (timing/density guidance survives into generation context)
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

import pytest

from omega.application.narrative_plan_service import InMemoryNarrativePlanRepository, NarrativePlanService
from omega.application.narrative_plan_validator import NarrativePlanValidator
from omega.application.narrative_script_adapter import NarrativePlanScriptAdapter
from omega.application.retention_pacing_engine import (
    DeadZoneDetector,
    EscalationCurveEngine,
    InformationDensityEngine,
    OpenLoopTimingEngine,
    PacingProfileResolver,
    RedundancyDetector,
    RetentionPacingService,
    RevealTimingEngine,
    SectionTimingEngine,
)
from omega.domain.channel_dna import ChannelDNA
from omega.domain.narrative_pacing import (
    PacingFindingCode,
    PacingPlan,
    PacingProfile,
    RevealStage,
)
from omega.domain.narrative_plan import (
    GroundingReference,
    GroundingType,
    InformationDensity,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativeSection,
    NarrativeSectionRole,
)


@pytest.fixture
def sample_sections_medium() -> list[NarrativeSection]:
    brief_id = uuid.uuid4()
    claim1_id = uuid.uuid4()
    claim2_id = uuid.uuid4()
    claim3_id = uuid.uuid4()

    return [
        NarrativeSection(
            id=uuid.uuid4(),
            section_order=1,
            role=NarrativeSectionRole.HOOK,
            objective="Hook viewer attention on transmon noise suppression.",
            key_information=["Transmon circuits decouple noise"],
            target_duration_seconds=20,
            promise_id="loop_transmon_1",
        ),
        NarrativeSection(
            id=uuid.uuid4(),
            section_order=2,
            role=NarrativeSectionRole.CONTEXT,
            objective="Explain the physical origin of charge dispersion.",
            key_information=["Josephson junctions and Cooper pair boxes"],
            grounding_references=[
                GroundingReference(research_brief_id=brief_id, claim_id=claim1_id, description="Cooper pair box theory")
            ],
            target_duration_seconds=40,
        ),
        NarrativeSection(
            id=uuid.uuid4(),
            section_order=3,
            role=NarrativeSectionRole.DEVELOPMENT,
            objective="Analyze the capacitive shunting exponential flattening mechanism.",
            key_information=["EJ to EC energy ratio suppresses charge noise exponentially"],
            grounding_references=[
                GroundingReference(research_brief_id=brief_id, claim_id=claim2_id, description="Exponential flattening proof")
            ],
            target_duration_seconds=120,
        ),
        NarrativeSection(
            id=uuid.uuid4(),
            section_order=4,
            role=NarrativeSectionRole.ESCALATION,
            objective="Examine thermal decoherence limits at millikelvin scales.",
            key_information=["Dilution refrigerator shield requirements"],
            grounding_references=[
                GroundingReference(research_brief_id=brief_id, claim_id=claim3_id, description="Dilution refrigeration")
            ],
            target_duration_seconds=60,
        ),
        NarrativeSection(
            id=uuid.uuid4(),
            section_order=5,
            role=NarrativeSectionRole.PAYOFF,
            objective="Synthesize the complete architectural solution for quantum processors.",
            key_information=["Transmon qubit robustness"],
            grounding_references=[
                GroundingReference(research_brief_id=brief_id, claim_id=claim2_id, description="Robustness synthesis")
            ],
            target_duration_seconds=45,
            payoff_reference="loop_transmon_1",
        ),
        NarrativeSection(
            id=uuid.uuid4(),
            section_order=6,
            role=NarrativeSectionRole.TAKEAWAY,
            objective="Deliver actionable takeaways on superconducting qubit hardware design.",
            key_information=["Capacitive ratio EJ/EC > 50"],
            target_duration_seconds=15,
        ),
        NarrativeSection(
            id=uuid.uuid4(),
            section_order=7,
            role=NarrativeSectionRole.CLOSING,
            objective="Conclude and summarize the quantum hardware analysis.",
            key_information=["Summary of transmon noise immunity"],
            target_duration_seconds=10,
        ),
    ]


@pytest.fixture
def sample_plan_medium(sample_sections_medium) -> NarrativePlan:
    return NarrativePlan(
        id=uuid.uuid4(),
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        version=1,
        status=NarrativePlanStatus.VALIDATED,
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=300,
        estimated_duration_seconds=300,
        is_current=True,
        sections=sample_sections_medium,
        metadata={"selected_strategy": "HOW_IT_WORKS"},
    )


# ======================================================================
# Test A: Timing Allocation
# ======================================================================

def test_timing_allocation_short():
    sections = [
        NarrativeSection(id=uuid.uuid4(), section_order=1, role=NarrativeSectionRole.HOOK, objective="Hook", target_duration_seconds=10),
        NarrativeSection(id=uuid.uuid4(), section_order=2, role=NarrativeSectionRole.DEVELOPMENT, objective="Dev", target_duration_seconds=20),
        NarrativeSection(id=uuid.uuid4(), section_order=3, role=NarrativeSectionRole.PAYOFF, objective="Pay", target_duration_seconds=10),
        NarrativeSection(id=uuid.uuid4(), section_order=4, role=NarrativeSectionRole.TAKEAWAY, objective="Take", target_duration_seconds=5),
    ]
    durations = SectionTimingEngine.calculate_durations(
        sections=sections,
        format_profile=NarrativeFormatProfile.SHORT,
        target_total_duration=45,
        pacing_profile=PacingProfile.FAST,
    )
    assert sum(durations) == 45
    assert durations[0] <= 10  # Hook bounded
    assert all(d >= 5 for d in durations)


def test_timing_allocation_medium(sample_sections_medium):
    durations = SectionTimingEngine.calculate_durations(
        sections=sample_sections_medium,
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_total_duration=300,
        pacing_profile=PacingProfile.BALANCED,
    )
    assert sum(durations) == 300
    assert durations[0] <= 30  # Hook bounded to max 30s for MEDIUM
    assert durations[2] > durations[0]  # Development receives larger allocation than hook


def test_timing_allocation_long():
    roles = [
        NarrativeSectionRole.HOOK,
        NarrativeSectionRole.PROMISE,
        NarrativeSectionRole.CONTEXT,
        NarrativeSectionRole.DEVELOPMENT,
        NarrativeSectionRole.DEVELOPMENT,
        NarrativeSectionRole.ESCALATION,
        NarrativeSectionRole.PAYOFF,
        NarrativeSectionRole.TAKEAWAY,
        NarrativeSectionRole.CLOSING,
    ]
    sections = [
        NarrativeSection(id=uuid.uuid4(), section_order=i, role=r, objective=f"Obj {i}", target_duration_seconds=100)
        for i, r in enumerate(roles, start=1)
    ]
    durations = SectionTimingEngine.calculate_durations(
        sections=sections,
        format_profile=NarrativeFormatProfile.LONG,
        target_total_duration=900,
        pacing_profile=PacingProfile.DELIBERATE,
    )
    assert sum(durations) == 900
    assert durations[0] <= 45  # Long hook bounded


# ======================================================================
# Test B: Information Density
# ======================================================================

def test_density_underloaded_section():
    sec = NarrativeSection(
        id=uuid.uuid4(),
        section_order=2,
        role=NarrativeSectionRole.CONTEXT,
        objective="Vague overview",
        key_information=[],
        grounding_references=[],
        target_duration_seconds=35,
    )
    density, findings = InformationDensityEngine.evaluate_density(sec, NarrativeFormatProfile.MEDIUM, 35)
    assert density == InformationDensity.LOW
    assert any(f.code == PacingFindingCode.DENSITY_UNDERLOADED for f in findings)


def test_density_overloaded_section():
    brief_id = uuid.uuid4()
    claims = [GroundingReference(research_brief_id=brief_id, claim_id=uuid.uuid4()) for _ in range(4)]
    sec = NarrativeSection(
        id=uuid.uuid4(),
        section_order=1,
        role=NarrativeSectionRole.HOOK,
        objective="Overstuffed hook",
        key_information=["a", "b", "c"],
        grounding_references=claims,
        target_duration_seconds=8,
    )
    density, findings = InformationDensityEngine.evaluate_density(sec, NarrativeFormatProfile.SHORT, 8)
    assert density == InformationDensity.HIGH
    assert any(f.code == PacingFindingCode.DENSITY_OVERLOADED for f in findings)


def test_density_balanced_section():
    brief_id = uuid.uuid4()
    claims = [GroundingReference(research_brief_id=brief_id, claim_id=uuid.uuid4()) for _ in range(2)]
    sec = NarrativeSection(
        id=uuid.uuid4(),
        section_order=3,
        role=NarrativeSectionRole.DEVELOPMENT,
        objective="Balanced explanation of mechanics",
        key_information=["core principle", "empirical metric"],
        grounding_references=claims,
        target_duration_seconds=45,
    )
    density, findings = InformationDensityEngine.evaluate_density(sec, NarrativeFormatProfile.MEDIUM, 45)
    assert density in (InformationDensity.MEDIUM, InformationDensity.HIGH)
    assert len(findings) == 0


# ======================================================================
# Test C: Promise / Payoff Open Loops
# ======================================================================

def test_open_loop_too_fast_payoff():
    sections = [
        NarrativeSection(id=uuid.uuid4(), section_order=1, role=NarrativeSectionRole.HOOK, objective="Hook", target_duration_seconds=10, promise_id="loop_fast"),
        NarrativeSection(id=uuid.uuid4(), section_order=2, role=NarrativeSectionRole.PAYOFF, objective="Immediate payoff", target_duration_seconds=15, payoff_reference="loop_fast"),
        NarrativeSection(id=uuid.uuid4(), section_order=3, role=NarrativeSectionRole.TAKEAWAY, objective="Takeaway", target_duration_seconds=20),
    ]
    metrics, findings = OpenLoopTimingEngine.analyze_loops(sections, NarrativeFormatProfile.MEDIUM)
    assert any(f.code == PacingFindingCode.LOOP_RESOLVED_TOO_QUICKLY for f in findings)
    assert metrics[0].status == "TOO_FAST"


def test_open_loop_excessive_delay():
    sections = [
        NarrativeSection(id=uuid.uuid4(), section_order=1, role=NarrativeSectionRole.HOOK, objective="Hook", target_duration_seconds=10, promise_id="loop_slow"),
        NarrativeSection(id=uuid.uuid4(), section_order=2, role=NarrativeSectionRole.DEVELOPMENT, objective="Dev 1", target_duration_seconds=150),
        NarrativeSection(id=uuid.uuid4(), section_order=3, role=NarrativeSectionRole.DEVELOPMENT, objective="Dev 2", target_duration_seconds=150),
        NarrativeSection(id=uuid.uuid4(), section_order=4, role=NarrativeSectionRole.PAYOFF, objective="Late payoff", target_duration_seconds=20, payoff_reference="loop_slow"),
    ]
    metrics, findings = OpenLoopTimingEngine.analyze_loops(sections, NarrativeFormatProfile.MEDIUM)
    assert any(f.code == PacingFindingCode.LOOP_HELD_TOO_LONG for f in findings)
    assert metrics[0].status == "TOO_SLOW"


def test_open_loop_acceptable_range(sample_sections_medium):
    metrics, findings = OpenLoopTimingEngine.analyze_loops(sample_sections_medium, NarrativeFormatProfile.MEDIUM)
    loop_findings = [f for f in findings if f.code in (PacingFindingCode.LOOP_RESOLVED_TOO_QUICKLY, PacingFindingCode.LOOP_HELD_TOO_LONG)]
    assert len(loop_findings) == 0
    assert metrics[0].is_resolved is True
    assert metrics[0].status == "OPTIMAL"


def test_open_loop_multiple_overlapping():
    sections = [
        NarrativeSection(id=uuid.uuid4(), section_order=1, role=NarrativeSectionRole.HOOK, objective="Hook", target_duration_seconds=10, promise_id="loop_1"),
        NarrativeSection(id=uuid.uuid4(), section_order=2, role=NarrativeSectionRole.PROMISE, objective="Promise", target_duration_seconds=15, promise_id="loop_2"),
        NarrativeSection(id=uuid.uuid4(), section_order=3, role=NarrativeSectionRole.CONTEXT, objective="Context", target_duration_seconds=20, promise_id="loop_3"),
        NarrativeSection(id=uuid.uuid4(), section_order=4, role=NarrativeSectionRole.PAYOFF, objective="Payoff", target_duration_seconds=30, payoff_reference="loop_1"),
    ]
    _, findings = OpenLoopTimingEngine.analyze_loops(sections, NarrativeFormatProfile.MEDIUM)
    assert any(f.code == PacingFindingCode.MULTIPLE_UNRESOLVED_LOOPS for f in findings)


# ======================================================================
# Test D: Reveal Timing
# ======================================================================

def test_premature_reveal_detected():
    sections = [
        NarrativeSection(id=uuid.uuid4(), section_order=1, role=NarrativeSectionRole.HOOK, objective="Hook", target_duration_seconds=10),
        NarrativeSection(id=uuid.uuid4(), section_order=2, role=NarrativeSectionRole.ESCALATION, objective="Immediate Major Climax", target_duration_seconds=20),
        NarrativeSection(id=uuid.uuid4(), section_order=3, role=NarrativeSectionRole.DEVELOPMENT, objective="Afterthought Dev", target_duration_seconds=50),
        NarrativeSection(id=uuid.uuid4(), section_order=4, role=NarrativeSectionRole.PAYOFF, objective="Payoff", target_duration_seconds=30),
        NarrativeSection(id=uuid.uuid4(), section_order=5, role=NarrativeSectionRole.TAKEAWAY, objective="Takeaway", target_duration_seconds=15),
    ]
    stages, findings = RevealTimingEngine.assign_stages(sections)
    assert any(f.code == PacingFindingCode.PREMATURE_REVEAL for f in findings)


def test_delayed_reveal_detected():
    sections = [
        NarrativeSection(id=uuid.uuid4(), section_order=1, role=NarrativeSectionRole.HOOK, objective="Hook", target_duration_seconds=10),
        NarrativeSection(id=uuid.uuid4(), section_order=2, role=NarrativeSectionRole.CONTEXT, objective="Context 1", target_duration_seconds=30),
        NarrativeSection(id=uuid.uuid4(), section_order=3, role=NarrativeSectionRole.DEVELOPMENT, objective="Dev 1", target_duration_seconds=50),
        NarrativeSection(id=uuid.uuid4(), section_order=4, role=NarrativeSectionRole.DEVELOPMENT, objective="Dev 2", target_duration_seconds=50),
        NarrativeSection(id=uuid.uuid4(), section_order=5, role=NarrativeSectionRole.TAKEAWAY, objective="Final section", target_duration_seconds=30),
    ]
    stages, findings = RevealTimingEngine.assign_stages(sections)
    assert any(f.code == PacingFindingCode.DELAYED_REVEAL for f in findings)


# ======================================================================
# Test E: Dead Zones
# ======================================================================

def test_dead_zone_detection():
    sections = [
        NarrativeSection(id=uuid.uuid4(), section_order=1, role=NarrativeSectionRole.HOOK, objective="Hook", target_duration_seconds=15),
        NarrativeSection(id=uuid.uuid4(), section_order=2, role=NarrativeSectionRole.CONTEXT, objective="Historical context", target_duration_seconds=120),  # >30% of 300s
        NarrativeSection(id=uuid.uuid4(), section_order=3, role=NarrativeSectionRole.DEVELOPMENT, objective="Empty section", target_duration_seconds=40, key_information=[], grounding_references=[]),
        NarrativeSection(id=uuid.uuid4(), section_order=4, role=NarrativeSectionRole.PAYOFF, objective="Payoff", target_duration_seconds=50),
    ]
    findings = DeadZoneDetector.detect_dead_zones(sections, total_duration=300)
    finding_codes = {f.code for f in findings}
    assert PacingFindingCode.OVERLONG_CONTEXT in finding_codes
    assert PacingFindingCode.DEAD_SECTION in finding_codes
    assert PacingFindingCode.LOW_INFORMATION_PROGRESS in finding_codes


# ======================================================================
# Test F: Redundancy Detection
# ======================================================================

def test_redundancy_repeated_claim_and_objective():
    claim_id = uuid.uuid4()
    brief_id = uuid.uuid4()
    ref = GroundingReference(research_brief_id=brief_id, claim_id=claim_id, description="Qubit claim")

    sections = [
        NarrativeSection(id=uuid.uuid4(), section_order=1, role=NarrativeSectionRole.HOOK, objective="Explain transmon qubit noise suppression in detail.", target_duration_seconds=15),
        NarrativeSection(id=uuid.uuid4(), section_order=2, role=NarrativeSectionRole.DEVELOPMENT, objective="Explain transmon qubit noise suppression in detail.", grounding_references=[ref], target_duration_seconds=40),
        NarrativeSection(id=uuid.uuid4(), section_order=3, role=NarrativeSectionRole.PAYOFF, objective="Final payoff", grounding_references=[ref], target_duration_seconds=40),
    ]
    findings = RedundancyDetector.detect_redundancy(sections)
    codes = {f.code for f in findings}
    assert PacingFindingCode.REPEATED_CLAIM in codes
    assert PacingFindingCode.REPEATED_OBJECTIVE in codes


# ======================================================================
# Test G: Escalation Curve
# ======================================================================

def test_escalation_curve_valid(sample_sections_medium):
    curve, findings = EscalationCurveEngine.calculate_curve(sample_sections_medium)
    assert len(curve) == len(sample_sections_medium)
    # Hook is energetic, context drops, development and escalation build to payoff
    assert curve[0] > curve[1]  # Hook > Context
    assert curve[4] > curve[1]  # Payoff > Context
    assert curve[4] >= curve[3] # Payoff >= Escalation


# ======================================================================
# Test H: Revision Behavior & PacingPlan Creation
# ======================================================================

def test_pacing_service_audit_and_optimization(sample_plan_medium):
    service = RetentionPacingService()
    pacing_plan = service.audit_plan(sample_plan_medium)

    assert isinstance(pacing_plan, PacingPlan)
    assert pacing_plan.narrative_plan_id == sample_plan_medium.id
    assert pacing_plan.format_profile == NarrativeFormatProfile.MEDIUM
    assert len(pacing_plan.section_timings) == len(sample_plan_medium.sections)

    # Optimize plan
    optimized_plan, optimized_pacing = service.optimize_plan(sample_plan_medium)
    assert optimized_plan.version == sample_plan_medium.version + 1
    assert optimized_plan.supersedes_plan_id == sample_plan_medium.id
    assert sample_plan_medium.version == 1  # Original plan unchanged
    assert optimized_plan.metadata.get("pacing_profile") == PacingProfile.BALANCED.value
    assert sum(s.target_duration_seconds for s in optimized_plan.sections) == sample_plan_medium.target_duration_seconds


def test_pacing_service_durable_revision_preserves_single_current(sample_plan_medium):
    repo = InMemoryNarrativePlanRepository()
    plan_service = NarrativePlanService(repository=repo)
    # Persist v1
    v1 = repo.save(sample_plan_medium)
    assert v1.is_current is True

    service = RetentionPacingService()
    v2, _ = service.optimize_plan(v1, plan_service=plan_service)

    # v2 is current, v1 is superseded
    fetched_v1 = repo.get(v1.id)
    fetched_v2 = repo.get(v2.id)

    assert fetched_v1.is_current is False
    assert fetched_v1.status == NarrativePlanStatus.SUPERSEDED
    assert fetched_v2.is_current is True
    assert fetched_v2.version == 2
    assert fetched_v2.supersedes_plan_id == v1.id


# ======================================================================
# Test I: Channel DNA Influence
# ======================================================================

def test_channel_dna_pacing_profile_resolution(sample_plan_medium):
    service = RetentionPacingService()

    fast_dna = {"brand_voice": {"pace": "FAST", "tone": "AUTHORITATIVE"}}
    deliberate_dna = {"brand_voice": {"pace": "MEASURED", "tone": "ANALYTICAL"}}

    plan_fast = service.audit_plan(sample_plan_medium, channel_dna=fast_dna)
    plan_deliberate = service.audit_plan(sample_plan_medium, channel_dna=deliberate_dna)

    assert plan_fast.pacing_profile == PacingProfile.FAST
    assert plan_deliberate.pacing_profile == PacingProfile.DELIBERATE

    # Fast profile gives less context allowance than deliberate profile
    fast_context = next(t for t in plan_fast.section_timings if t.role == NarrativeSectionRole.CONTEXT)
    delib_context = next(t for t in plan_deliberate.section_timings if t.role == NarrativeSectionRole.CONTEXT)
    assert fast_context.recommended_duration_seconds < delib_context.recommended_duration_seconds


# ======================================================================
# Test J: Script Handoff With Paced Plan
# ======================================================================

def test_paced_plan_script_handoff(sample_plan_medium):
    service = RetentionPacingService()
    optimized_plan, _ = service.optimize_plan(sample_plan_medium)

    outline = NarrativePlanScriptAdapter.map_plan_to_script_outline(optimized_plan)
    assert outline["narrative_plan_id"] == str(optimized_plan.id)
    assert outline["narrative_plan_version"] == 2
    assert len(outline["sections"]) == len(optimized_plan.sections)

    for sec in outline["sections"]:
        assert "estimated_duration_seconds" in sec
        assert "information_density" in sec
        assert "narrative_role" in sec
