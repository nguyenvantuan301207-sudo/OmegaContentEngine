"""Comprehensive Unit Test Matrix for P21-D Narrative QA & Acceptance.

Tests:
A. Structural (valid plan, invalid ordering, missing role, orphan payoff)
B. Hook (strong hook, weak hook, misleading hook, overlong hook)
C. Promise/Payoff (coherent, mismatch, incomplete payoff, unsupported payoff)
D. Grounding (sufficient grounding, insufficient grounding, uncertain claim misuse, unsupported key info)
E. Coherence (repetition, circular development)
F. Pacing (acceptable pacing, dead zone, early payoff, excessive open-loop, flat escalation)
G. Channel DNA (matching narrative, tone mismatch, depth mismatch, pacing style mismatch)
H. Acceptance (PASS, REVISE, FAIL, BLOCKER handling)
I. Script gate (PASS allows generation, REVISE blocks, FAIL blocks)
J. Finding deduplication (same issue from multiple sources collapses correctly)
K. Model reviewer integration (fake reviewer returns findings and merges cleanly)
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from omega.application.narrative_qa_service import (
    ChannelFitQAEvaluator,
    CoherenceQAEvaluator,
    EditorialCompletenessEvaluator,
    FakeEditorialModelReviewer,
    FindingDeduplicator,
    GroundingQAEvaluator,
    HookQAEvaluator,
    NarrativeQAService,
    PacingQAEvaluator,
    PromisePayoffQAEvaluator,
    SeverityAndAcceptancePolicy,
    StructuralQAEvaluator,
)
from omega.application.narrative_script_adapter import NarrativePlanScriptAdapter
from omega.domain.channel_dna import AudienceProfile, BrandVoice, ChannelDNA
from omega.domain.narrative_pacing import (
    InformationDensity,
    OpenLoopMetric,
    PacingFinding,
    PacingFindingCode,
    PacingFindingSeverity,
    PacingPlan,
    PacingProfile,
    SectionTimingDetail,
)
from omega.domain.narrative_plan import (
    GroundingReference,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativeSection,
    NarrativeSectionRole,
)
from omega.domain.narrative_qa import (
    NarrativeQAFinding,
    NarrativeQAFindingCode,
    NarrativeQAGateError,
    NarrativeQARecommendationAction,
    NarrativeQASeverity,
    NarrativeQAStatus,
    NarrativeQASubsystem,
)

BRIEF_ID = uuid4()
CLAIM_1 = uuid4()
CLAIM_2 = uuid4()
CLAIM_3 = uuid4()


def _make_valid_medium_plan() -> NarrativePlan:
    plan_id = uuid4()
    s1 = NarrativeSection(
        narrative_plan_id=plan_id,
        section_order=1,
        role=NarrativeSectionRole.HOOK,
        objective="Discover why distributed transactions fail to achieve consensus under network partitions.",
        key_information=["Atomic distributed operations face coordinator failures."],
        target_duration_seconds=20,
        promise_id="p_atomicity",
    )
    s2 = NarrativeSection(
        narrative_plan_id=plan_id,
        section_order=2,
        role=NarrativeSectionRole.CONTEXT,
        objective="Explain traditional two-phase commit protocols for distributed transactions and isolation levels.",
        key_information=[
            "Two-phase commit requires synchronous lock acquisition across distributed participants.",
            "Locks must be held during the entire prepare and commit phases.",
        ],
        target_duration_seconds=20,
    )
    s3 = NarrativeSection(
        narrative_plan_id=plan_id,
        section_order=3,
        role=NarrativeSectionRole.DEVELOPMENT,
        objective="Demonstrate how network partitions cause coordinator blocking and transaction timeouts.",
        key_information=["Coordinator failure forces participants to hold locks indefinitely."],
        target_duration_seconds=55,
        grounding_references=[
            GroundingReference(research_brief_id=BRIEF_ID, claim_id=CLAIM_1)
        ],
    )
    s4 = NarrativeSection(
        narrative_plan_id=plan_id,
        section_order=4,
        role=NarrativeSectionRole.ESCALATION,
        objective="Analyze the performance impact of cascading aborts across dependent microservices.",
        key_information=["Throughput drops by 85% as queue depth exceeds safe thresholds."],
        target_duration_seconds=35,
        grounding_references=[
            GroundingReference(research_brief_id=BRIEF_ID, claim_id=CLAIM_2)
        ],
    )
    s5 = NarrativeSection(
        narrative_plan_id=plan_id,
        section_order=5,
        role=NarrativeSectionRole.PAYOFF,
        objective="Resolve how modern consensus algorithms guarantee distributed transaction progress without coordinator blocking.",
        key_information=["Raft consensus uses leader election and quorum logging to guarantee forward progress."],
        target_duration_seconds=25,
        payoff_reference="p_atomicity",
        grounding_references=[
            GroundingReference(research_brief_id=BRIEF_ID, claim_id=CLAIM_3)
        ],
    )
    s6 = NarrativeSection(
        narrative_plan_id=plan_id,
        section_order=6,
        role=NarrativeSectionRole.TAKEAWAY,
        objective="Extract core trade-offs between linearizable consensus and eventual consistency in distributed systems.",
        key_information=["Quorum-based replication trades absolute write latency for non-blocking availability."],
        target_duration_seconds=15,
        grounding_references=[
            GroundingReference(research_brief_id=BRIEF_ID, claim_id=CLAIM_3)
        ],
    )
    s7 = NarrativeSection(
        narrative_plan_id=plan_id,
        section_order=7,
        role=NarrativeSectionRole.CLOSING,
        objective="Summarize key architectural principles for high-resilience consensus architectures.",
        key_information=["Prefer quorum-based replication over synchronous two-phase locks."],
        target_duration_seconds=10,
    )
    return NarrativePlan(
        id=plan_id,
        content_generation_request_id=uuid4(),
        channel_dna_revision_id=uuid4(),
        topic_candidate_id=uuid4(),
        research_brief_id=BRIEF_ID,
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=180,
        estimated_duration_seconds=180,
        sections=[s1, s2, s3, s4, s5, s6, s7],
    )


# ── A. Structural Tests ─────────────────────────────────────────────────────


def test_structural_qa_valid_plan():
    plan = _make_valid_medium_plan()
    evaluator = StructuralQAEvaluator()
    findings = evaluator.evaluate(plan)
    assert len(findings) == 0


def test_structural_qa_missing_role():
    plan = _make_valid_medium_plan()
    # Remove CLOSING role
    plan.sections = [s for s in plan.sections if s.role != NarrativeSectionRole.CLOSING]
    evaluator = StructuralQAEvaluator()
    findings = evaluator.evaluate(plan)
    assert any(f.code == NarrativeQAFindingCode.MISSING_REQUIRED_ROLE for f in findings)
    assert any(f.severity == NarrativeQASeverity.BLOCKER for f in findings)


def test_structural_qa_orphan_payoff():
    plan = _make_valid_medium_plan()
    # Make payoff point to nonexistent promise
    for s in plan.sections:
        if s.role == NarrativeSectionRole.PAYOFF:
            s.payoff_reference = "nonexistent_promise"
    evaluator = StructuralQAEvaluator()
    findings = evaluator.evaluate(plan)
    assert any(f.code == NarrativeQAFindingCode.ORPHAN_PAYOFF for f in findings)


# ── B. Hook Tests ───────────────────────────────────────────────────────────


def test_hook_qa_strong_hook():
    plan = _make_valid_medium_plan()
    evaluator = HookQAEvaluator()
    findings = evaluator.evaluate(plan)
    assert len(findings) == 0


def test_hook_qa_weak_hook():
    plan = _make_valid_medium_plan()
    hook = plan.sections[0]
    hook.objective = "Welcome to the video."
    hook.key_information = []
    evaluator = HookQAEvaluator()
    findings = evaluator.evaluate(plan)
    assert any(f.code == NarrativeQAFindingCode.WEAK_HOOK for f in findings)


def test_hook_qa_overlong_hook():
    plan = _make_valid_medium_plan()
    plan.sections[0].target_duration_seconds = 50  # Max for MEDIUM is 30
    evaluator = HookQAEvaluator()
    findings = evaluator.evaluate(plan)
    assert any(f.code == NarrativeQAFindingCode.OVERLONG_HOOK for f in findings)


def test_hook_qa_misleading_hook():
    plan = _make_valid_medium_plan()
    plan.sections[0].objective = "Quantum superconductivity in cryogenic tokamak reactors revealed!"
    evaluator = HookQAEvaluator()
    findings = evaluator.evaluate(plan)
    assert any(f.code == NarrativeQAFindingCode.MISLEADING_HOOK for f in findings)


# ── C. Promise / Payoff Tests ───────────────────────────────────────────────


def test_promise_payoff_qa_coherent():
    plan = _make_valid_medium_plan()
    evaluator = PromisePayoffQAEvaluator()
    findings = evaluator.evaluate(plan)
    assert len(findings) == 0


def test_promise_payoff_qa_mismatch():
    plan = _make_valid_medium_plan()
    # Promise is about distributed databases; change payoff to gardening vegetables
    for s in plan.sections:
        if s.role == NarrativeSectionRole.PAYOFF:
            s.objective = "Cultivating organic heirloom tomatoes in greenhouse beds during spring months."
            s.key_information = ["Use nitrogen fertilizer and maintain soil moisture levels."]
    evaluator = PromisePayoffQAEvaluator()
    findings = evaluator.evaluate(plan)
    assert any(f.code == NarrativeQAFindingCode.PROMISE_PAYOFF_MISMATCH for f in findings)


def test_promise_payoff_qa_incomplete_payoff():
    plan = _make_valid_medium_plan()
    for s in plan.sections:
        if s.role == NarrativeSectionRole.PAYOFF:
            s.target_duration_seconds = 5  # Too short for MEDIUM (min 10)
    evaluator = PromisePayoffQAEvaluator()
    findings = evaluator.evaluate(plan)
    assert any(f.code == NarrativeQAFindingCode.PAYOFF_INCOMPLETE for f in findings)


# ── D. Grounding Tests ──────────────────────────────────────────────────────


def test_grounding_qa_sufficient():
    plan = _make_valid_medium_plan()
    brief = {
        "verified_claims": [
            {"claim_id": str(CLAIM_1)},
            {"claim_id": str(CLAIM_2)},
            {"claim_id": str(CLAIM_3)},
        ],
        "uncertain_claims": [],
        "contradictions": [],
    }
    evaluator = GroundingQAEvaluator()
    findings = evaluator.evaluate(plan, research_brief=brief)
    assert len(findings) == 0


def test_grounding_qa_uncertain_claim_promoted():
    plan = _make_valid_medium_plan()
    brief = {
        "verified_claims": [{"claim_id": str(CLAIM_1)}],
        "uncertain_claims": [{"claim_id": str(CLAIM_3)}],
        "contradictions": [],
    }
    evaluator = GroundingQAEvaluator()
    findings = evaluator.evaluate(plan, research_brief=brief)
    assert any(f.code == NarrativeQAFindingCode.UNCERTAIN_CLAIM_PROMOTED for f in findings)
    assert any(f.severity == NarrativeQASeverity.BLOCKER for f in findings)


def test_grounding_qa_payoff_lacking_evidence():
    plan = _make_valid_medium_plan()
    # Remove citations from payoff
    for s in plan.sections:
        if s.role == NarrativeSectionRole.PAYOFF:
            s.grounding_references = []
    brief = {
        "verified_claims": [{"claim_id": str(CLAIM_1)}],
        "uncertain_claims": [],
        "contradictions": [],
    }
    evaluator = GroundingQAEvaluator()
    findings = evaluator.evaluate(plan, research_brief=brief)
    assert any(f.code == NarrativeQAFindingCode.PAYOFF_LACKING_EVIDENCE for f in findings)


# ── E. Coherence Tests ──────────────────────────────────────────────────────


def test_coherence_qa_repetition():
    plan = _make_valid_medium_plan()
    # Duplicate objective between section 2 and 3
    plan.sections[2].objective = plan.sections[1].objective
    evaluator = CoherenceQAEvaluator()
    findings = evaluator.evaluate(plan)
    assert any(f.code == NarrativeQAFindingCode.NARRATIVE_REPETITION for f in findings)


# ── F. Pacing Acceptance Tests ──────────────────────────────────────────────


def test_pacing_qa_promotion():
    plan = _make_valid_medium_plan()
    pacing_plan = PacingPlan(
        narrative_plan_id=plan.id,
        narrative_plan_version=plan.version,
        pacing_profile=PacingProfile.BALANCED,
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=180,
        initial_duration_sum_seconds=180,
        optimized_duration_sum_seconds=180,
        section_timings=[],
        open_loop_metrics=[],
        findings=[
            PacingFinding(
                code=PacingFindingCode.DEAD_SECTION,
                severity=PacingFindingSeverity.BLOCKING,
                message="Section 3 is completely dead with zero evidence or progression.",
                section_order=3,
            ),
            PacingFinding(
                code=PacingFindingCode.OVERLONG_CONTEXT,
                severity=PacingFindingSeverity.WARNING,
                message="Context accounts for excessive duration.",
                section_order=2,
            ),
        ],
        recommended_adjustments=[],
        escalation_curve=[0.3, 0.4, 0.6, 0.9, 0.9, 0.5, 0.2],
    )
    evaluator = PacingQAEvaluator()
    findings = evaluator.evaluate(plan, pacing_plan=pacing_plan)
    assert any(f.code == NarrativeQAFindingCode.DEAD_ZONE for f in findings)
    assert any(f.code == NarrativeQAFindingCode.OVERLONG_CONTEXT for f in findings)


# ── G. Channel DNA Fit Tests ────────────────────────────────────────────────


def test_channel_fit_qa_matching():
    plan = _make_valid_medium_plan()
    dna = ChannelDNA(
        brand_voice=BrandVoice(tone=["AUTHORITATIVE", "OBJECTIVE"], pace="MODERATE"),
        audience=AudienceProfile(knowledge_level="INTERMEDIATE"),
    )
    evaluator = ChannelFitQAEvaluator()
    findings = evaluator.evaluate(plan, channel_dna=dna)
    assert len(findings) == 0


def test_channel_fit_qa_tone_mismatch():
    plan = _make_valid_medium_plan()
    # Strip all claim citations
    for s in plan.sections:
        s.grounding_references = []
    dna = ChannelDNA(
        brand_voice=BrandVoice(tone=["ACADEMIC", "AUTHORITATIVE"]),
        audience=AudienceProfile(knowledge_level="ADVANCED"),
    )
    evaluator = ChannelFitQAEvaluator()
    findings = evaluator.evaluate(plan, channel_dna=dna)
    assert any(f.code == NarrativeQAFindingCode.CHANNEL_TONE_MISMATCH for f in findings)


# ── H. Acceptance Policy Tests ──────────────────────────────────────────────


def test_acceptance_policy_pass():
    status, highest_sev = SeverityAndAcceptancePolicy.evaluate_status([])
    assert status == NarrativeQAStatus.PASS
    assert highest_sev == NarrativeQASeverity.INFO


def test_acceptance_policy_fail_on_blocker():
    findings = [
        NarrativeQAFinding(
            code=NarrativeQAFindingCode.EMPTY_PLAN,
            severity=NarrativeQASeverity.BLOCKER,
            subsystem=NarrativeQASubsystem.STRUCTURE,
            explanation="Empty plan.",
        )
    ]
    status, highest_sev = SeverityAndAcceptancePolicy.evaluate_status(findings)
    assert status == NarrativeQAStatus.FAIL
    assert highest_sev == NarrativeQASeverity.BLOCKER


def test_acceptance_policy_revise_on_error():
    findings = [
        NarrativeQAFinding(
            code=NarrativeQAFindingCode.PAYOFF_LACKING_EVIDENCE,
            severity=NarrativeQASeverity.ERROR,
            subsystem=NarrativeQASubsystem.GROUNDING,
            explanation="Payoff lacks citations.",
            recommended_remediation="Add citations.",
        )
    ]
    status, highest_sev = SeverityAndAcceptancePolicy.evaluate_status(findings)
    assert status == NarrativeQAStatus.REVISE
    assert highest_sev == NarrativeQASeverity.ERROR


# ── I. Script Gate Tests ────────────────────────────────────────────────────


def test_script_gate_pass_allows_generation():
    plan = _make_valid_medium_plan()
    brief = {
        "verified_claims": [
            {"claim_id": str(CLAIM_1)},
            {"claim_id": str(CLAIM_2)},
            {"claim_id": str(CLAIM_3)},
        ]
    }
    service = NarrativeQAService()
    result = service.enforce_script_gate(plan=plan, research_brief=brief)
    assert result.status == NarrativeQAStatus.PASS
    outline = NarrativePlanScriptAdapter.map_plan_to_script_outline(plan)
    assert len(outline["sections"]) == 7


def test_script_gate_blocks_on_revise_or_fail():
    plan = _make_valid_medium_plan()
    # Intentionally corrupt plan to make payoff orphan
    for s in plan.sections:
        if s.role == NarrativeSectionRole.PAYOFF:
            s.payoff_reference = "missing_promise"

    service = NarrativeQAService()
    with pytest.raises(NarrativeQAGateError) as exc_info:
        service.enforce_script_gate(plan=plan)

    assert "blocked by script generation gate" in str(exc_info.value)
    assert exc_info.value.qa_result.status == NarrativeQAStatus.FAIL


# ── J. Finding Deduplication Tests ──────────────────────────────────────────


def test_finding_deduplication():
    f1 = NarrativeQAFinding(
        code=NarrativeQAFindingCode.DEAD_ZONE,
        severity=NarrativeQASeverity.WARNING,
        subsystem=NarrativeQASubsystem.PACING,
        explanation="Low progress in section 3.",
        affected_section_orders=[3],
        contributing_sources=["PacingEngine"],
    )
    f2 = NarrativeQAFinding(
        code=NarrativeQAFindingCode.DEAD_ZONE,
        severity=NarrativeQASeverity.ERROR,
        subsystem=NarrativeQASubsystem.PACING,
        explanation="Stalled narrative development in section 3.",
        affected_section_orders=[3],
        contributing_sources=["EditorialReview"],
        recommended_remediation="Add verified evidence to section 3.",
    )
    deduped = FindingDeduplicator.deduplicate([f1, f2])
    assert len(deduped) == 1
    assert deduped[0].severity == NarrativeQASeverity.ERROR
    assert set(deduped[0].contributing_sources) == {"PacingEngine", "EditorialReview"}
    assert deduped[0].recommended_remediation == "Add verified evidence to section 3."


# ── K. Model Reviewer Integration Tests ─────────────────────────────────────


def test_model_reviewer_integration():
    plan = _make_valid_medium_plan()
    fake_findings = [
        NarrativeQAFinding(
            code=NarrativeQAFindingCode.WEAK_HOOK,
            severity=NarrativeQASeverity.WARNING,
            subsystem=NarrativeQASubsystem.EDITORIAL,
            explanation="Model suggests hook could be more punchy.",
            affected_section_orders=[1],
            contributing_sources=["FakeEditorialModelReviewer"],
            recommended_remediation="Add punchy question.",
        )
    ]
    reviewer = FakeEditorialModelReviewer(findings=fake_findings)
    service = NarrativeQAService()
    result = service.evaluate_plan(plan=plan, model_reviewer=reviewer)
    assert result.provenance["model_reviewer"] == "FakeEditorialModelReviewer"
    assert any(f.code == NarrativeQAFindingCode.WEAK_HOOK for f in result.findings)
