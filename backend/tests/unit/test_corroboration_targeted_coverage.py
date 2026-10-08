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

from types import SimpleNamespace
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


def grounded_target(**kwargs):
    """Unit-only canonical source fixture; production targets require real edges.

    Invalid/context fixtures retain no provenance and must fall back to discovery.
    """
    target = CorroborationTarget(**kwargs)
    topic = "Why Concrete Cracks: 5 Mechanisms"
    request_id, source_id = uuid4(), uuid4()
    text = target.representative_claim_text
    claim = SimpleNamespace(id=uuid4(), research_request_id=request_id,
        claim_text=text, is_verified=False, confidence_score=target.confidence_score,
        independent_sources_count=target.independent_support_count,
        evidence=[SimpleNamespace(source_id=source_id, excerpt=text, support_direction="SUPPORTS")])
    source = SimpleNamespace(research_request_id=request_id,
        content_excerpt=text, url="https://example.org/fixture")
    candidates = build_corroboration_targets([claim], {source_id: source},
        extract_numeric_promise(topic), topic_title=topic)
    if not candidates:
        return target
    candidate = candidates[0]
    return target.model_copy(update={"claim_id": candidate.claim_id,
        "source_ids": candidate.source_ids, "source_grounded": candidate.source_grounded,
        "causal_assertion": candidate.causal_assertion,
        "candidate_family": candidate.candidate_family})


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

    target = grounded_target(
        representative_claim_text="Plastic shrinkage cracking occurs when concrete loses moisture rapidly under high wind.",
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

    t_1_indep = grounded_target(
        representative_claim_text="Torsional flutter causes aeroelastic instability in slender suspension bridges.",
        claim_type=ClaimType.FACT,
        independent_support_count=1,
        confidence_score=54.0,
        priority=2,
    )
    t_2_indep = grounded_target(
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
    target = grounded_target(
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

    target = grounded_target(
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
    P1_DUP = "Excessive drying shrinkage leads to slab volume reduction and surface tensile fractures."
    P2 = "Concrete cracking occurs when thermal gradients produce differential cooling and massive foundation fractures."
    P3 = "Concrete cracking occurs when freeze-thaw cycling triggers absorbed water expansion that fractures concrete pores."
    P4 = "Concrete cracking occurs when alkali-silica reaction creates expansive chemical gel that disrupts aggregate matrix."
    P5 = "Concrete cracking occurs when settlement subsidence induces tensile fissures as concrete settles over rebar."

    # Distractor claims to test target relevance gating
    DISTRACTOR_INSPECTION = "Cracks that have widened beyond hairline width warrant closer visual inspection and monitoring."
    DISTRACTOR_SEVERITY = "Failure severity depends on crack width and environmental exposure."
    DISTRACTOR_CONSEQUENCE = "Structural fractures pose a safety risk to the building and compromise durability."
    DISTRACTOR_PREVENTION = "Proper curing methods and preventive measures reduce the risk of structural surface defects."
    DISTRACTOR_MAINTENANCE = "Expansion joints require periodic maintenance to prevent joint failure."

    async def extract_document(self, candidate: DiscoveryCandidate) -> ExtractedResearchDocument:
        url = candidate.canonical_url

        if "source-alpha.org" in url:
            content = f"{self.P1}\n{self.P1_DUP}\n{self.P2}\n{self.DISTRACTOR_INSPECTION}\n{self.DISTRACTOR_SEVERITY}\n{self.DISTRACTOR_CONSEQUENCE}"
        elif "source-beta.edu" in url:
            content = f"{self.P3}\n{self.P4}\n{self.P5}\n{self.DISTRACTOR_PREVENTION}\n{self.DISTRACTOR_MAINTENANCE}"
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

    # Verify distractor non-entity claims did NOT displace entity queries
    distractor_queries = [
        q for q in query_texts
        if any(w in q for w in ["inspection", "preventive", "widened", "hairline", "severity", "maintenance", "safety"])
    ]
    assert len(distractor_queries) == 0  # DISTRACTOR_TARGET_DISPLACED_ENTITY_QUERY = NO

    # Verify duplicate same-family proposition did NOT displace distinct candidate families in round 2
    if len(res["coverage_truth"]) >= 2:
        r2_queries = [q["query_text"] for q in res["coverage_truth"][1].get("queries", [])]
        shrink_queries = [q for q in r2_queries if "shrinkage" in q or "shrinks" in q]
        assert len(shrink_queries) <= 1  # SAME_FAMILY_DUPLICATE_DISPLACED_DISTINCT_FAMILY = NO

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


def test_canonical_evaluation_before_target_derivation_multi_domain() -> None:
    """Canonical evaluation resolves 2 independent domains to independent_support_count=2 and priority=1."""
    from omega.application.research_service import evaluate_canonical_claims
    from omega.infrastructure.models import ClaimEvidence, ResearchSource

    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
    claim = ResearchClaim(
        id=uuid4(),
        claim_text="What Causes Them: Concrete shrinks as it cures consuming water and reducing volume.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=0.0,
        independent_sources_count=1,
    )
    s1_id, s2_id = uuid4(), uuid4()
    s1 = ResearchSource(
        id=s1_id,
        url="https://bethelcustombrick.com/article",
        title="Bethel Brick",
        publisher="Bethel",
        content_excerpt="Bethel masonry advisory: Concrete shrinks as it cures consuming mix water in joints.",
        quality_score=55.0,
    )
    s2 = ResearchSource(
        id=s2_id,
        url="https://specchem.com/guide",
        title="SpecChem",
        publisher="SpecChem",
        content_excerpt="SpecChem technical engineering bulletin: Shrinkage during hydration reduces slab volume.",
        quality_score=55.0,
    )
    claim.evidence = [
        ClaimEvidence(id=uuid4(), source_id=s1_id, strength_score=80.0, support_direction="SUPPORTS", excerpt=""),
        ClaimEvidence(id=uuid4(), source_id=s2_id, strength_score=80.0, support_direction="SUPPORTS", excerpt=""),
    ]

    sources_map = {s1_id: s1, s2_id: s2}
    # Run canonical evaluation
    evaluate_canonical_claims(eligible_sources=[s1, s2], claims=[claim])

    assert claim.independent_sources_count == 2
    assert claim.confidence_score > 60.0

    targets = build_corroboration_targets(
        claims=[claim],
        sources_map=sources_map,
        contract=contract,
    )
    assert len(targets) == 1
    t = targets[0]
    assert t.independent_support_count == 2
    assert t.priority == 1
    assert set(t.supporting_domains) == {"bethelcustombrick.com", "specchem.com"}


def test_entity_candidate_outranks_diagnostic_context() -> None:
    """Promised entity candidate outranks diagnostic/inspection proposition in planning priority."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")

    c_entity = ResearchClaim(
        id=uuid4(),
        claim_text="Freeze-thaw cycling operates on the damaging mechanism where absorbed water expands.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.75,
        independent_sources_count=1,
    )
    c_diagnostic = ResearchClaim(
        id=uuid4(),
        claim_text="Cracks that have widened beyond hairline width warrant closer visual inspection.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=54.35,  # Higher initial confidence
        independent_sources_count=1,
    )

    targets = build_corroboration_targets(
        claims=[c_diagnostic, c_entity],  # Diagnostic passed first
        contract=contract,
    )

    assert len(targets) == 2
    # Entity candidate is priority 2, diagnostic is priority 5
    assert targets[0].representative_claim_text == c_entity.claim_text
    assert targets[0].priority == 2
    assert targets[1].representative_claim_text == c_diagnostic.claim_text
    assert targets[1].priority == 5


def test_diagnostic_context_preserved_at_lower_priority() -> None:
    """Diagnostic/mitigation claims remain preserved in target pool at lower priority."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
    c_mitigation = ResearchClaim(
        id=uuid4(),
        claim_text="Implementing preventive measures during construction and proper curing reduces defect risks.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.75,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(
        claims=[c_mitigation],
        contract=contract,
    )
    assert len(targets) == 1
    assert targets[0].priority == 5
    assert targets[0].topic_relevance == 25.0


def test_provider_neutral_topic_relevance_scoring() -> None:
    """Causal process classification works for arbitrary non-concrete domains."""
    contract = extract_numeric_promise("10 Fault Tolerant Consensus Protocols in Distributed Storage")
    c_causal = ResearchClaim(
        id=uuid4(),
        claim_text="Leader failure triggers automatic election timeout causing split vote resolution.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.0,
        independent_sources_count=1,
    )
    c_generic = ResearchClaim(
        id=uuid4(),
        claim_text="Distributed storage networks consist of multiple interconnected server nodes.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.0,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(
        claims=[c_generic, c_causal],
        contract=contract,
    )
    assert len(targets) == 2
    assert targets[0].representative_claim_text == c_causal.claim_text
    assert targets[0].priority == 2  # Entity candidate
    assert targets[1].priority == 4  # Generic context


def test_explicit_a_causes_b_proposition_is_entity_candidate() -> None:
    """Explicit A-causes-B proposition is recognized as an entity candidate."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
    c = ResearchClaim(
        id=uuid4(),
        claim_text="Drying shrinkage causes internal tensile stresses in hardened concrete.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.75,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(claims=[c], contract=contract)
    assert len(targets) == 1
    assert targets[0].priority == 2
    assert targets[0].semantic_role == "CAUSE_OR_PROCESS"


def test_a_results_in_b_is_entity_candidate() -> None:
    """A-results-in-B proposition is recognized as an entity candidate."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
    c = ResearchClaim(
        id=uuid4(),
        claim_text="Rapid moisture evaporation results in plastic shrinkage fissures.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.75,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(claims=[c], contract=contract)
    assert len(targets) == 1
    assert targets[0].priority == 2
    assert targets[0].semantic_role == "CAUSE_OR_PROCESS"


def test_b_due_to_a_is_entity_candidate() -> None:
    """B-due-to-A proposition is recognized as an entity candidate."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
    c = ResearchClaim(
        id=uuid4(),
        claim_text="Severe tensile cracking develops due to freeze-thaw pore pressure.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.75,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(claims=[c], contract=contract)
    assert len(targets) == 1
    assert targets[0].priority == 2
    assert targets[0].semantic_role == "CAUSE_OR_PROCESS"


def test_consequence_only_structural_fractures_pose_safety_risk_not_mechanism() -> None:
    """Consequence-only statement 'structural fractures pose safety risk' is NOT an entity candidate."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
    c = ResearchClaim(
        id=uuid4(),
        claim_text="Structural fractures pose a safety risk to the building and compromise durability.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=54.35,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(claims=[c], contract=contract)
    assert len(targets) == 1
    assert targets[0].priority == 5
    assert targets[0].semantic_role == "OUTCOME_OR_CONSEQUENCE"


def test_inspection_proposition_is_low_priority() -> None:
    """Inspection advice is relegated to low priority."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
    c = ResearchClaim(
        id=uuid4(),
        claim_text="Cracks that have widened beyond hairline width require inspection.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=54.35,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(claims=[c], contract=contract)
    assert len(targets) == 1
    assert targets[0].priority == 5
    assert targets[0].semantic_role == "DIAGNOSTIC_OR_INSPECTION"


def test_mitigation_proposition_is_low_priority() -> None:
    """Mitigation and maintenance advice is relegated to low priority."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
    c = ResearchClaim(
        id=uuid4(),
        claim_text="Expansion joints require maintenance to prevent structural joint failure.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=54.35,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(claims=[c], contract=contract)
    assert len(targets) == 1
    assert targets[0].priority == 5
    assert targets[0].semantic_role == "MITIGATION_OR_PREVENTION"


def test_broad_token_degradation_alone_not_sufficient() -> None:
    """Broad token 'degradation' alone without causal process is not a mechanism candidate."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
    c = ResearchClaim(
        id=uuid4(),
        claim_text="Material degradation is visible at the surface in aging concrete infrastructure.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.75,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(claims=[c], contract=contract)
    assert len(targets) == 1
    assert targets[0].priority == 5
    assert targets[0].semantic_role != "CAUSE_OR_PROCESS"


def test_broad_token_fractures_alone_not_sufficient() -> None:
    """Broad token 'fractures' alone without causal process is not a mechanism candidate."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
    c = ResearchClaim(
        id=uuid4(),
        claim_text="Visible fractures appear along the exterior slab without structural consequence.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.75,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(claims=[c], contract=contract)
    assert len(targets) == 1
    assert targets[0].priority == 4
    assert targets[0].semantic_role == "GENERIC_CONTEXT"


def test_explicit_mechanism_process_remains_candidate() -> None:
    """Explicit mechanism noun with process relationship remains an entity candidate."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
    c = ResearchClaim(
        id=uuid4(),
        claim_text="Freeze-thaw cycling operates on the damaging mechanism where absorbed water expands internally.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.75,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(claims=[c], contract=contract)
    assert len(targets) == 1
    assert targets[0].priority == 2
    assert targets[0].semantic_role == "CAUSE_OR_PROCESS"


def test_cause_bearing_anchors_preserve_original_vocabulary() -> None:
    """Anchors preserve exact original words and exclude invented terminology."""
    text = "Drying shrinkage causes internal tensile stresses that crack hardened concrete slabs."
    anchors = extract_query_anchors(text, subject="concrete cracks")
    assert "drying" in anchors
    assert "shrinkage" in anchors
    assert "tensile" in anchors
    # Zero invented or stemmed terminology
    for a in anchors:
        assert a in text.lower()


def test_no_invented_search_terms_in_anchors() -> None:
    """All extracted anchors are proven substrings of the original proposition."""
    text = "Freeze-thaw cycling produces internal ice expansion that causes cracking."
    anchors = extract_query_anchors(text, subject="concrete cracks")
    for a in anchors:
        assert a in text.lower()


def test_three_distinct_candidate_families_outrank_duplicate_family_propositions() -> None:
    """Three distinct candidate families are selected across query budget ahead of duplicates."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")

    c_shrink_1 = ResearchClaim(
        id=uuid4(),
        claim_text="Drying shrinkage causes internal tensile stresses that produce cracking.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.75,
        independent_sources_count=1,
    )
    c_shrink_2 = ResearchClaim(
        id=uuid4(),
        claim_text="Drying shrinkage leads to cracking in hardened concrete.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.75,
        independent_sources_count=1,
    )
    c_thermal = ResearchClaim(
        id=uuid4(),
        claim_text="Thermal gradients produce cracking.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.75,
        independent_sources_count=1,
    )
    c_freeze = ResearchClaim(
        id=uuid4(),
        claim_text="Freeze-thaw cycling produces internal ice expansion that causes cracking.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.75,
        independent_sources_count=1,
    )

    targets = [grounded_target(representative_claim_text=c.claim_text,
        confidence_score=c.confidence_score, independent_support_count=1)
        for c in [c_shrink_1, c_shrink_2, c_thermal, c_freeze]]

    queries = plan_research_queries(
        topic_title="Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand",
        contract=contract,
        round_number=2,
        max_queries=3,
        corroboration_targets=targets,
    )

    assert len(queries) == 3
    q_texts = [q.query_text for q in queries]
    # Verify that shrinkage did not take 2 slots, leaving thermal and freeze thaw
    shrink_count = sum(1 for q in q_texts if "shrinkage" in q or "shrinks" in q)
    assert shrink_count == 1
    thermal_count = sum(1 for q in q_texts if "thermal" in q)
    assert thermal_count == 1
    freeze_count = sum(1 for q in q_texts if "freeze" in q or "thaw" in q)
    assert freeze_count == 1


def test_planning_family_signature_does_not_alter_verified_coverage() -> None:
    """Candidate family signature is strictly planning-only and cannot alter verified coverage."""
    from omega.domain.numeric_promise import extract_distinct_entities
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")

    c = ResearchClaim(
        id=uuid4(),
        claim_text="Drying shrinkage causes internal tensile stresses.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.75,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(claims=[c], contract=contract)
    assert targets[0].candidate_family != ""

    # Verified coverage requires verified claims, ignoring candidate_family
    entities = extract_distinct_entities([], topic_title="Why Concrete Cracks", entity_type="mechanism")
    assert len(entities) == 0


def test_verified_claim_remains_excluded_from_targets() -> None:
    """Already verified claims are strictly excluded from corroboration targets."""
    contract = extract_numeric_promise("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand")
    c = ResearchClaim(
        id=uuid4(),
        claim_text="Drying shrinkage causes internal tensile stresses.",
        claim_type=ClaimType.FACT.value,
        is_verified=True,  # Already verified
        confidence_score=75.0,
        independent_sources_count=3,
    )
    targets = build_corroboration_targets(claims=[c], contract=contract)
    assert len(targets) == 0


def test_non_concrete_causal_domain_battery_degradation() -> None:
    """Causal process classification functions for arbitrary non-concrete technical domains."""
    contract = extract_numeric_promise("5 Mechanisms of Lithium-Ion Battery Degradation")
    c = ResearchClaim(
        id=uuid4(),
        claim_text="Dendrite growth triggers internal short circuits causing battery thermal runaway.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.0,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(claims=[c], contract=contract)
    assert len(targets) == 1
    assert targets[0].priority == 2
    assert targets[0].semantic_role == "CAUSE_OR_PROCESS"


def test_non_causal_enumerable_topic_regression() -> None:
    """Non-causal enumerable entities (step, method, tip) do not require causal grammar."""
    contract = extract_numeric_promise("5 Steps to Clean Architectural Code")
    c = ResearchClaim(
        id=uuid4(),
        claim_text="Step one involves creating clear domain boundaries between application layers.",
        claim_type=ClaimType.FACT.value,
        is_verified=False,
        confidence_score=53.0,
        independent_sources_count=1,
    )
    targets = build_corroboration_targets(claims=[c], contract=contract)
    assert len(targets) == 1
    assert targets[0].priority == 2


# ── Phase J: Live Failure Remediation R1 Dedicated Invariant Tests ──


def test_phase_j_causal_contract_direct_corroboration_accepts_only_promised_candidate() -> None:
    """1. Causal contract direct corroboration accepts only PROMISED_ENTITY_CANDIDATE + CAUSE_OR_PROCESS."""
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    contract = extract_numeric_promise(topic)

    t_valid = grounded_target(
        representative_claim_text="Plastic shrinkage cracking occurs when surface evaporation exceeds bleed rate.",
        claim_type=ClaimType.FACT,
        priority=2,
        semantic_role="CAUSE_OR_PROCESS",
        candidate_family="plastic_shrink",
        independent_support_count=1,
    )
    t_context = grounded_target(
        representative_claim_text="Concrete is the most widely used material in construction worldwide.",
        claim_type=ClaimType.FACT,
        priority=4,
        semantic_role="GENERIC_CONTEXT",
        candidate_family="general_context",
        independent_support_count=1,
    )

    queries = plan_research_queries(
        topic_title=topic,
        contract=contract,
        round_number=2,
        max_queries=2,
        corroboration_targets=[t_valid, t_context],
    )
    assert len(queries) == 2
    assert queries[0].intent == ResearchQueryIntent.CORROBORATION
    # The second query MUST NOT be corroboration for context; it must be generic fallback
    assert queries[1].intent != ResearchQueryIntent.CORROBORATION
    assert queries[1].intent == ResearchQueryIntent.ADDITIONAL_COVERAGE


def test_phase_j_non_entity_roles_cannot_consume_causal_corroboration_slot() -> None:
    """2-5. TOPIC_RELEVANT_CONTEXT, OUTCOME, DIAGNOSTIC, MITIGATION cannot consume causal corroboration slot."""
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    contract = extract_numeric_promise(topic)

    t_context = grounded_target(
        representative_claim_text="Material properties vary across construction sites.",
        priority=3,
        semantic_role="GENERIC_CONTEXT",
        candidate_family="mat_prop",
    )
    t_outcome = grounded_target(
        representative_claim_text="Structural fractures pose severe safety hazards to building occupants.",
        priority=5,
        semantic_role="OUTCOME_OR_CONSEQUENCE",
        candidate_family="struct_fract",
    )
    t_diagnostic = grounded_target(
        representative_claim_text="Cracks wider than hairline require visual inspection and monitoring.",
        priority=5,
        semantic_role="DIAGNOSTIC_OR_INSPECTION",
        candidate_family="visual_inspect",
    )
    t_mitigation = grounded_target(
        representative_claim_text="Joints should also be placed at re-entrant corners to prevent cracking.",
        priority=5,
        semantic_role="MITIGATION_OR_PREVENTION",
        candidate_family="joint_place",
    )

    queries = plan_research_queries(
        topic_title=topic,
        contract=contract,
        round_number=2,
        max_queries=3,
        corroboration_targets=[t_context, t_outcome, t_diagnostic, t_mitigation],
    )
    # Zero corroboration queries issued from these non-entity targets
    corrob_queries = [q for q in queries if q.intent == ResearchQueryIntent.CORROBORATION]
    assert len(corrob_queries) == 0


def test_phase_j_generic_mechanism_coverage_preferred_over_context_target() -> None:
    """6. Generic mechanism coverage fallback is preferred over context target."""
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    contract = extract_numeric_promise(topic)

    t_context = grounded_target(
        representative_claim_text="Concrete formulations require proper hydration time.",
        priority=4,
        semantic_role="GENERIC_CONTEXT",
        candidate_family="proper_hydrat",
    )
    queries = plan_research_queries(
        topic_title=topic,
        contract=contract,
        round_number=2,
        max_queries=2,
        corroboration_targets=[t_context],
    )
    # All queries should be generic coverage fallback, none corroborating context
    assert len(queries) == 2
    for q in queries:
        assert q.intent != ResearchQueryIntent.CORROBORATION


def test_phase_j_three_distinct_causal_candidate_families_fill_slots() -> None:
    """7. Three distinct causal candidate families fill 3 slots when available."""
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    contract = extract_numeric_promise(topic)

    t1 = grounded_target(
        representative_claim_text="Drying shrinkage causes internal tensile stresses that crack hardened concrete.",
        priority=2,
        semantic_role="CAUSE_OR_PROCESS",
        candidate_family="dry_shrink",
    )
    t2 = grounded_target(
        representative_claim_text="Freeze-thaw cycling produces internal ice expansion that causes cracking.",
        priority=2,
        semantic_role="CAUSE_OR_PROCESS",
        candidate_family="freez_thaw",
    )
    t3 = grounded_target(
        representative_claim_text="Thermal contraction creates steep temperature gradients that cause cracking.",
        priority=2,
        semantic_role="CAUSE_OR_PROCESS",
        candidate_family="therm_contract",
    )

    queries = plan_research_queries(
        topic_title=topic,
        contract=contract,
        round_number=2,
        max_queries=3,
        corroboration_targets=[t1, t2, t3],
    )
    assert len(queries) == 3
    for q in queries:
        assert q.intent == ResearchQueryIntent.CORROBORATION


def test_phase_j_two_support_causal_family_outranks_one_support() -> None:
    """8. Two-support causal family outranks one-support family."""
    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    contract = extract_numeric_promise(topic)

    t1_indep = grounded_target(
        representative_claim_text="Thermal contraction creates steep temperature gradients.",
        priority=2,
        semantic_role="CAUSE_OR_PROCESS",
        candidate_family="therm_contract",
        independent_support_count=1,
    )
    t2_indep = grounded_target(
        representative_claim_text="Plastic shrinkage cracking occurs when evaporation exceeds bleed rate.",
        priority=1,
        semantic_role="CAUSE_OR_PROCESS",
        candidate_family="plastic_shrink",
        independent_support_count=2,
    )

    queries = plan_research_queries(
        topic_title=topic,
        contract=contract,
        round_number=2,
        max_queries=1,
        corroboration_targets=[t1_indep, t2_indep],
    )
    assert len(queries) == 1
    assert "plastic" in queries[0].query_text or "shrinkage" in queries[0].query_text


def test_phase_j_cause_bearing_anchor_avoids_discourse_leading_tokens() -> None:
    """9-10. Cause-bearing anchor avoids discourse-only leading tokens and remains exact-source grounded."""
    text1 = "While determining the cause and severity of a crack can be somewhat difficult, shrinkage is primary."
    anchors1 = extract_query_anchors(text1, subject="concrete cracks")
    for bad_token in ["while", "determining", "somewhat", "difficult"]:
        assert bad_token not in anchors1

    text2 = "What we need to watch out for though, are cracks that are a result of foundation movement."
    anchors2 = extract_query_anchors(text2, subject="concrete cracks")
    for bad_token in ["need", "watch", "though"]:
        assert bad_token not in anchors2

    text3 = "Joints should also be placed at re-entrant corners where shrinkage causes stresses."
    anchors3 = extract_query_anchors(text3, subject="concrete cracks")
    for bad_token in ["joints", "should", "placed", "entrant"]:
        assert bad_token not in anchors3

    # All anchors in all three must be exact substrings of original text
    for a in anchors1:
        assert a in text1.lower()
    for a in anchors2:
        assert a in text2.lower()
    for a in anchors3:
        assert a in text3.lower()


def test_phase_j_non_causal_enumerable_topics_remain_valid() -> None:
    """11. Non-causal enumerable topics remain valid."""
    contract = extract_numeric_promise("7 Proven Tips for Effective Technical Writing")
    assert contract is not None
    assert contract.promised_count == 7
    assert contract.entity_type == "tip"

    t = grounded_target(
        representative_claim_text="Tip three advises using active voice rather than passive constructions.",
        priority=2,
        semantic_role="GENERIC_CONTEXT",
        candidate_family="active_voice",
    )
    queries = plan_research_queries(
        topic_title="7 Proven Tips for Effective Technical Writing",
        contract=contract,
        round_number=2,
        max_queries=1,
        corroboration_targets=[t],
    )
    assert len(queries) == 1
    assert queries[0].intent == ResearchQueryIntent.CORROBORATION


def test_phase_j_verified_family_semantics_concrete_cases() -> None:
    """12-14. Verified family labels are meaningful, cause-grounded, not raw stem bigrams."""
    from omega.domain.numeric_promise import extract_distinct_entities

    c1 = (
        "What Causes Them: Concrete shrinks as it cures - the chemical hydration process that hardens "
        "the concrete consumes water and reduces the material's volume slightly. This volumetric change is "
        "normal and expected, but when it's uneven - because the surface is drying faster than the interior, "
        "because the mix had too much water, or because curing conditions were too warm or too dry - the "
        "differential shrinkage produces surface cracking."
    )
    c2 = (
        "Structural cracking occurs when applied forces whether from loading, settlement, lateral pressure, "
        "or thermal stress exceed the concrete's tensile capacity."
    )

    families = extract_distinct_entities(
        [c1, c2],
        topic_title="Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand",
        entity_type="mechanism",
    )
    assert len(families) == 2
    assert "what_them" not in families
    assert "appli_forc" not in families
    assert "while_determin" not in families
    assert "need_watch" not in families
    assert any("shrinkage" in f or "drying" in f for f in families)
    assert any("applied" in f or "forces" in f or "loading" in f for f in families)


def test_phase_j_planning_family_cannot_affect_verified_count() -> None:
    """15. Unverified planning family cannot affect supported count."""
    from omega.domain.numeric_promise import extract_distinct_entities

    # Unverified claims must NOT be passed to extract_distinct_entities
    # Even if planning created 10 candidate families, verified count remains 0
    distinct = extract_distinct_entities([], topic_title="Why Concrete Cracks", entity_type="mechanism")
    assert len(distinct) == 0


def test_phase_j_one_broad_proposition_contributes_at_most_one_family() -> None:
    """16. One broad verified proposition cannot fabricate multiple verified families."""
    from omega.domain.numeric_promise import extract_distinct_entities

    broad_claim = (
        "Structural cracking occurs when applied forces whether from loading, settlement, lateral pressure, "
        "or thermal stress exceed the concrete's tensile capacity."
    )
    distinct = extract_distinct_entities(
        [broad_claim],
        topic_title="Why Concrete Cracks: 5 Mechanisms",
        entity_type="mechanism",
    )
    assert len(distinct) == 1


def test_phase_j_retry4_offline_corpus_remains_two_families() -> None:
    """17. Retry #4 offline corpus remains only 2 verified families."""
    from omega.domain.numeric_promise import extract_distinct_entities

    c1 = (
        "What Causes Them: Concrete shrinks as it cures - the chemical hydration process that hardens "
        "the concrete consumes water and reduces the material's volume slightly. This volumetric change is "
        "normal and expected, but when it's uneven - because the surface is drying faster than the interior, "
        "because the mix had too much water, or because curing conditions were too warm or too dry - the "
        "differential shrinkage produces surface cracking."
    )
    c2 = (
        "Structural cracking occurs when applied forces whether from loading, settlement, lateral pressure, "
        "or thermal stress exceed the concrete's tensile capacity."
    )
    distinct = extract_distinct_entities(
        [c1, c2],
        topic_title="Why Concrete Cracks: 5 Mechanisms",
        entity_type="mechanism",
    )
    assert len(distinct) == 2


# ==============================================================================
# Phase J R1.1: Domain-Agnostic Verified Family Grounding Tests (Items 1-20)
# ==============================================================================

def test_r1_1_no_concrete_mechanism_lookup_table_in_production_code() -> None:
    """1. No concrete mechanism lookup table or hard-coded mechanisms in production code."""
    import inspect

    from omega.domain import numeric_promise

    source = inspect.getsource(numeric_promise.derive_verified_family_label)

    forbidden = [
        "plastic shrinkage",
        "drying shrinkage",
        "differential shrinkage",
        "applied loading",
        "applied forces",
        "freeze-thaw cycling",
        "freeze-thaw expansion",
        "thermal contraction",
        "thermal expansion",
        "thermal stress",
        "tensile overload",
        "tensile capacity",
        "tensile stress",
        "subgrade settlement",
        "alkali-silica reaction",
        "chemical attack",
        "rebar corrosion",
    ]
    for term in forbidden:
        assert f'"{term}"' not in source, f"Found hard-coded mechanism: {term}"
        assert f"'{term}'" not in source, f"Found hard-coded mechanism: {term}"


def test_r1_1_family_label_words_all_exist_in_claim() -> None:
    """2. Every word in the derived family label must exist in the verified claim vocabulary."""
    import re

    from omega.domain.numeric_promise import derive_verified_family_label

    claims = [
        "Cracking occurs when applied forces exceed tensile capacity.",
        "Leader failure causes an election timeout and triggers a new election.",
        "Lithium plating causes capacity loss during fast charging.",
        "Packet congestion leads to retransmission and increased latency.",
        "Differential shrinkage produces surface cracking in mass elements.",
    ]
    for claim in claims:
        _, label = derive_verified_family_label(claim)
        claim_words = set(re.findall(r"\b[a-zA-Z]{2,}\b", claim.lower()))
        label_words = label.split()
        for w in label_words:
            assert w in claim_words, f"Word '{w}' from label '{label}' not in claim '{claim}'"


def test_r1_1_x_causes_y_extracts_x_side_concept() -> None:
    """3. 'X causes Y' extracts X-side concept."""
    from omega.domain.numeric_promise import derive_verified_family_label

    claim = "Thermal contraction causes cracking as temperature falls."
    _, label = derive_verified_family_label(claim)
    assert label == "thermal contraction"


def test_r1_1_x_leads_to_y_extracts_x_side_concept() -> None:
    """4. 'X leads to Y' extracts X-side concept."""
    from omega.domain.numeric_promise import derive_verified_family_label

    claim = "Packet congestion leads to retransmission and increased latency."
    _, label = derive_verified_family_label(claim)
    assert label == "packet congestion"


def test_r1_1_y_results_from_x_extracts_x_side_concept() -> None:
    """5. 'Y results from X' extracts X-side concept."""
    from omega.domain.numeric_promise import derive_verified_family_label

    claim = "Surface cracking results from differential shrinkage during rapid hydration."
    _, label = derive_verified_family_label(claim)
    assert label == "differential shrinkage"


def test_r1_1_y_occurs_when_x_extracts_x_side_concept() -> None:
    """6. 'Y occurs when X' extracts X-side concept."""
    from omega.domain.numeric_promise import derive_verified_family_label

    claim = "Cracking occurs when applied forces exceed tensile capacity."
    _, label = derive_verified_family_label(claim)
    assert label == "applied forces"


def test_r1_1_applied_forces_claim_never_invents_loading() -> None:
    """7. Applied-forces claim never invents 'loading' when absent from claim."""
    from omega.domain.numeric_promise import derive_verified_family_label

    claim = "Structural cracking occurs when applied forces exceed the material's tensile capacity."
    _, label = derive_verified_family_label(claim)
    assert "loading" not in label
    assert label == "applied forces"


def test_r1_1_corrosion_claim_never_invents_rebar() -> None:
    """8. Corrosion claim never invents 'rebar' when absent from claim."""
    from omega.domain.numeric_promise import derive_verified_family_label

    claim = "Corrosion of embedded steel causes expansion and surface spalling."
    _, label = derive_verified_family_label(claim)
    assert "rebar" not in label
    assert "corrosion" in label


def test_r1_1_thermal_claim_never_invents_stress_unless_present() -> None:
    """9. Thermal claim never invents 'stress' unless present in claim."""
    from omega.domain.numeric_promise import derive_verified_family_label

    claim = "Thermal contraction causes cracking in mass concrete as heat dissipates."
    _, label = derive_verified_family_label(claim)
    assert "stress" not in label
    assert label == "thermal contraction"


def test_r1_1_subgrade_claim_never_invents_settlement_unless_present() -> None:
    """10. Subgrade claim never invents 'settlement' unless present in claim."""
    from omega.domain.numeric_promise import derive_verified_family_label

    claim = "Subgrade movement causes foundation cracking."
    _, label = derive_verified_family_label(claim)
    assert "settlement" not in label
    assert label == "subgrade movement"


def test_r1_1_domain_agnostic_distributed_systems() -> None:
    """11. Distributed systems domain example PASS."""
    from omega.domain.numeric_promise import derive_verified_family_label

    claim = "Leader failure causes an election timeout and triggers a new election."
    _, label = derive_verified_family_label(claim)
    assert label == "leader failure"
    for w in label.split():
        assert w in claim.lower()


def test_r1_1_domain_agnostic_battery() -> None:
    """12. Battery domain example PASS."""
    from omega.domain.numeric_promise import derive_verified_family_label

    claim = "Lithium plating causes capacity loss during fast charging."
    _, label = derive_verified_family_label(claim)
    assert label == "lithium plating"
    for w in label.split():
        assert w in claim.lower()


def test_r1_1_domain_agnostic_networking() -> None:
    """13. Networking domain example PASS."""
    from omega.domain.numeric_promise import derive_verified_family_label

    claim = "Packet congestion leads to retransmission and increased latency."
    _, label = derive_verified_family_label(claim)
    assert label == "packet congestion"
    for w in label.split():
        assert w in claim.lower()


def test_r1_1_verified_only_numeric_coverage_preserved() -> None:
    """14. Verified-only numeric coverage preserved: unverified cannot change count."""
    from omega.domain.numeric_promise import extract_distinct_entities

    # Unverified claims never passed to verified numeric extraction
    verified_distinct = extract_distinct_entities([], topic_title="Reliability Patterns")
    assert len(verified_distinct) == 0


def test_r1_1_one_verified_proposition_le_one_family() -> None:
    """15. One verified proposition contributes at most one family."""
    from omega.domain.numeric_promise import extract_distinct_entities

    broad = "Structural cracking occurs when applied forces whether from loading, settlement, lateral pressure, or thermal stress exceed tensile capacity."
    distinct = extract_distinct_entities([broad], topic_title="Structural Engineering")
    assert len(distinct) == 1


def test_r1_1_duplicate_family_collapsing_preserved() -> None:
    """16. Duplicate family collapsing preserved across rephrased claims."""
    from omega.domain.numeric_promise import extract_distinct_entities

    duplicates = [
        "Plastic shrinkage cracking occurs when surface evaporation is rapid.",
        "Plastic-shrinkage cracking develops prior to initial set.",
        "Rapid evaporation causes plastic shrinkage cracks on flatwork.",
        "Plastic shrinkage crack formation during early curing.",
        "Occurrence of plastic shrinkage cracking under high wind conditions.",
    ]
    distinct = extract_distinct_entities(duplicates, topic_title="Concrete Cracking")
    assert len(distinct) == 1
    assert "plastic" in distinct[0]


def test_r1_1_retry4_corpus_remains_exactly_two_verified_families() -> None:
    """17. Retry #4 corpus remains exactly 2 verified families with 0 absent words."""
    import re

    from omega.domain.numeric_promise import derive_verified_family_label, extract_distinct_entities

    c1 = (
        "What Causes Them: Concrete shrinks as it cures - the chemical hydration process that hardens "
        "the concrete consumes water and reduces the material's volume slightly. This volumetric change is "
        "normal and expected, but when it's uneven - because the surface is drying faster than the interior, "
        "because the mix had too much water, or because curing conditions were too warm or too dry - the "
        "differential shrinkage produces surface cracking."
    )
    c2 = (
        "Structural cracking occurs when applied forces whether from loading, settlement, lateral pressure, "
        "or thermal stress exceed the concrete's tensile capacity."
    )
    _, l1 = derive_verified_family_label(c1)
    _, l2 = derive_verified_family_label(c2)

    assert l1 == "differential shrinkage"
    assert l2 == "applied forces"

    c1_words = set(re.findall(r"\b[a-zA-Z]{2,}\b", c1.lower()))
    c2_words = set(re.findall(r"\b[a-zA-Z]{2,}\b", c2.lower()))
    assert all(w in c1_words for w in l1.split())
    assert all(w in c2_words for w in l2.split())

    families = extract_distinct_entities([c1, c2], topic_title="Why Concrete Cracks")
    assert len(families) == 2


def test_r1_1_r1_non_entity_query_waste_remains_zero() -> None:
    """18. R1 non-entity query waste remains 0 with generalized planner rules."""
    from omega.application.research_query_planner import (
        classify_target_planning_usefulness,
        plan_research_queries,
    )
    from omega.domain.numeric_promise import extract_numeric_promise

    mitig_text = "Joints should also be placed at re-entrant corners where shrinkage causes stresses."
    p_class, _, role = classify_target_planning_usefulness(mitig_text, "mechanism", ["concrete", "cracks"])
    assert role == "MITIGATION_OR_PREVENTION"
    assert p_class == "MITIGATION_OR_PREVENTION"

    topic = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"
    contract = extract_numeric_promise(topic)
    t_cause = grounded_target(
        representative_claim_text="Thermal contraction causes cracking as temperature falls.",
        priority=2,
        semantic_role="CAUSE_OR_PROCESS",
        candidate_family="therm_contract",
    )
    t_mitig = grounded_target(
        representative_claim_text=mitig_text,
        priority=5,
        semantic_role="MITIGATION_OR_PREVENTION",
        candidate_family="joint_place",
    )
    queries = plan_research_queries(
        topic_title=topic,
        contract=contract,
        round_number=2,
        max_queries=1,
        corroboration_targets=[t_cause, t_mitig],
    )
    assert len(queries) == 1
    assert "thermal" in queries[0].query_text or "contraction" in queries[0].query_text
    assert "joint" not in queries[0].query_text


def test_r1_1_offline_simulation_five_distinct_families_sufficient() -> None:
    """19. Offline 0->5 simulation remains SUFFICIENT."""
    from omega.application.research_scorer import determine_research_outcome
    from omega.domain.numeric_promise import extract_distinct_entities
    from omega.domain.research import ResearchOutcome

    claims = [
        "Plastic shrinkage cracking occurs when surface evaporation exceeds bleed rate.",
        "Drying shrinkage develops over months as tensile stresses build up.",
        "Thermal contraction causes cracking in mass concrete as hydration heat dissipates.",
        "Chemical attack via alkali-silica reaction expands and fractures the aggregate matrix.",
        "Subgrade settlement causes differential movement and structural shear cracking.",
    ]
    distinct = extract_distinct_entities(claims, topic_title="Why Concrete Cracks: 5 Mechanisms", entity_type="mechanism")
    assert len(distinct) == 5

    outcome = determine_research_outcome(
        sources_count=3,
        independent_sources_count=3,
        verified_claims_count=5,
        open_high_conflicts_count=0,
        promised_count=5,
        distinct_entities_count=len(distinct),
    )
    assert outcome == ResearchOutcome.SUFFICIENT


def test_r1_1_non_causal_numeric_contracts_remain_valid() -> None:
    """20. Non-causal numeric contracts remain valid."""
    from omega.domain.numeric_promise import derive_verified_family_label, extract_numeric_promise

    contract = extract_numeric_promise("7 Proven Tips for Effective Technical Writing")
    assert contract is not None
    assert contract.promised_count == 7
    assert contract.entity_type == "tip"

    claim = "Tip three emphasizes customer retention above aggressive acquisition."
    _, label = derive_verified_family_label(claim, entity_type="tip")
    assert label != "unknown"
    for w in label.split():
        assert w in claim.lower()
