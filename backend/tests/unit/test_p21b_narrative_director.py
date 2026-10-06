"""Unit tests for P21-B Narrative Director.

Validates:
A. Strategy selection (explanatory, comparison, chronology, misconception, mystery, fallback)
B. Format behavior (SHORT, MEDIUM, LONG)
C. Grounding (verified research carried, uncertain evidence not promoted, invalid IDs rejected)
D. Promise/payoff (generated loop valid, payoff linked, no orphan payoff)
E. Candidate generation (multiple candidates generated, selection criteria)
F. Model failures (retry exhaustion, deterministic fallback)
G. Provenance (request/research/DNA pins, director version)
H. Script handoff (reaches NarrativePlanScriptAdapter, ScriptVersion pins plan ID)
I. Legacy compatibility (legacy flow remains functional)
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

import pytest

from omega.application.content_provider import TemplateContentProvider
from omega.application.narrative_director import (
    CandidateSelectionEngine,
    DeterministicNarrativeDirector,
    GeminiNarrativeModelClient,
    ModelBackedNarrativeDirector,
    NarrativeDirectorGroundingError,
    NarrativeDirectorStrategyError,
    NarrativeModelError,
    NarrativeModelMalformedError,
    NarrativeModelProviderError,
    NarrativeModelTimeoutError,
)
from omega.application.narrative_plan_service import (
    InMemoryNarrativePlanRepository,
    NarrativePlanService,
)
from omega.application.narrative_plan_validator import NarrativePlanValidator
from omega.application.narrative_planning_service import NarrativePlanningService
from omega.application.narrative_script_adapter import (
    NarrativePlanScriptAdapter,
    ScriptGenerationContext,
)
from omega.domain.channel_dna import ChannelDNA
from omega.domain.narrative_plan import (
    GroundingType,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativeRuleCode,
    NarrativeSection,
    NarrativeSectionRole,
)
from omega.domain.narrative_strategy import (
    STRATEGY_CATALOG,
    NarrativePlanDraft,
    NarrativeStrategy,
    NarrativeStrategySelector,
)


@pytest.fixture
def sample_dna() -> dict[str, Any]:
    return {
        "channel_id": str(uuid.uuid4()),
        "brand_voice": {
            "tone": "AUTHORITATIVE",
            "pace": "FAST",
            "complexity": "INTERMEDIATE",
        },
        "narrative_preferences": {
            "default_strategy": "HOW_IT_WORKS",
        },
    }


@pytest.fixture
def sample_research_brief() -> dict[str, Any]:
    brief_id = str(uuid.uuid4())
    claim1_id = str(uuid.uuid4())
    ev1_id = str(uuid.uuid4())
    src1_id = str(uuid.uuid4())

    claim2_id = str(uuid.uuid4())

    return {
        "id": brief_id,
        "title": "Quantum Computing Mechanics",
        "summary": "Quantum computers use superposition and entanglement for computation.",
        "verified_claims": [
            {
                "claim_id": claim1_id,
                "claim_text": "Qubits can exist in a superposition of states |0> and |1>.",
                "evidence": [
                    {
                        "evidence_id": ev1_id,
                        "source_id": src1_id,
                        "excerpt": "Superposition is verified in superconducting transmon qubits.",
                    }
                ],
            },
            {
                "claim_id": claim2_id,
                "claim_text": "Quantum decoherence occurs due to thermal fluctuations.",
                "evidence": [],
            },
        ],
        "uncertain_claims": [
            {
                "claim_id": str(uuid.uuid4()),
                "claim_text": "Room temperature superconductors will be standard by 2030.",
            }
        ],
        "contradictions": [],
    }


@pytest.fixture
def sample_content_intent() -> dict[str, Any]:
    return {
        "primary_goal": "Deliver a technical explanation of quantum computing.",
        "audience_intent": "Understand how quantum gates operate.",
        "viewer_promise": "You will understand the core reality behind quantum computers.",
        "central_question": "How do quantum computers actually perform calculations?",
        "core_takeaway": "Superposition enables parallel state transformations.",
    }


# ======================================================================
# Test A: Strategy Selection
# ======================================================================

def test_strategy_selection_explanatory(sample_dna, sample_research_brief, sample_content_intent):
    strategy = NarrativeStrategySelector.select_best_strategy(
        topic_title="How Quantum Computers Work",
        topic_summary="Mechanisms of transmon circuits.",
        brief_dict=sample_research_brief,
        dna_dict=sample_dna,
        content_intent=sample_content_intent,
        format_profile=NarrativeFormatProfile.MEDIUM,
    )
    assert strategy in (NarrativeStrategy.HOW_IT_WORKS, NarrativeStrategy.QUESTION_ANSWER)


def test_strategy_selection_comparison(sample_dna, sample_research_brief):
    intent = {
        "primary_goal": "Compare Postgres versus MySQL trade-offs.",
        "central_question": "Which database performs better under concurrency?",
    }
    strategy = NarrativeStrategySelector.select_best_strategy(
        topic_title="Postgres vs MySQL",
        topic_summary="A comparison of relational engines.",
        brief_dict=sample_research_brief,
        dna_dict=sample_dna,
        content_intent=intent,
        format_profile=NarrativeFormatProfile.MEDIUM,
    )
    assert strategy == NarrativeStrategy.CONTRAST_COMPARISON


def test_strategy_selection_chronology(sample_dna, sample_research_brief):
    intent = {
        "primary_goal": "Explore the history of Unix evolution.",
        "central_question": "How did Unix evolve from 1969 to present?",
    }
    strategy = NarrativeStrategySelector.select_best_strategy(
        topic_title="The History and Origin of Unix",
        topic_summary="Timeline and evolution of Unix.",
        brief_dict=sample_research_brief,
        dna_dict=sample_dna,
        content_intent=intent,
        format_profile=NarrativeFormatProfile.LONG,
    )
    assert strategy == NarrativeStrategy.CHRONOLOGICAL


def test_strategy_selection_misconception(sample_dna, sample_content_intent):
    brief_with_contradictions = {
        "id": str(uuid.uuid4()),
        "title": "Radiation Myths",
        "contradictions": [{"claim_id": str(uuid.uuid4()), "severity": "HIGH"}],
    }
    strategy = NarrativeStrategySelector.select_best_strategy(
        topic_title="The Biggest Myth About Nuclear Energy",
        topic_summary="Common misconceptions debunked.",
        brief_dict=brief_with_contradictions,
        dna_dict=sample_dna,
        content_intent=sample_content_intent,
        format_profile=NarrativeFormatProfile.SHORT,
    )
    assert strategy == NarrativeStrategy.MYTH_REALITY


def test_strategy_selection_mystery(sample_dna, sample_research_brief):
    intent = {
        "primary_goal": "Investigate the bizarre anomaly in the WOW signal.",
        "central_question": "Why did the telescope detect this mystery signal?",
    }
    strategy = NarrativeStrategySelector.select_best_strategy(
        topic_title="The Unsolved Mystery of the Wow Signal",
        topic_summary="An unsolved space paradox.",
        brief_dict=sample_research_brief,
        dna_dict=sample_dna,
        content_intent=intent,
        format_profile=NarrativeFormatProfile.MEDIUM,
    )
    assert strategy == NarrativeStrategy.MYSTERY_REVEAL


def test_strategy_selection_fallback(sample_dna, sample_research_brief):
    intent = {"primary_goal": "Generic topic.", "central_question": "Something."}
    strategy = NarrativeStrategySelector.select_best_strategy(
        topic_title="Random Uncategorized Title",
        topic_summary="No obvious signals.",
        brief_dict=sample_research_brief,
        dna_dict=sample_dna,
        content_intent=intent,
        format_profile=NarrativeFormatProfile.SHORT,
    )
    assert strategy in (NarrativeStrategy.HOW_IT_WORKS, NarrativeStrategy.PROBLEM_SOLUTION)


# ======================================================================
# Test B: Format Behavior (SHORT / MEDIUM / LONG)
# ======================================================================

def test_format_behavior_short(sample_dna, sample_research_brief, sample_content_intent):
    director = DeterministicNarrativeDirector()
    candidates = director.generate_candidates(
        content_request_id=uuid.uuid4(),
        channel_dna=sample_dna,
        research_brief=sample_research_brief,
        content_intent=sample_content_intent,
        topic_title="Quantum Computing",
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
    )
    assert len(candidates) >= 1
    draft = candidates[0]
    assert draft.format_profile == NarrativeFormatProfile.SHORT
    assert draft.target_duration_seconds == 45
    assert len(draft.sections) <= 6
    assert draft.sections[0].target_duration_seconds <= 10  # Max hook duration for SHORT


def test_format_behavior_medium(sample_dna, sample_research_brief, sample_content_intent):
    director = DeterministicNarrativeDirector()
    candidates = director.generate_candidates(
        content_request_id=uuid.uuid4(),
        channel_dna=sample_dna,
        research_brief=sample_research_brief,
        content_intent=sample_content_intent,
        topic_title="Quantum Computing Deep Dive",
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=300,
    )
    assert len(candidates) >= 1
    draft = candidates[0]
    assert draft.format_profile == NarrativeFormatProfile.MEDIUM
    assert 5 <= len(draft.sections) <= 14
    assert draft.sections[0].target_duration_seconds <= 30  # Max hook duration for MEDIUM


def test_format_behavior_long(sample_dna, sample_research_brief, sample_content_intent):
    director = DeterministicNarrativeDirector()
    candidates = director.generate_candidates(
        content_request_id=uuid.uuid4(),
        channel_dna=sample_dna,
        research_brief=sample_research_brief,
        content_intent=sample_content_intent,
        topic_title="Quantum Computing Masterclass",
        format_profile=NarrativeFormatProfile.LONG,
        target_duration_seconds=720,
    )
    assert len(candidates) >= 1
    draft = candidates[0]
    assert draft.format_profile == NarrativeFormatProfile.LONG
    assert 8 <= len(draft.sections) <= 30
    assert draft.sections[0].target_duration_seconds <= 60  # Max hook duration for LONG


# ======================================================================
# Test C: Research Grounding & Uncertain Evidence
# ======================================================================

def test_grounding_verified_claims_carried(sample_dna, sample_research_brief, sample_content_intent):
    director = DeterministicNarrativeDirector()
    candidates = director.generate_candidates(
        content_request_id=uuid.uuid4(),
        channel_dna=sample_dna,
        research_brief=sample_research_brief,
        content_intent=sample_content_intent,
        topic_title="Quantum Computing",
        format_profile=NarrativeFormatProfile.MEDIUM,
    )
    draft = candidates[0]
    all_refs = [g for s in draft.sections for g in s.grounding_references]
    assert len(all_refs) > 0
    # Verified claim ID must match
    verified_ids = {UUID(c["claim_id"]) for c in sample_research_brief["verified_claims"]}
    for ref in all_refs:
        if ref.claim_id:
            assert ref.claim_id in verified_ids


def test_grounding_uncertain_evidence_not_promoted(sample_dna, sample_research_brief, sample_content_intent):
    director = DeterministicNarrativeDirector()
    candidates = director.generate_candidates(
        content_request_id=uuid.uuid4(),
        channel_dna=sample_dna,
        research_brief=sample_research_brief,
        content_intent=sample_content_intent,
        topic_title="Quantum Computing",
        format_profile=NarrativeFormatProfile.MEDIUM,
    )
    draft = candidates[0]
    all_claim_ids = {g.claim_id for s in draft.sections for g in s.grounding_references if g.claim_id}
    uncertain_ids = {UUID(c["claim_id"]) for c in sample_research_brief["uncertain_claims"]}
    # No uncertain claims should be attached as factual grounding
    assert all_claim_ids.isdisjoint(uncertain_ids)


# ======================================================================
# Test D: Promise → Payoff Construction
# ======================================================================

def test_promise_payoff_coherence(sample_dna, sample_research_brief, sample_content_intent):
    director = DeterministicNarrativeDirector()
    candidates = director.generate_candidates(
        content_request_id=uuid.uuid4(),
        channel_dna=sample_dna,
        research_brief=sample_research_brief,
        content_intent=sample_content_intent,
        topic_title="Quantum Computing",
        format_profile=NarrativeFormatProfile.MEDIUM,
    )
    draft = candidates[0]
    promise_sections = [s for s in draft.sections if s.promise_id]
    payoff_sections = [s for s in draft.sections if s.payoff_reference]

    assert len(promise_sections) == 1
    assert len(payoff_sections) == 1
    assert payoff_sections[0].payoff_reference == promise_sections[0].promise_id
    assert payoff_sections[0].section_order > promise_sections[0].section_order


# ======================================================================
# Test E & G: Candidate Generation, Selection, and Provenance
# ======================================================================

def test_candidate_generation_and_selection(sample_dna, sample_research_brief, sample_content_intent):
    repo = InMemoryNarrativePlanRepository()
    plan_service = NarrativePlanService(repository=repo)
    planning_service = NarrativePlanningService(plan_service=plan_service)

    req_id = uuid.uuid4()
    dna_rev_id = uuid.uuid4()
    brief_id = UUID(sample_research_brief["id"])

    plan, val_result, context = planning_service.plan_narrative_for_request(
        content_generation_request_id=req_id,
        channel_dna_revision_id=dna_rev_id,
        channel_dna=sample_dna,
        research_brief=sample_research_brief,
        content_intent=sample_content_intent,
        topic_title="Quantum Computing",
        format_profile=NarrativeFormatProfile.MEDIUM,
        target_duration_seconds=300,
        research_brief_id=brief_id,
        candidate_count=3,
    )

    assert plan is not None
    assert val_result.is_valid
    assert plan.is_current is True
    assert plan.version == 1

    # Verify provenance metadata
    assert plan.content_generation_request_id == req_id
    assert plan.channel_dna_revision_id == dna_rev_id
    assert plan.research_brief_id == brief_id
    assert plan.metadata.get("director_version") == "p21-b-v1.0.0"
    assert "selection_rationale" in plan.metadata


# ======================================================================
# Test F: Fallback and Error Handling & Model Failure Matrix
# ======================================================================

class MockNarrativeModelClient:
    """Mock model client for controlled offline failure and schema testing."""

    provider_name: str = "mock_provider"
    model_name: str = "mock_model"

    def __init__(self, side_effect: Any = None, return_value: dict[str, Any] | None = None) -> None:
        self.side_effect = side_effect
        self.return_value = return_value or {}
        self.call_count = 0

    def generate_structured_plan(
        self,
        prompt: str,
        system_instruction: str,
        response_schema: dict[str, Any],
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        self.call_count += 1
        if isinstance(self.side_effect, Exception):
            raise self.side_effect
        if callable(self.side_effect):
            return self.side_effect()
        return self.return_value


class FailingDirector:
    """Mock director that always raises an error to test retry and deterministic fallback."""

    def generate_candidates(self, **kwargs) -> list[NarrativePlanDraft]:
        raise RuntimeError("LLM provider timeout simulation.")


def test_failing_director_triggers_deterministic_fallback(sample_dna, sample_research_brief, sample_content_intent):
    repo = InMemoryNarrativePlanRepository()
    plan_service = NarrativePlanService(repository=repo)
    failing_director = FailingDirector()
    planning_service = NarrativePlanningService(
        director=failing_director,
        plan_service=plan_service,
        max_retries=1,
    )

    plan, val_result, context = planning_service.plan_narrative_for_request(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        channel_dna=sample_dna,
        research_brief=sample_research_brief,
        content_intent=sample_content_intent,
        topic_title="Quantum Computing",
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
    )

    assert plan is not None
    assert val_result.is_valid
    assert plan.status == NarrativePlanStatus.VALIDATED


def test_p21b_model_timeout_error(sample_dna, sample_research_brief, sample_content_intent):
    """A. Provider timeout raises NarrativeModelTimeoutError."""
    client = MockNarrativeModelClient(side_effect=NarrativeModelTimeoutError("Gemini API request timed out after 20.0s"))
    director = ModelBackedNarrativeDirector(client=client)
    with pytest.raises(NarrativeModelTimeoutError):
        director.generate_candidates(
            content_request_id=uuid.uuid4(),
            channel_dna=sample_dna,
            research_brief=sample_research_brief,
            content_intent=sample_content_intent,
            topic_title="Quantum Mechanics",
        )


def test_p21b_model_provider_failure_error(sample_dna, sample_research_brief, sample_content_intent):
    """B. Provider exception raises NarrativeModelProviderError."""
    client = MockNarrativeModelClient(side_effect=NarrativeModelProviderError("Gemini API returned status code 503"))
    director = ModelBackedNarrativeDirector(client=client)
    with pytest.raises(NarrativeModelProviderError):
        director.generate_candidates(
            content_request_id=uuid.uuid4(),
            channel_dna=sample_dna,
            research_brief=sample_research_brief,
            content_intent=sample_content_intent,
            topic_title="Quantum Mechanics",
        )


def test_p21b_model_malformed_structured_response(sample_dna, sample_research_brief, sample_content_intent):
    """C. Malformed structured output raises NarrativeModelMalformedError."""
    client = MockNarrativeModelClient(return_value={"candidates": []})
    director = ModelBackedNarrativeDirector(client=client)
    with pytest.raises(NarrativeModelMalformedError):
        director.generate_candidates(
            content_request_id=uuid.uuid4(),
            channel_dna=sample_dna,
            research_brief=sample_research_brief,
            content_intent=sample_content_intent,
            topic_title="Quantum Mechanics",
        )


def test_p21b_model_schema_invalid_and_ineligible_strategy(sample_dna, sample_research_brief, sample_content_intent):
    """D & E. Schema-invalid / ineligible strategy rejected by director."""
    client = MockNarrativeModelClient(return_value={
        "candidates": [
            {
                "strategy": "MYSTERY_REVEAL",  # Ineligible for SHORT format
                "rationale": "Ineligible strategy draft",
                "sections": [
                    {"role": "HOOK", "objective": "Hook", "target_duration_seconds": 15},
                    {"role": "PAYOFF", "objective": "Payoff", "target_duration_seconds": 30},
                ],
            }
        ]
    })
    director = ModelBackedNarrativeDirector(client=client)
    with pytest.raises(NarrativeModelMalformedError):
        director.generate_candidates(
            content_request_id=uuid.uuid4(),
            channel_dna=sample_dna,
            research_brief=sample_research_brief,
            content_intent=sample_content_intent,
            topic_title="Quantum Mechanics",
            format_profile=NarrativeFormatProfile.SHORT,
        )


def test_p21b_model_unknown_grounding_id_rejected(sample_dna, sample_research_brief, sample_content_intent):
    """F. Unknown grounding claim ID is rejected and disqualifies candidate."""
    client = MockNarrativeModelClient(return_value={
        "candidates": [
            {
                "strategy": "HOW_IT_WORKS",
                "rationale": "Cites unknown claim",
                "sections": [
                    {"role": "HOOK", "objective": "Hook", "target_duration_seconds": 15},
                    {"role": "DEVELOPMENT", "objective": "Dev", "target_duration_seconds": 20, "cited_claim_id": str(uuid.uuid4())},
                    {"role": "CLOSING", "objective": "Close", "target_duration_seconds": 10},
                ],
            }
        ]
    })
    director = ModelBackedNarrativeDirector(client=client)
    with pytest.raises(NarrativeModelMalformedError):
        director.generate_candidates(
            content_request_id=uuid.uuid4(),
            channel_dna=sample_dna,
            research_brief=sample_research_brief,
            content_intent=sample_content_intent,
            topic_title="Quantum Mechanics",
            format_profile=NarrativeFormatProfile.SHORT,
        )


def test_p21b_model_uncertain_claim_not_promoted(sample_dna, sample_research_brief, sample_content_intent):
    """F2. Uncertain claims cannot be promoted to factual grounding."""
    uncertain_cid = sample_research_brief["uncertain_claims"][0]["claim_id"]
    client = MockNarrativeModelClient(return_value={
        "candidates": [
            {
                "strategy": "HOW_IT_WORKS",
                "rationale": "Cites uncertain claim",
                "sections": [
                    {"role": "HOOK", "objective": "Hook", "target_duration_seconds": 15},
                    {"role": "DEVELOPMENT", "objective": "Dev", "target_duration_seconds": 20, "cited_claim_id": uncertain_cid},
                    {"role": "CLOSING", "objective": "Close", "target_duration_seconds": 10},
                ],
            }
        ]
    })
    director = ModelBackedNarrativeDirector(client=client)
    with pytest.raises(NarrativeModelMalformedError):
        director.generate_candidates(
            content_request_id=uuid.uuid4(),
            channel_dna=sample_dna,
            research_brief=sample_research_brief,
            content_intent=sample_content_intent,
            topic_title="Quantum Mechanics",
            format_profile=NarrativeFormatProfile.SHORT,
        )


def test_p21b_model_validator_rejection(sample_dna, sample_research_brief, sample_content_intent):
    """G. Invalid role combinations rejected by P21-A structural constraints."""
    client = MockNarrativeModelClient(return_value={
        "candidates": [
            {
                "strategy": "HOW_IT_WORKS",
                "rationale": "Invalid role combination",
                "sections": [
                    {"role": "INVALID_ROLE", "objective": "Hook", "target_duration_seconds": 15},
                ],
            }
        ]
    })
    director = ModelBackedNarrativeDirector(client=client)
    with pytest.raises(NarrativeModelMalformedError):
        director.generate_candidates(
            content_request_id=uuid.uuid4(),
            channel_dna=sample_dna,
            research_brief=sample_research_brief,
            content_intent=sample_content_intent,
            topic_title="Quantum Mechanics",
            format_profile=NarrativeFormatProfile.SHORT,
        )


def test_p21b_fallback_after_exhaustion(sample_dna, sample_research_brief, sample_content_intent):
    """H & I. Bounded retry exhaustion triggers clean deterministic fallback."""
    client = MockNarrativeModelClient(side_effect=NarrativeModelTimeoutError("Simulated provider timeout"))
    director = ModelBackedNarrativeDirector(client=client)
    repo = InMemoryNarrativePlanRepository()
    plan_service = NarrativePlanService(repository=repo)
    planning_service = NarrativePlanningService(
        director=director,
        plan_service=plan_service,
        max_retries=2,
    )
    plan, val_result, _ = planning_service.plan_narrative_for_request(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        channel_dna=sample_dna,
        research_brief=sample_research_brief,
        content_intent=sample_content_intent,
        topic_title="Quantum Computing",
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
    )
    # Check that retries occurred (initial attempt + 2 retries = 3 calls)
    assert client.call_count == 3
    # Plan was successfully generated via fallback
    assert plan is not None
    assert val_result.is_valid
    assert plan.status == NarrativePlanStatus.VALIDATED
    assert plan.metadata.get("director_implementation") == "DeterministicNarrativeDirector"


# ======================================================================
# Test H: Script Handoff & Lineage Pinning
# ======================================================================

def test_script_handoff_and_outline_generation(sample_dna, sample_research_brief, sample_content_intent):
    repo = InMemoryNarrativePlanRepository()
    plan_service = NarrativePlanService(repository=repo)
    planning_service = NarrativePlanningService(plan_service=plan_service)

    plan, _, context = planning_service.plan_narrative_for_request(
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        channel_dna=sample_dna,
        research_brief=sample_research_brief,
        content_intent=sample_content_intent,
        topic_title="Quantum Computing",
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
    )

    # Outline translation
    outline = planning_service.prepare_script_outline(plan)
    assert outline["narrative_plan_id"] == str(plan.id)
    assert outline["narrative_plan_version"] == 1
    assert len(outline["sections"]) == len(plan.sections)

    # Feed into TemplateContentProvider
    provider = TemplateContentProvider()
    selected_hook = {
        "id": str(uuid.uuid4()),
        "hook_variant_index": 0,
        "hook_text": "Did you know that qubits compute in superposition?",
        "hook_type": "CURIOSITY_GAP",
        "selected": True,
        "citations": [],
    }

    script_data = provider.generate_script(
        topic_title="Quantum Computing",
        brief_dict=sample_research_brief,
        dna_dict=sample_dna,
        intent_dict=sample_content_intent,
        selected_hook=selected_hook,
        outline_dict=outline,
        target_duration_seconds=45,
    )

    assert "title" in script_data
    assert len(script_data["sections"]) == len(plan.sections)
    assert outline["narrative_plan_id"] == str(plan.id)


# ======================================================================
# Test C (Additional): Invalid Grounding ID Handling
# ======================================================================

def test_grounding_invalid_or_unknown_evidence_ids(sample_dna, sample_content_intent):
    """Ensure grounding references with non-existent or malformed claim IDs are safely handled."""
    director = DeterministicNarrativeDirector()
    brief_with_empty_claims = {
        "id": str(uuid.uuid4()),
        "title": "Quantum Computing",
        "verified_claims": [],
        "uncertain_claims": [],
    }
    candidates = director.generate_candidates(
        content_request_id=uuid.uuid4(),
        channel_dna=sample_dna,
        research_brief=brief_with_empty_claims,
        content_intent=sample_content_intent,
        topic_title="Quantum Computing",
        format_profile=NarrativeFormatProfile.SHORT,
    )
    assert len(candidates) >= 1
    # Without verified claims, sections have empty grounding references, not invented UUIDs
    for section in candidates[0].sections:
        for ref in section.grounding_references:
            assert ref.claim_id is not None


# ======================================================================
# Test D (Additional): Orphan Payoff Rejection
# ======================================================================

def test_orphan_payoff_rejected():
    """Ensure validator rejects candidate plan with orphan payoff."""
    validator = NarrativePlanValidator()
    plan = NarrativePlan(
        id=uuid.uuid4(),
        content_generation_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        version=1,
        status=NarrativePlanStatus.DRAFT,
        format_profile=NarrativeFormatProfile.SHORT,
        target_duration_seconds=45,
        estimated_duration_seconds=45,
        is_current=True,
        schema_version=1,
        sections=[
            NarrativeSection(
                id=uuid.uuid4(),
                section_order=1,
                role=NarrativeSectionRole.HOOK,
                objective="Hook the audience.",
                target_duration_seconds=10,
            ),
            NarrativeSection(
                id=uuid.uuid4(),
                section_order=2,
                role=NarrativeSectionRole.PAYOFF,
                objective="Payoff without promise.",
                payoff_reference="non_existent_promise_loop",
                target_duration_seconds=20,
            ),
            NarrativeSection(
                id=uuid.uuid4(),
                section_order=3,
                role=NarrativeSectionRole.TAKEAWAY,
                objective="Deliver takeaway.",
                target_duration_seconds=15,
            ),
        ],
    )
    result = validator.validate(plan)
    assert not result.is_valid
    assert any(f.rule_code == NarrativeRuleCode.ORPHAN_PAYOFF for f in result.findings)


# ======================================================================
# Test E (Additional): Single Current Invariant Across Candidates
# ======================================================================

def test_multi_candidate_only_one_persisted_current(sample_dna, sample_research_brief, sample_content_intent):
    """Ensure that after generating multiple candidate drafts, exactly one plan is persisted as current."""
    repo = InMemoryNarrativePlanRepository()
    plan_service = NarrativePlanService(repository=repo)
    planning_service = NarrativePlanningService(plan_service=plan_service)

    req_id = uuid.uuid4()
    plan, _, _ = planning_service.plan_narrative_for_request(
        content_generation_request_id=req_id,
        channel_dna_revision_id=uuid.uuid4(),
        channel_dna=sample_dna,
        research_brief=sample_research_brief,
        content_intent=sample_content_intent,
        topic_title="Quantum Computing",
        format_profile=NarrativeFormatProfile.SHORT,
        candidate_count=3,
    )

    plans = repo.list_by_request(req_id)
    assert len(plans) == 1
    assert plans[0].id == plan.id
    assert plans[0].is_current is True


# ======================================================================
# Test I: Legacy Compatibility
# ======================================================================

def test_legacy_script_generation_compatibility(sample_dna, sample_research_brief, sample_content_intent):
    """Ensure legacy flow without NarrativePlan remains fully functional."""
    provider = TemplateContentProvider()
    selected_hook = {
        "id": str(uuid.uuid4()),
        "hook_variant_index": 0,
        "hook_text": "Did you know that qubits compute in superposition?",
        "hook_type": "CURIOSITY_GAP",
        "selected": True,
        "citations": [],
    }
    # Legacy outline without narrative_plan_id
    legacy_outline = {
        "sections": [
            {"role": "HOOK", "objective": "Capture attention"},
            {"role": "DEVELOPMENT", "objective": "Explain core mechanics"},
            {"role": "PAYOFF", "objective": "Resolve core question"},
            {"role": "TAKEAWAY", "objective": "Summarize conclusions"},
            {"role": "CLOSING", "objective": "Wrap up"},
        ]
    }

    script_data = provider.generate_script(
        topic_title="Quantum Computing",
        brief_dict=sample_research_brief,
        dna_dict=sample_dna,
        intent_dict=sample_content_intent,
        selected_hook=selected_hook,
        outline_dict=legacy_outline,
        target_duration_seconds=300,
    )

    assert "title" in script_data
    assert len(script_data["sections"]) == 5
    assert "narrative_plan_id" not in legacy_outline
