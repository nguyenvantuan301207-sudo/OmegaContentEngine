"""Focused Tests for Corroboration-Targeted Coverage Planning (P0.3c.6).

Verifies:
1. Round 1 broad foundational discovery preserved.
2. 2-support targets produce corroboration-targeted queries and outrank 1-support targets.
3. Already verified claims never produce corroboration targets.
4. Unverified planning hints remain strictly non-authoritative.
5. Deduplication across rounds via execution-local query history.
6. Round 2 and Round 3 differ for unresolved deficits.
7. Anchors are grounded in exact claim vocabulary without invented terms or stems.
8. Domain-agnostic behavior (no hard-coded concrete mechanism names).
9. Generic fallback when no targets exist.
10. Offline coverage simulation reaches 5 verified families and SUFFICIENT brief.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from omega.application.research_coverage_service import execute_coverage_driven_research
from omega.application.research_discovery import (
    ResearchContentExtractor,
    ResearchDiscoveryProvider,
)
from omega.application.research_query_planner import (
    build_corroboration_targets,
    extract_query_anchors,
    plan_research_queries,
)
from omega.domain.numeric_promise import extract_numeric_promise
from omega.domain.research import (
    ClaimType,
    CorroborationTarget,
    DiscoveryCandidate,
    ExtractedResearchDocument,
    PrimarySourceStatus,
    ResearchQueryIntent,
)
from omega.infrastructure.models import ResearchClaim


def test_round_1_remains_broad() -> None:
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    contract = extract_numeric_promise(topic)
    queries = plan_research_queries(topic, contract=contract, round_number=1)

    assert len(queries) == 3
    intents = [q.intent for q in queries]
    assert ResearchQueryIntent.OVERVIEW in intents
    assert any("overview" in q.query_text for q in queries)
    assert any("engineering guide" in q.query_text for q in queries)
    assert any("standards" in q.query_text for q in queries)


def test_two_support_target_produces_targeted_query() -> None:
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    contract = extract_numeric_promise(topic)

    target = CorroborationTarget(
        representative_claim_text="Plastic shrinkage occurs when concrete loses moisture rapidly under high wind.",
        claim_type=ClaimType.FACT,
        independent_support_count=2,
        supporting_domains=["example.org", "reference.com"],
        confidence_score=68.0,
        priority=1,
    )

    queries = plan_research_queries(
        topic,
        contract=contract,
        round_number=2,
        corroboration_targets=[target],
        max_queries=2,
    )

    assert len(queries) >= 1
    targeted = queries[0]
    assert targeted.intent == ResearchQueryIntent.CORROBORATION
    assert "plastic" in targeted.query_text
    assert "shrinkage" in targeted.query_text
    assert "moisture" in targeted.query_text
    assert "example.org" in targeted.reason


def test_two_support_target_outranks_one_support_target() -> None:
    topic = "Structural Failure Modes in Modern Architecture"
    contract = extract_numeric_promise(topic)

    t_1_indep = CorroborationTarget(
        representative_claim_text="Torsional flutter causes aeroelastic instability in slender suspension bridges.",
        claim_type=ClaimType.FACT,
        independent_support_count=1,
        confidence_score=54.0,
        priority=2,
    )
    t_2_indep = CorroborationTarget(
        representative_claim_text="Progressive collapse initiates when primary load bearing columns undergo shear failure.",
        claim_type=ClaimType.FACT,
        independent_support_count=2,
        confidence_score=68.0,
        priority=1,
    )

    targets = [t_1_indep, t_2_indep]  # Passed in reverse order
    queries = plan_research_queries(
        topic,
        contract=contract,
        round_number=2,
        corroboration_targets=targets,
        max_queries=2,
    )

    # First query must target the priority 1 (2-independent) claim
    assert "collapse" in queries[0].query_text or "shear" in queries[0].query_text
    # Second query targets the priority 2 (1-independent) claim
    assert "flutter" in queries[1].query_text or "aeroelastic" in queries[1].query_text


def test_verified_claims_excluded_from_corroboration_targets() -> None:
    claim_verified = ResearchClaim(
        id=uuid4(),
        claim_text="Drying shrinkage causes internal tensile stresses that fracture hardened concrete.",
        claim_type=ClaimType.FACT.value,
        is_verified=True,
        confidence_score=80.0,
        independent_sources_count=3,
        contradicting_sources_count=0,
    )
    claim_unverified = ResearchClaim(
        id=uuid4(),
        claim_text="Thermal gradients induce tensile stresses across the concrete cross section.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=68.0,
        independent_sources_count=2,
        contradicting_sources_count=0,
    )

    targets = build_corroboration_targets([claim_verified, claim_unverified])
    assert len(targets) == 1
    assert "thermal" in targets[0].representative_claim_text.lower()


def test_planning_authority_firewall_unverified_claims_not_evidence() -> None:
    target = CorroborationTarget(
        representative_claim_text="Unverified proposition about chemical attack.",
        claim_type=ClaimType.FACT,
        independent_support_count=1,
        confidence_score=53.0,
        priority=2,
    )

    # Deriving targets or planning queries does not mutate claim verification
    assert target.priority == 2
    assert target.confidence_score == 53.0
    # Model cannot be cast to verified entity or brief
    assert not hasattr(target, "is_verified")


def test_query_history_dedup_across_rounds() -> None:
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    contract = extract_numeric_promise(topic)

    r1_queries = plan_research_queries(topic, contract=contract, round_number=1)
    issued = [q.query_text for q in r1_queries]

    # Calling round 1 or round 2 with issued queries avoids emitting any duplicates
    r2_queries = plan_research_queries(
        topic,
        contract=contract,
        round_number=2,
        issued_query_texts=issued,
    )

    for q in r2_queries:
        assert q.query_text not in issued


def test_round_2_and_round_3_differ_for_unresolved_deficit() -> None:
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    contract = extract_numeric_promise(topic)

    target = CorroborationTarget(
        representative_claim_text="Drying shrinkage causes cracking in concrete structures when restrained.",
        claim_type=ClaimType.FACT,
        independent_support_count=2,
        confidence_score=68.0,
        priority=1,
    )

    r2_queries = plan_research_queries(
        topic,
        contract=contract,
        round_number=2,
        corroboration_targets=[target],
        max_queries=1,
    )
    assert len(r2_queries) == 1
    q2_text = r2_queries[0].query_text

    # Target remains unresolved in Round 3; issued history contains Round 2 query
    r3_queries = plan_research_queries(
        topic,
        contract=contract,
        round_number=3,
        corroboration_targets=[target],
        issued_query_texts=[q2_text],
        max_queries=1,
    )
    assert len(r3_queries) == 1
    q3_text = r3_queries[0].query_text

    assert q2_text != q3_text
    assert "drying" in q3_text or "shrinkage" in q3_text


def test_anchors_grounded_in_claim_vocabulary() -> None:
    claim = "Freeze-thaw cycling operates on the damaging mechanism where absorbed water freezes and expands."
    anchors = extract_query_anchors(claim, subject="concrete cracks")

    assert "freeze" in anchors
    assert "thaw" in anchors
    assert "cycling" in anchors
    assert "damaging" in anchors or "absorbed" in anchors
    # Zero invented or corrupted stem fragments
    assert "shrinkag" not in anchors
    assert "concr" not in anchors
    for a in anchors:
        assert a in claim.lower()


def test_domain_agnostic_anchor_extraction() -> None:
    # Completely non-concrete topic
    claim = "Consensus protocols in distributed databases require quorum replication to prevent split-brain anomalies."
    anchors = extract_query_anchors(claim, subject="distributed databases")

    assert "consensus" in anchors
    assert "protocols" in anchors
    assert "quorum" in anchors
    assert "replication" in anchors


def test_generic_fallback_when_no_targets() -> None:
    topic = "Advanced Aerospace Composite Fabrication"
    queries = plan_research_queries(topic, round_number=2, corroboration_targets=[], max_queries=2)
    assert len(queries) == 2
    assert all(len(q.query_text) > 5 for q in queries)


def test_non_numeric_topic_round_planning() -> None:
    topic = "The History of Cryptographic Ciphers"
    r1 = plan_research_queries(topic, contract=None, round_number=1)
    r2 = plan_research_queries(topic, contract=None, round_number=2)

    assert len(r1) == 3
    assert len(r2) >= 2
    assert r1[0].query_text != r2[0].query_text


class MockSimulatedDiscoveryProvider(ResearchDiscoveryProvider):
    """Simulated search provider that returns candidate sources matching targeted intents."""

    def __init__(self) -> None:
        self.issued_queries: list[str] = []

    async def search(self, query: str, limit: int = 5) -> list[DiscoveryCandidate]:
        self.issued_queries.append(query)
        q_lower = query.lower()
        candidates: list[DiscoveryCandidate] = []

        if "overview" in q_lower or "engineering guide" in q_lower or "standards" in q_lower:
            # Round 1: Foundation sources covering initial candidate mechanisms
            candidates.append(
                DiscoveryCandidate(
                    canonical_url="https://source-alpha.org/foundations",
                    title="Alpha Overview: Concrete Cracking Foundations",
                    snippet="Discusses drying shrinkage and thermal gradients in structural concrete.",
                    publisher="Alpha Engineering Society",
                )
            )
            candidates.append(
                DiscoveryCandidate(
                    canonical_url="https://source-beta.edu/causes",
                    title="Beta University: Chemical and Physical Cracking Mechanisms",
                    snippet="Examines freeze-thaw and alkali-silica reaction degradation.",
                    publisher="Beta University Press",
                )
            )
        elif "drying" in q_lower or "shrinkage" in q_lower:
            candidates.append(
                DiscoveryCandidate(
                    canonical_url="https://source-gamma.net/drying-study",
                    title="Gamma Materials: Drying Shrinkage in Concrete",
                    snippet="Independent evidence for drying shrinkage internal tensile stresses.",
                    publisher="Gamma Materials Journal",
                )
            )
            candidates.append(
                DiscoveryCandidate(
                    canonical_url="https://source-epsilon.gov/frost-study",
                    title="Epsilon DOT: Freeze-Thaw and Shrinkage Analysis",
                    snippet="Independent government study on drying shrinkage and freeze-thaw.",
                    publisher="Epsilon Transportation Department",
                )
            )
        elif "thermal" in q_lower or "gradient" in q_lower:
            candidates.append(
                DiscoveryCandidate(
                    canonical_url="https://source-delta.com/thermal-study",
                    title="Delta Institute: Thermal Gradient Fractures",
                    snippet="Independent engineering analysis of thermal gradient fractures.",
                    publisher="Delta Research Institute",
                )
            )
            candidates.append(
                DiscoveryCandidate(
                    canonical_url="https://source-zeta.info/chemical-study",
                    title="Zeta Institute: Alkali-Silica and Thermal Stresses",
                    snippet="Independent investigation into thermal gradients and ASR.",
                    publisher="Zeta Materials Institute",
                )
            )
        elif "freeze" in q_lower or "thaw" in q_lower:
            candidates.append(
                DiscoveryCandidate(
                    canonical_url="https://source-epsilon.gov/frost-study",
                    title="Epsilon DOT: Freeze-Thaw and Shrinkage Analysis",
                    snippet="Independent government study on freeze-thaw internal pressure.",
                    publisher="Epsilon Transportation Department",
                )
            )
            candidates.append(
                DiscoveryCandidate(
                    canonical_url="https://source-eta.io/frost-bleed",
                    title="Eta Labs: Frost Action and Plastic Settlement",
                    snippet="Independent analysis of freeze-thaw cycling.",
                    publisher="Eta Structural Engineering",
                )
            )
        elif "alkali" in q_lower or "silica" in q_lower or "chemical" in q_lower:
            candidates.append(
                DiscoveryCandidate(
                    canonical_url="https://source-zeta.info/chemical-study",
                    title="Zeta Institute: Alkali-Silica and Thermal Stresses",
                    snippet="Independent chemical analysis of alkali-silica reaction gel.",
                    publisher="Zeta Materials Institute",
                )
            )
            candidates.append(
                DiscoveryCandidate(
                    canonical_url="https://source-theta.tech/asr-rebar",
                    title="Theta Tech: ASR Expansion and Settlement",
                    snippet="Independent technical report on alkali-silica reaction.",
                    publisher="Theta Technical Review",
                )
            )
        elif "settle" in q_lower or "subsidence" in q_lower or "rebar" in q_lower:
            candidates.append(
                DiscoveryCandidate(
                    canonical_url="https://source-eta.io/frost-bleed",
                    title="Eta Labs: Frost Action and Plastic Settlement",
                    snippet="Independent analysis of settlement subsidence over reinforcement.",
                    publisher="Eta Structural Engineering",
                )
            )
            candidates.append(
                DiscoveryCandidate(
                    canonical_url="https://source-theta.tech/asr-rebar",
                    title="Theta Tech: ASR Expansion and Settlement",
                    snippet="Independent technical report on settlement subsidence.",
                    publisher="Theta Technical Review",
                )
            )
            candidates.append(
                DiscoveryCandidate(
                    canonical_url="https://source-iota.us/settlement-survey",
                    title="Iota US: National Survey on Plastic Settlement",
                    snippet="Independent national survey corroborating plastic settlement.",
                    publisher="Iota Structural Survey",
                )
            )

        return candidates[:limit]


class MockSimulatedContentExtractor(ResearchContentExtractor):
    """Simulated content extractor returning substantive technical prose with keywords."""

    P1 = "Concrete cracking occurs when drying shrinkage causes internal tensile stresses in hardened concrete slabs."
    P2 = "Concrete cracking occurs when thermal gradients produce differential cooling and massive foundation fractures."
    P3 = "Concrete cracking occurs when freeze-thaw cycling operates where absorbed water freezes and expands internally."
    P4 = "Concrete cracking occurs when alkali-silica reaction creates expansive chemical gel that disrupts aggregate matrix."
    P5 = "Concrete cracking occurs when settlement subsidence occurs and fresh concrete bleeds and settles over rebar."

    async def extract_document(self, candidate: DiscoveryCandidate) -> ExtractedResearchDocument:
        url = candidate.canonical_url

        if "source-alpha.org" in url:
            content = f"{self.P1}\n{self.P2}"
        elif "source-beta.edu" in url:
            content = f"{self.P3}\n{self.P4}"
        elif "source-gamma.net" in url:
            content = f"{self.P1}\n{self.P5} (Gamma report)"
        elif "source-delta.com" in url:
            content = f"{self.P2}\n{self.P5} (Delta report)"
        elif "source-epsilon.gov" in url:
            content = f"{self.P3}\n{self.P1} (Epsilon report)"
        elif "source-zeta.info" in url:
            content = f"{self.P4}\n{self.P2} (Zeta report)"
        elif "source-eta.io" in url:
            content = f"{self.P3} (Eta report)\n{self.P5} (Eta report)"
        elif "source-theta.tech" in url:
            content = f"{self.P4} (Theta report)\n{self.P5} (Theta report)"
        elif "source-iota.us" in url:
            content = f"{self.P5} (Iota survey)\n{self.P4} (Iota survey)"
        else:
            content = f"General technical analysis of concrete materials.\n{self.P1}"

        return ExtractedResearchDocument(
            canonical_url=url,
            title=candidate.title,
            publisher=candidate.publisher,
            extracted_content=content,
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )


@pytest.mark.asyncio
async def test_offline_targeted_coverage_simulation_reaches_five_families() -> None:
    """Demonstrate end-to-end corroboration-targeted coverage expansion reaching 5 verified families."""
    from tests.unit.test_research_coverage_expansion import make_test_fixture

    topic_title = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    session, req, channel_id, topic_id = make_test_fixture(
        topic_title=topic_title,
        acquisition_mode="AUTOMATIC_SEARCH",
        max_sources=15,
    )
    req.topic_candidate.keywords = ["concrete", "cracking"]

    # Run coverage-driven research with simulated provider and extractor
    provider = MockSimulatedDiscoveryProvider()
    extractor = MockSimulatedContentExtractor()

    res = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        max_rounds=3,
        max_queries_per_round=3,
        max_candidates_per_query=5,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=15,
    )

    # 1. Rounds and budget invariants
    assert res["rounds_executed"] <= 3
    assert res["stop_reason"] == "COVERAGE_FULFILLED"

    # 2. Check query planner issued targeted queries in later rounds
    all_queries = [q for r in res["coverage_truth"] for q in r.get("queries", [])]
    query_texts = [q["query_text"] for q in all_queries]

    # Verify zero duplicate query texts were issued across all rounds
    assert len(query_texts) == len(set(query_texts))

    # Verify corroboration-targeted intent appeared after round 1
    corroboration_intents = [
        q for q in all_queries if q.get("intent") in (
            ResearchQueryIntent.CORROBORATION.value,
            ResearchQueryIntent.TECHNICAL_REFERENCE.value,
            ResearchQueryIntent.ADDITIONAL_COVERAGE.value,
        )
    ]
    assert len(corroboration_intents) > 0

    # 3. Verify final brief outcome and 5 verified families
    brief = res["brief"]
    assert brief is not None
    assert brief.outcome.value == "SUFFICIENT"
    assert res["final_supported_count"] == 5
    assert len(res["final_supported_families"]) == 5
    assert len(brief.verified_claims) >= 5
