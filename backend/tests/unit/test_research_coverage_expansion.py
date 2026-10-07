"""Unit tests for Automatic Research Coverage Expansion (P0.3 & P0.3a.1).

Verifies discovery provider contracts, deterministic topic-grounded query planning,
intent diversification, candidate deduplication, ResearchContentExtractor boundary,
discovery snippet cannot become evidence, 2 -> 5 verified coverage expansion,
duplicate-family non-progress fail-closed, budget exhaustion, provider failure resilience,
manual mode preservation, non-numeric topic support, stop-reason/brief consistency,
deterministic claim extractor V2, and runtime truth observability.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from omega.application import research_service
from omega.application.claim_extractor import extract_deterministic_claims_from_source
from omega.application.claim_reconciliation import (
    are_propositions_corroborating,
    reconcile_source_extractions_into_claims,
)
from omega.application.research_coverage_service import execute_coverage_driven_research
from omega.application.research_discovery import (
    DiscoveryProviderUnavailableError,
    InMemoryDiscoveryProvider,
    InMemoryResearchContentExtractor,
    NullDiscoveryProvider,
    filter_and_deduplicate_candidates,
)
from omega.application.research_query_planner import extract_core_subject, plan_research_queries
from omega.application.source_provider import ManualAuthorityProvider
from omega.domain.numeric_promise import (
    extract_numeric_promise,
)
from omega.domain.research import (
    ClaimType,
    DiscoveryCandidate,
    ExtractedResearchDocument,
    PrimarySourceStatus,
    ResearchCoverageStopReason,
    ResearchOutcome,
    ResearchQueryIntent,
    ResearchRequestStatus,
    ResearchSourceCreate,
    ResearchSourceType,
)
from omega.infrastructure.models import (
    Channel,
    ResearchBrief,
    ResearchClaim,
    ResearchConflict,
    ResearchRequest,
    ResearchSource,
    TopicCandidate,
)

CANONICAL_TOPIC = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"


class FakeResult:
    def __init__(self, items):
        self._items = items if isinstance(items, list) else ([items] if items is not None else [])

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def scalars(self):
        return self

    def all(self):
        return list(self._items)

    def one(self):
        if not self._items:
            raise ValueError("No rows found")
        return self._items[0]


class FakeAsyncSession:
    """Lightweight in-memory AsyncSession for fast, isolated unit tests."""

    def __init__(self, req: ResearchRequest, channel: Channel | None = None, topic: TopicCandidate | None = None):
        self.req = req
        self.channel = channel
        self.topic = topic
        self.sources: list[ResearchSource] = []
        self.claims: list[ResearchClaim] = []
        self.briefs: list[ResearchBrief] = []
        self.conflicts: list[Any] = []

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0]["entity"]
        if entity is ResearchRequest:
            return FakeResult([self.req])
        elif entity is Channel:
            return FakeResult([self.channel] if self.channel else [])
        elif entity is TopicCandidate:
            return FakeResult([self.topic] if self.topic else [])
        elif entity is ResearchSource:
            params = stmt.compile().params
            filtered = list(self.sources)
            for k, v in params.items():
                if "content_hash" in k:
                    filtered = [s for s in filtered if s.content_hash == v]
            return FakeResult(filtered)
        elif entity is ResearchClaim:
            return FakeResult(list(self.claims))
        elif entity is ResearchBrief:
            return FakeResult(list(self.briefs))
        elif entity is ResearchConflict:
            return FakeResult(list(self.conflicts))
        return FakeResult([])

    def add(self, obj):
        if isinstance(obj, ResearchSource):
            self.sources.append(obj)
            if not hasattr(self.req, "sources") or self.req.sources is None:
                self.req.sources = []
            if obj not in self.req.sources:
                self.req.sources.append(obj)
        elif isinstance(obj, ResearchClaim):
            self.claims.append(obj)
        elif isinstance(obj, ResearchBrief):
            self.briefs.append(obj)
        elif isinstance(obj, ResearchRequest):
            self.req = obj
        elif hasattr(obj, "__tablename__") and obj.__tablename__ == "research_conflicts":
            self.conflicts.append(obj)

    async def commit(self):
        pass

    async def flush(self):
        pass


def make_test_fixture(
    topic_title: str = CANONICAL_TOPIC,
    acquisition_mode: str = "AUTOMATIC_SEARCH",
    max_sources: int = 15,
):
    channel_id = uuid.uuid4()
    topic_id = uuid.uuid4()
    req_id = uuid.uuid4()

    contract = extract_numeric_promise(topic_title)

    channel = Channel(
        id=channel_id,
        name="Civil Engineering Insights",
        slug="civil-eng-insights",
        state="ACTIVE",
    )
    topic = TopicCandidate(
        id=topic_id,
        channel_id=channel_id,
        title=topic_title,
        status="SELECTED",
        keywords=["concrete", "cracks", "civil", "engineering"],
        metadata_={"numeric_contract": contract.to_dict()} if contract else {},
    )
    req = ResearchRequest(
        id=req_id,
        channel_id=channel_id,
        topic_candidate_id=topic_id,
        mode="INTERACTIVE",
        status=ResearchRequestStatus.PENDING.value,
        language="en",
        region="US",
        max_sources=max_sources,
        minimum_source_quality=50.0,
        minimum_claim_confidence=50.0,
        metadata_={
            "acquisition_mode": acquisition_mode,
            **({"numeric_contract": contract.to_dict()} if contract else {}),
        },
    )
    req.topic_candidate = topic
    req.sources = []

    session = FakeAsyncSession(req, channel, topic)
    return session, req, channel_id, topic_id


# ── 1. Query Planner: Core Subject Extraction ──


def test_core_subject_extraction():
    """Verify rhetorical packaging is stripped while canonical subject is preserved."""
    cases = [
        ("Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand", "concrete cracks"),
        ("How Bridges Collapse: 7 Structural Causes Explained", "bridges collapse"),
        ("7 Causes of Foundation Settlement in 2026", "foundation settlement"),
        ("Understanding Soil Liquefaction: 4 Triggers", "soil liquefaction"),
        ("The Physics of Resonance", "physics of resonance"),
    ]
    for raw_title, expected_sub in cases:
        subject = extract_core_subject(raw_title)
        assert expected_sub in subject or subject in expected_sub, (
            f"Subject extraction failed for '{raw_title}': got '{subject}'"
        )


# ── 2. Query Planner: Topic Grounding & Intent Diversification ──


def test_plan_research_queries_topic_grounded():
    """Verify generated research queries are grounded, diversified, and free of hard-coded mechanisms."""
    contract = extract_numeric_promise(CANONICAL_TOPIC)
    assert contract is not None

    # Round 1
    r1_queries = plan_research_queries(
        topic_title=CANONICAL_TOPIC,
        contract=contract,
        round_number=1,
        max_queries=3,
    )
    assert len(r1_queries) == 3
    intents = {q.intent for q in r1_queries}
    assert ResearchQueryIntent.OVERVIEW in intents
    assert ResearchQueryIntent.MECHANISMS in intents
    assert ResearchQueryIntent.TECHNICAL_REFERENCE in intents

    # Verify queries contain canonical subject and promised entity
    for q in r1_queries:
        assert "concrete" in q.query_text
        assert "mechanism" in q.query_text
        # Verify NO hard-coded mechanism answers exist in queries!
        assert "asr" not in q.query_text
        assert "thermal" not in q.query_text
        assert "shrinkage" not in q.query_text
        assert q.reason != ""
        assert q.round_number == 1

    # Round 2: Diversification
    r2_queries = plan_research_queries(
        topic_title=CANONICAL_TOPIC,
        contract=contract,
        round_number=2,
        already_supported_families=["plastic_shrinkage", "dry_shrinkage"],
        max_queries=3,
    )
    assert len(r2_queries) == 3
    r2_intents = {q.intent for q in r2_queries}
    assert ResearchQueryIntent.ADDITIONAL_COVERAGE in r2_intents
    assert ResearchQueryIntent.CAUSES in r2_intents
    assert ResearchQueryIntent.TYPES in r2_intents


def test_plan_research_queries_non_numeric():
    """Verify query generation works cleanly for topics without a numeric promise."""
    topic = "The Physics of Resonance in Modern Engineering"
    queries = plan_research_queries(topic_title=topic, contract=None, round_number=1)
    assert len(queries) == 3
    for q in queries:
        assert "resonance" in q.query_text
        assert q.reason != ""


def test_query_deduplication():
    """Verify identical or whitespace-variant queries are deduplicated."""
    queries = plan_research_queries(
        topic_title="Concrete Cracks",
        contract=None,
        max_queries=10,
    )
    texts = [q.query_text for q in queries]
    assert len(texts) == len(set(texts))


# ── 3. Candidate Filtering & URL Deduplication ──


def test_filter_and_deduplicate_candidates():
    """Verify candidate filtering removes invalid schemes, duplicates, and empty content."""
    cand1 = DiscoveryCandidate(
        canonical_url="https://engineering.org/mechanisms-1",
        title="Mechanism Guide 1",
        publisher="Civil Engineering Org",
        snippet="Plastic shrinkage happens when evaporation exceeds bleed rate.",
    )
    cand_dup = DiscoveryCandidate(
        canonical_url="https://engineering.org/mechanisms-1",
        title="Duplicate Guide",
        publisher="Civil Engineering Org",
        snippet="Plastic shrinkage occurs early in curing.",
    )
    cand_bad_scheme = DiscoveryCandidate(
        canonical_url="ftp://files.org/paper.pdf",
        title="FTP Document",
        publisher="FTP Host",
        snippet="Valid text but invalid scheme.",
    )
    cand_empty_excerpt = DiscoveryCandidate(
        canonical_url="https://engineering.org/empty",
        title="Empty Paper",
        publisher="Org",
        snippet="Too short",
    )
    cand2 = DiscoveryCandidate(
        canonical_url="https://engineering.org/mechanisms-2",
        title="Mechanism Guide 2",
        publisher="ACI Journal",
        snippet="Drying shrinkage develops over months of internal water loss.",
    )

    already_seen = {"https://engineering.org/already-seen"}
    accepted, reasons = filter_and_deduplicate_candidates(
        [cand1, cand_dup, cand_bad_scheme, cand_empty_excerpt, cand2],
        already_seen_urls=already_seen,
        max_accepted=5,
    )
    assert len(accepted) == 2
    assert accepted[0].canonical_url == "https://engineering.org/mechanisms-1"
    assert accepted[1].canonical_url == "https://engineering.org/mechanisms-2"
    assert any("DUPLICATE_URL" in r for r in reasons)
    assert any("UNSUPPORTED_SCHEME" in r for r in reasons)
    assert any("LOW_INFORMATION_SNIPPET" in r for r in reasons)


# ── 4. Discovery Provider Contracts ──


@pytest.mark.asyncio
async def test_null_discovery_provider():
    provider = NullDiscoveryProvider()
    results = await provider.search("any query")
    assert results == []


@pytest.mark.asyncio
async def test_in_memory_discovery_provider_failure():
    provider = InMemoryDiscoveryProvider(fail=True, failure_message="Upstream API 503")
    with pytest.raises(DiscoveryProviderUnavailableError) as exc_info:
        await provider.search("concrete mechanisms")
    assert "Upstream API 503" in str(exc_info.value)


# ── 5. Mandatory Test: 2 -> 5 Coverage Expansion (Phase R) ──


@pytest.mark.asyncio
async def test_coverage_expansion_two_to_five_mechanisms():
    """Synthetic Scenario (Phase R):
    Starts with 2 initial verified mechanisms in DB.
    Discovery provider returns sources containing 3 more distinct mechanisms.
    Orchestrator discovers sources, extracts documents, ingests them, verifies 5/5 mechanisms,
    and produces SUFFICIENT ResearchBrief outcome.
    """
    session, req, channel_id, topic_id = make_test_fixture()

    authority = ManualAuthorityProvider({
        "aci journal": 90.0,
        "structural engineering review": 90.0,
        "materials science press": 85.0,
        "concrete technology institute": 85.0,
        "civil engineering org": 85.0,
    })

    # Seed 2 initial mechanisms (Plastic Shrinkage and Drying Shrinkage)
    src1 = ResearchSourceCreate(
        source_type=ResearchSourceType.MANUAL,
        title="Early Age Plastic Shrinkage in Slabs",
        publisher="ACI Journal",
        url="https://aci.org/plastic-shrinkage",
        content_excerpt="Plastic shrinkage cracks occur when surface evaporation exceeds bleeding before concrete sets.",
        primary_source_status=PrimarySourceStatus.CONFIRMED,
    )
    src2 = ResearchSourceCreate(
        source_type=ResearchSourceType.MANUAL,
        title="Long-Term Drying Shrinkage of Hardened Concrete",
        publisher="Structural Engineering Review",
        url="https://structeng.org/drying-shrinkage",
        content_excerpt="Drying shrinkage takes place as hardened concrete loses internal moisture over service life.",
        primary_source_status=PrimarySourceStatus.CONFIRMED,
    )
    await research_service.add_source(session, req.id, src1, authority_provider=authority)
    await research_service.add_source(session, req.id, src2, authority_provider=authority)

    # Configure discovery provider with 3 additional distinct mechanism sources
    expansion_candidates = [
        DiscoveryCandidate(
            canonical_url="https://materialsscience.org/thermal-cracking",
            title="Thermal Contraction Cracking in Mass Pours",
            publisher="Materials Science Press",
            snippet="Thermal contraction cracking snippet for mass concrete.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        DiscoveryCandidate(
            canonical_url="https://concretetech.org/asr-damage",
            title="Alkali-Silica Reaction Internal Deterioration",
            publisher="Concrete Technology Institute",
            snippet="Alkali-silica reaction (ASR) snippet for concrete map cracking.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        DiscoveryCandidate(
            canonical_url="https://civileng.org/structural-overload",
            title="Structural Overloading and Shear Failure",
            publisher="Civil Engineering Org",
            snippet="Structural overloading snippet for flexural tensile cracks.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
    ]

    expansion_docs = [
        ExtractedResearchDocument(
            canonical_url="https://materialsscience.org/thermal-cracking",
            title="Thermal Contraction Cracking in Mass Pours",
            publisher="Materials Science Press",
            extracted_content="Thermal contraction cracking happens during rapid cooling of massive concrete elements.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        ExtractedResearchDocument(
            canonical_url="https://concretetech.org/asr-damage",
            title="Alkali-Silica Reaction Internal Deterioration",
            publisher="Concrete Technology Institute",
            extracted_content="Alkali-silica reaction (ASR) creates internal expansive gel causing map cracking.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        ExtractedResearchDocument(
            canonical_url="https://civileng.org/structural-overload",
            title="Structural Overloading and Shear Failure",
            publisher="Civil Engineering Org",
            extracted_content="Structural overloading produces flexural tensile cracks when design load capacity is exceeded.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
    ]

    provider = InMemoryDiscoveryProvider(seeded_candidates=expansion_candidates)
    extractor = InMemoryResearchContentExtractor(seeded_documents=expansion_docs)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
        max_rounds=3,
    )

    assert result["stop_reason"] == ResearchCoverageStopReason.COVERAGE_FULFILLED.value
    assert result["initial_supported_count"] == 2
    assert result["final_supported_count"] >= 5
    assert result["brief"] is not None
    assert result["brief"].outcome == ResearchOutcome.SUFFICIENT
    assert result["brief"].metadata.get("numeric_promise_fulfilled") is True
    assert len(result["brief"].metadata.get("distinct_entities_supported", [])) >= 5


# ── 6. Mandatory Test: Duplicate-Family Non-Progress (Phase S) ──


@pytest.mark.asyncio
async def test_duplicate_family_non_progress_fails_closed():
    """Synthetic Scenario (Phase S):
    Initial: 2 mechanisms (Plastic Shrinkage, Drying Shrinkage).
    Discovery provider repeatedly returns sources describing ONLY those same 2 families.
    Verified claim count increases, but distinct coverage remains 2.
    Research must stop boundedly and outcome must NOT become SUFFICIENT.
    """
    session, req, channel_id, topic_id = make_test_fixture()

    authority = ManualAuthorityProvider({
        "aci journal": 90.0,
        "structural engineering review": 90.0,
        "concrete contractor monthly": 80.0,
    })

    # Seed initial 2 mechanisms
    src1 = ResearchSourceCreate(
        source_type=ResearchSourceType.MANUAL,
        title="Plastic Shrinkage Principles",
        publisher="ACI Journal",
        url="https://aci.org/plastic-shrinkage-initial",
        content_excerpt="Plastic shrinkage cracks develop when evaporation rate exceeds water bleeding.",
        primary_source_status=PrimarySourceStatus.CONFIRMED,
    )
    src2 = ResearchSourceCreate(
        source_type=ResearchSourceType.MANUAL,
        title="Drying Shrinkage Behavior",
        publisher="Structural Engineering Review",
        url="https://structeng.org/drying-shrinkage-initial",
        content_excerpt="Drying shrinkage manifests as internal moisture leaves hardened concrete paste.",
        primary_source_status=PrimarySourceStatus.CONFIRMED,
    )
    await research_service.add_source(session, req.id, src1, authority_provider=authority)
    await research_service.add_source(session, req.id, src2, authority_provider=authority)

    duplicate_candidates = [
        DiscoveryCandidate(
            canonical_url="https://concretecontractor.com/article-1",
            title="More on Plastic Shrinkage in Hot Weather",
            publisher="Concrete Contractor Monthly",
            snippet="Hot weather plastic shrinkage causes surface tension cracks.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        DiscoveryCandidate(
            canonical_url="https://concretecontractor.com/article-2",
            title="Understanding Long-Term Drying Shrinkage",
            publisher="Concrete Contractor Monthly",
            snippet="Restrained drying shrinkage produces tensile stress in hardened concrete.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
    ]
    duplicate_docs = [
        ExtractedResearchDocument(
            canonical_url="https://concretecontractor.com/article-1",
            title="More on Plastic Shrinkage in Hot Weather",
            publisher="Concrete Contractor Monthly",
            extracted_content="Hot weather plastic shrinkage causes surface tension cracks before curing finishes.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        ExtractedResearchDocument(
            canonical_url="https://concretecontractor.com/article-2",
            title="Understanding Long-Term Drying Shrinkage",
            publisher="Concrete Contractor Monthly",
            extracted_content="Restrained drying shrinkage produces tensile stress in hardened concrete members.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
    ]

    provider = InMemoryDiscoveryProvider(seeded_candidates=duplicate_candidates)
    extractor = InMemoryResearchContentExtractor(seeded_documents=duplicate_docs)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
        max_rounds=2,
    )

    # Distinct coverage remains 2 despite new sources!
    assert result["final_supported_count"] == 2
    # Fails closed: outcome must NOT be SUFFICIENT
    assert result["brief"].outcome != ResearchOutcome.SUFFICIENT
    assert result["brief"].outcome == ResearchOutcome.PARTIAL
    assert result["brief"].metadata.get("numeric_promise_fulfilled") is False


# ── 7. Mandatory Test: Budget Exhaustion (Phase T) ──


@pytest.mark.asyncio
async def test_budget_exhaustion_stops_boundedly():
    """Synthetic Scenario (Phase T):
    Title promises 5. Provider only has 1 additional mechanism (total 3).
    Rounds are exhausted without reaching 5.
    Must stop boundedly with outcome != SUFFICIENT and no infinite loop.
    """
    session, req, channel_id, topic_id = make_test_fixture()

    authority = ManualAuthorityProvider({"aci journal": 90.0, "thermal press": 85.0})

    src1 = ResearchSourceCreate(
        source_type=ResearchSourceType.MANUAL,
        title="Plastic Shrinkage",
        publisher="ACI Journal",
        url="https://aci.org/p-shrinkage",
        content_excerpt="Plastic shrinkage cracks occur when rapid evaporation takes place.",
        primary_source_status=PrimarySourceStatus.CONFIRMED,
    )
    src2 = ResearchSourceCreate(
        source_type=ResearchSourceType.MANUAL,
        title="Drying Shrinkage",
        publisher="ACI Journal",
        url="https://aci.org/d-shrinkage",
        content_excerpt="Drying shrinkage occurs as hardened concrete dries out over time.",
        primary_source_status=PrimarySourceStatus.CONFIRMED,
    )
    await research_service.add_source(session, req.id, src1, authority_provider=authority)
    await research_service.add_source(session, req.id, src2, authority_provider=authority)

    one_additional = [
        DiscoveryCandidate(
            canonical_url="https://thermalpress.org/thermal-cracking",
            title="Thermal Cracking",
            publisher="Thermal Press",
            snippet="Thermal contraction cracking occurs during cooling of thick slabs.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        )
    ]
    one_doc = [
        ExtractedResearchDocument(
            canonical_url="https://thermalpress.org/thermal-cracking",
            title="Thermal Cracking",
            publisher="Thermal Press",
            extracted_content="Thermal contraction cracking occurs during cooling of thick slabs.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        )
    ]
    provider = InMemoryDiscoveryProvider(seeded_candidates=one_additional)
    extractor = InMemoryResearchContentExtractor(seeded_documents=one_doc)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
        max_rounds=2,
    )

    assert result["final_supported_count"] == 3
    assert result["stop_reason"] in (
        ResearchCoverageStopReason.SEARCH_BUDGET_EXHAUSTED.value,
        ResearchCoverageStopReason.NO_NEW_RELEVANT_SOURCES.value,
        ResearchCoverageStopReason.NUMERIC_COVERAGE_NOT_FULFILLED.value,
    )
    assert result["brief"].outcome != ResearchOutcome.SUFFICIENT


# ── 8. Provider Failure Resilience ──


@pytest.mark.asyncio
async def test_provider_failure_stops_safely():
    """Verify provider exceptions fail closed safely without crashing."""
    session, req, channel_id, topic_id = make_test_fixture()

    failing_provider = InMemoryDiscoveryProvider(fail=True, failure_message="Connection timed out")

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=failing_provider,
    )

    assert result["stop_reason"] == ResearchCoverageStopReason.DISCOVERY_PROVIDER_UNAVAILABLE.value
    assert result["brief"].outcome == ResearchOutcome.INSUFFICIENT


# ── 9. Manual Mode Preservation (Phase U) ──


@pytest.mark.asyncio
async def test_manual_mode_preservation():
    """Verify that when acquisition_mode is MANUAL, automatic discovery does not run."""
    session, req, channel_id, topic_id = make_test_fixture(acquisition_mode="MANUAL")

    provider = InMemoryDiscoveryProvider(seeded_candidates=[
        DiscoveryCandidate(
            canonical_url="https://some-site.com/test",
            title="Some Title",
            publisher="Publisher",
            snippet="Some excerpt about concrete.",
        )
    ])

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
    )

    assert result["stop_reason"] == ResearchCoverageStopReason.MANUAL_ONLY_AWAITING_INPUT.value
    assert result["manual_input_required"] is True
    assert len(provider.call_history) == 0  # Provider was never called!


# ── 10. Non-Numeric Topic Behavior (Phase V) ──


@pytest.mark.asyncio
async def test_non_numeric_topic_quality_pass():
    """Verify non-numeric topics achieve SUFFICIENT via normal research quality rules."""
    session, req, channel_id, topic_id = make_test_fixture(
        topic_title="The Physics of Mechanical Resonance",
        acquisition_mode="AUTOMATIC_SEARCH",
    )
    authority = ManualAuthorityProvider({"physics journal": 95.0, "acoustics institute": 90.0})

    candidates = [
        DiscoveryCandidate(
            canonical_url="https://physicsjournal.org/resonance-basics",
            title="Foundations of Mechanical Resonance",
            publisher="Physics Journal",
            snippet="Resonance occurs when driving frequency matches natural frequency.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        DiscoveryCandidate(
            canonical_url="https://acoustics.org/damping-effects",
            title="Damping Effects in Oscillating Systems",
            publisher="Acoustics Institute",
            snippet="Damping reduces resonant peak amplitude.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        DiscoveryCandidate(
            canonical_url="https://physicsjournal.org/dynamic-amplification",
            title="Dynamic Amplification Factor in Resonance",
            publisher="Physics Journal",
            snippet="Amplification peaks at resonance frequency.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
    ]
    docs = [
        ExtractedResearchDocument(
            canonical_url="https://physicsjournal.org/resonance-basics",
            title="Foundations of Mechanical Resonance",
            publisher="Physics Journal",
            extracted_content="Mechanical resonance occurs when a physical system drives another system at harmonic frequency.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        ExtractedResearchDocument(
            canonical_url="https://acoustics.org/damping-effects",
            title="Damping Effects in Oscillating Systems",
            publisher="Acoustics Institute",
            extracted_content="Damping reduces resonant peak amplitude and prevents structural catastrophic failure.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        ExtractedResearchDocument(
            canonical_url="https://physicsjournal.org/dynamic-amplification",
            title="Dynamic Amplification Factor in Resonance",
            publisher="Physics Journal",
            extracted_content="Dynamic amplification factors reach maximum amplitude when driving frequency equals natural frequency.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
    ]

    provider = InMemoryDiscoveryProvider(seeded_candidates=candidates)
    extractor = InMemoryResearchContentExtractor(seeded_documents=docs)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
    )

    assert result["stop_reason"] == ResearchCoverageStopReason.COVERAGE_FULFILLED.value
    assert result["brief"].outcome == ResearchOutcome.SUFFICIENT


# ── 11. Idempotency & Observability (Phases O, P) ──


@pytest.mark.asyncio
async def test_idempotent_retry_and_observability():
    """Verify retry does not duplicate sources and records complete round truth."""
    session, req, channel_id, topic_id = make_test_fixture()
    authority = ManualAuthorityProvider({"aci journal": 90.0})

    candidates = [
        DiscoveryCandidate(
            canonical_url="https://aci.org/mechanism-1",
            title="Mechanism 1",
            publisher="ACI Journal",
            snippet="Plastic shrinkage happens during early curing.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
    ]
    docs = [
        ExtractedResearchDocument(
            canonical_url="https://aci.org/mechanism-1",
            title="Mechanism 1",
            publisher="ACI Journal",
            extracted_content="Plastic shrinkage happens during early curing of flatwork slabs.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        )
    ]
    provider = InMemoryDiscoveryProvider(seeded_candidates=candidates)
    extractor = InMemoryResearchContentExtractor(seeded_documents=docs)

    res1 = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
        max_rounds=1,
    )

    truth1 = res1["coverage_truth"]
    assert len(truth1) == 1
    assert truth1[0]["round_number"] == 1
    assert truth1[0]["sources_accepted"] == 1
    assert len(truth1[0]["queries"]) > 0

    # Check request metadata persistence
    assert "coverage_expansion" in req.metadata_
    assert req.metadata_["coverage_expansion"]["stop_reason"] is not None


# ── 12. Phase B Regression: Discovery Snippet Cannot Become Evidence ──


@pytest.mark.asyncio
async def test_discovery_snippet_cannot_become_evidence():
    """Regression test proving SEARCH_SNIPPET_CAN_BECOME_EVIDENCE = NO:
    A discovery snippet explicitly claims:
        'Thermal contraction is a major concrete cracking mechanism'
    but the extracted source document does NOT support that proposition.

    Required result:
    - no verified thermal-contraction claim may be created from the snippet
    - snippet must not appear as ClaimEvidence authority
    - numeric coverage must not increase because of the snippet
    """
    session, req, channel_id, topic_id = make_test_fixture()

    authority = ManualAuthorityProvider({
        "aci journal": 90.0,
        "structural engineering review": 90.0,
        "curing guide publisher": 85.0,
    })

    # Seed 2 initial mechanisms (Plastic Shrinkage and Drying Shrinkage)
    src1 = ResearchSourceCreate(
        source_type=ResearchSourceType.MANUAL,
        title="Plastic Shrinkage Guide",
        publisher="ACI Journal",
        url="https://aci.org/plastic-shrinkage-p03a1",
        content_excerpt="Plastic shrinkage cracks occur when rapid evaporation takes place on freshly poured concrete.",
        primary_source_status=PrimarySourceStatus.CONFIRMED,
    )
    src2 = ResearchSourceCreate(
        source_type=ResearchSourceType.MANUAL,
        title="Drying Shrinkage Review",
        publisher="Structural Engineering Review",
        url="https://structeng.org/drying-shrinkage-p03a1",
        content_excerpt="Drying shrinkage manifests as internal moisture leaves hardened concrete paste over time.",
        primary_source_status=PrimarySourceStatus.CONFIRMED,
    )
    await research_service.add_source(session, req.id, src1, authority_provider=authority)
    await research_service.add_source(session, req.id, src2, authority_provider=authority)

    # Snippet makes a concrete claim, but the extracted document DOES NOT support it!
    cand = DiscoveryCandidate(
        canonical_url="https://curingguide.org/curing-protocols",
        title="Standard Field Curing Protocols",
        publisher="Curing Guide Publisher",
        snippet="Thermal contraction is a major concrete cracking mechanism caused by steep thermal gradients.",
        primary_source_status=PrimarySourceStatus.CONFIRMED,
    )
    doc = ExtractedResearchDocument(
        canonical_url="https://curingguide.org/curing-protocols",
        title="Standard Field Curing Protocols",
        publisher="Curing Guide Publisher",
        # Document text only discusses curing procedures for plastic shrinkage, nothing about thermal cracking mechanism!
        extracted_content="Field curing procedures must maintain ambient moisture to prevent plastic shrinkage cracking in fresh concrete.",
        primary_source_status=PrimarySourceStatus.CONFIRMED,
    )

    provider = InMemoryDiscoveryProvider(seeded_candidates=[cand])
    extractor = InMemoryResearchContentExtractor(seeded_documents=[doc])

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
        max_rounds=1,
    )

    # 1. Snippet MUST NOT appear in ResearchSource.content_excerpt
    sources = session.sources
    ingested_web_src = next(s for s in sources if s.url == "https://curingguide.org/curing-protocols")
    assert "thermal contraction" not in ingested_web_src.content_excerpt.lower()
    assert ingested_web_src.content_excerpt == doc.extracted_content

    # 2. Snippet MUST NOT appear as ClaimEvidence authority
    claims = session.claims
    all_evidence = [e for c in claims for e in (c.evidence or [])]
    for ev in all_evidence:
        assert "thermal contraction is a major concrete cracking mechanism" not in ev.excerpt.lower()

    # 3. Numeric coverage MUST NOT increase because of the snippet!
    final_families = result["final_supported_families"]
    assert "thermal" not in " ".join(final_families).lower()
    assert result["final_supported_count"] == 2  # Remains 2, does NOT become 3!


# ── 13. Phase C Regression: 5 Raw Families / 3 Verified Coverage Hardening ──


@pytest.mark.asyncio
async def test_five_raw_families_three_verified_coverage_fails_closed():
    """Regression test proving:
    - 5 distinct raw claim families exist
    - only 3 pass canonical verification
    - promised_count = 5

    Required:
        VERIFIED_SUPPORTED_COUNT = 3
        COVERAGE_FULFILLED = NO
        additional research continues if budget remains (or stops unfulfilled if exhausted)
    """
    session, req, channel_id, topic_id = make_test_fixture()

    authority = ManualAuthorityProvider({
        "materials press": 90.0,
    })

    # Ingest 3 verified FACT claims and 2 unverified CAUSAL claims
    # (In OMEGA scoring, ClaimType.CAUSAL claims are not verified as absolute facts)
    doc_text = """
Plastic shrinkage cracking occurs when early evaporation rate exceeds surface water bleeding.
Drying shrinkage develops over months as hardened paste steadily loses internal moisture.
Thermal contraction cracking takes place when massive elements cool rapidly after hydration.
Alkali-silica reaction causes expansive gel swelling under uncertain chemical exposure conditions.
Structural overload causes flexural tension failure when applied load exceeds design limit.
"""
    # Create claims directly in source metadata
    structured_claims = [
        {"text": "Plastic shrinkage cracking occurs when early evaporation rate exceeds surface water bleeding.", "type": "FACT", "strength_score": 90.0},
        {"text": "Drying shrinkage develops over months as hardened paste steadily loses internal moisture.", "type": "FACT", "strength_score": 90.0},
        {"text": "Thermal contraction cracking takes place when massive elements cool rapidly after hydration.", "type": "FACT", "strength_score": 90.0},
        # These 2 are CAUSAL -> evaluate_claim_confidence marks is_verified = False!
        {"text": "Alkali-silica reaction causes expansive gel swelling under uncertain chemical exposure conditions.", "type": "CAUSAL", "strength_score": 80.0},
        {"text": "Structural overload causes flexural tension failure when applied load exceeds design limit.", "type": "CAUSAL", "strength_score": 80.0},
    ]

    src = ResearchSourceCreate(
        source_type=ResearchSourceType.MANUAL,
        title="Comprehensive Crack Mechanisms Overview",
        publisher="Materials Press",
        url="https://materialspress.org/crack-mechanisms",
        content_excerpt=doc_text,
        primary_source_status=PrimarySourceStatus.CONFIRMED,
        metadata={"claims": structured_claims},
    )
    await research_service.add_source(session, req.id, src, authority_provider=authority)

    # Empty discovery provider (no additional sources)
    provider = NullDiscoveryProvider()

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        authority_provider=authority,
        max_rounds=1,
    )

    # 5 raw claims exist, but only 3 pass canonical verification!
    assert len(session.claims) == 5
    verified_claims = [c for c in session.claims if c.is_verified]
    assert len(verified_claims) == 3

    # Coverage MUST be 3, NOT 5!
    assert result["final_supported_count"] == 3
    assert result["stop_reason"] != ResearchCoverageStopReason.COVERAGE_FULFILLED.value
    assert result["brief"].outcome != ResearchOutcome.SUFFICIENT


# ── 14. Phase C Positive Regression: 5 Verified Distinct Families Fulfill Coverage ──


@pytest.mark.asyncio
async def test_five_raw_five_verified_distinct_families_fulfills_coverage():
    """Verify that when 5 raw claims exist and ALL 5 pass canonical verification,
    coverage is genuinely fulfilled.
    """
    session, req, channel_id, topic_id = make_test_fixture()
    authority = ManualAuthorityProvider({
        "materials press": 90.0,
        "concrete science review": 90.0,
    })

    claims_part1 = [
        {"text": "Plastic shrinkage cracking occurs when early evaporation exceeds bleeding.", "type": "FACT", "strength_score": 90.0},
        {"text": "Drying shrinkage develops over months as hardened paste loses internal moisture.", "type": "FACT", "strength_score": 90.0},
        {"text": "Thermal contraction cracking takes place when massive elements cool unevenly.", "type": "FACT", "strength_score": 90.0},
    ]
    claims_part2 = [
        {"text": "Alkali-silica reaction produces internal expansive gel resulting in map cracking.", "type": "FACT", "strength_score": 90.0},
        {"text": "Structural overloading produces flexural tensile cracks when design load is exceeded.", "type": "FACT", "strength_score": 90.0},
    ]

    src1 = ResearchSourceCreate(
        source_type=ResearchSourceType.MANUAL,
        title="Three Initial Concrete Mechanisms",
        publisher="Materials Press",
        url="https://materialspress.org/three-mechanisms",
        content_excerpt="Comprehensive review of three distinct cracking mechanisms.",
        primary_source_status=PrimarySourceStatus.CONFIRMED,
        metadata={"claims": claims_part1},
    )
    src2 = ResearchSourceCreate(
        source_type=ResearchSourceType.MANUAL,
        title="Two Additional Concrete Mechanisms",
        publisher="Concrete Science Review",
        url="https://concretescience.org/two-mechanisms",
        content_excerpt="Comprehensive review of chemical and structural cracking mechanisms.",
        primary_source_status=PrimarySourceStatus.CONFIRMED,
        metadata={"claims": claims_part2},
    )
    await research_service.add_source(session, req.id, src1, authority_provider=authority)
    await research_service.add_source(session, req.id, src2, authority_provider=authority)

    provider = NullDiscoveryProvider()
    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        authority_provider=authority,
        max_rounds=1,
    )

    assert result["final_supported_count"] == 5
    assert result["stop_reason"] == ResearchCoverageStopReason.COVERAGE_FULFILLED.value
    assert result["brief"].outcome == ResearchOutcome.SUFFICIENT


# ── 15. Phase D Regression: Stop Reason / Brief Consistency Gate ──


@pytest.mark.asyncio
async def test_stop_reason_brief_consistency_enforced():
    """Verify impossible final state is prevented:
    stop_reason == COVERAGE_FULFILLED while brief.outcome != SUFFICIENT.
    If brief.outcome is not SUFFICIENT, stop_reason MUST fail closed to a non-fulfilled reason.
    """
    session, req, channel_id, topic_id = make_test_fixture()
    authority = ManualAuthorityProvider({"low quality press": 40.0})  # Below minimum_source_quality (50.0)!

    src = ResearchSourceCreate(
        source_type=ResearchSourceType.MANUAL,
        title="Low Quality Source",
        publisher="Low Quality Press",
        url="https://lowqual.org/article",
        content_excerpt="Some concrete cracking information from an unverified source.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
    )
    await research_service.add_source(session, req.id, src, authority_provider=authority)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=NullDiscoveryProvider(),
        authority_provider=authority,
    )

    # Inconsistent state (COVERAGE_FULFILLED + PARTIAL/INSUFFICIENT) must be blocked
    assert result["brief"].outcome != ResearchOutcome.SUFFICIENT
    assert result["stop_reason"] != ResearchCoverageStopReason.COVERAGE_FULFILLED.value


# ── 16. Phase E Regression: Deterministic Claim Extractor V2 Multi-Sentence Prose ──


def test_deterministic_claim_extraction_v2_multi_sentence_prose():
    """Mandatory positive regression for V2 claim extractor:
    One extracted technical document contains three separate explanatory mechanism
    sentences in different paragraphs.
    Required:
    - multiple useful claims can be extracted
    - every claim has an exact excerpt from the document
    - no invented proposition exists
    """
    doc_prose = """
Plastic shrinkage cracking occurs when early surface evaporation rate exceeds bleeding rate.

Drying shrinkage develops over months as hardened concrete paste steadily loses absorbed moisture.

Thermal contraction cracking takes place when massive structural elements cool unevenly from peak hydration temperatures.
"""
    claims = extract_deterministic_claims_from_source(
        source_title="Cracking Mechanisms in Concrete Structures",
        source_excerpt=doc_prose,
        metadata={},
        max_claims_per_source=5,
    )

    assert len(claims) == 3

    # Exact excerpt provenance: every claim excerpt must exist verbatim in doc_prose!
    for c in claims:
        assert c["excerpt"] in doc_prose
        assert len(c["claim_text"]) >= 25
        assert c["strength_score"] >= 70.0

    # Ensure no invented propositions exist
    texts = [c["claim_text"] for c in claims]
    assert any("plastic shrinkage" in t.lower() for t in texts)
    assert any("drying shrinkage" in t.lower() for t in texts)
    assert any("thermal contraction" in t.lower() for t in texts)


# ── 17. Phase E Negative Regression: Boilerplate Rejection ──


def test_deterministic_claim_extraction_v2_boilerplate_rejection():
    """Mandatory negative regression:
    Navigation, cookie, menu, and legal boilerplate must not become a technical claim.
    """
    boilerplate_text = """
Accept all cookies to enhance your browsing experience on our civil engineering portal.

Copyright 2026 Concrete World Publishing, All Rights Reserved.

Privacy Policy and Terms of Service apply to all registered members.

Subscribe to our daily newsletter for industry news and structural updates.

Click here to read more articles or skip to main content navigation.
"""
    claims = extract_deterministic_claims_from_source(
        source_title="Boilerplate Page",
        source_excerpt=boilerplate_text,
        metadata={},
    )
    # Zero claims extracted from boilerplate!
    assert len(claims) == 0


# ── 18. Phase F Regression: Strict Source Budgets & Partial Capacity Bound ──


@pytest.mark.asyncio
async def test_strict_source_budget_respects_both_limits_and_partial_round():
    """Verify strict source budget hard bounds:
    1. Effective ceiling respects min(req.max_sources, max_total_acquired_sources).
    2. If request currently has 9 sources and effective maximum is 10,
       a discovery round may ingest at most 1 additional source (partial capacity).
    3. Never accept a full round batch and overshoot the ceiling.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=10)

    authority = ManualAuthorityProvider({"aci journal": 90.0, "round publisher": 85.0})

    # Seed 9 existing sources into the session
    for i in range(1, 10):
        src = ResearchSourceCreate(
            source_type=ResearchSourceType.MANUAL,
            title=f"Existing Source {i}",
            publisher="ACI Journal",
            url=f"https://aci.org/source-{i}",
            content_excerpt=f"Valid technical prose for pre-existing source number {i} in the database.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        )
        await research_service.add_source(session, req.id, src, authority_provider=authority)

    assert len(session.sources) == 9

    # Provider returns 5 candidates for this round
    five_candidates = [
        DiscoveryCandidate(
            canonical_url=f"https://roundpub.org/new-source-{i}",
            title=f"New Source {i}",
            publisher="Round Publisher",
            snippet=f"Snippet for candidate number {i} in discovery round.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        )
        for i in range(1, 6)
    ]
    five_docs = [
        ExtractedResearchDocument(
            canonical_url=f"https://roundpub.org/new-source-{i}",
            title=f"New Source {i}",
            publisher="Round Publisher",
            extracted_content=f"Extracted content for prospective source number {i} in round.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        )
        for i in range(1, 6)
    ]

    provider = InMemoryDiscoveryProvider(seeded_candidates=five_candidates)
    extractor = InMemoryResearchContentExtractor(seeded_documents=five_docs)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=15,  # Stricter is req.max_sources = 10!
        max_rounds=2,
    )

    # Exactly 1 additional source could be accepted (9 + 1 = 10)
    assert len(session.sources) == 10
    assert result["stop_reason"] in (
        ResearchCoverageStopReason.SEARCH_BUDGET_EXHAUSTED.value,
        ResearchCoverageStopReason.NUMERIC_COVERAGE_NOT_FULFILLED.value,
    )


# ── 19. Phase B & E: Two Independent Unknown-Primary Web Sources Corroborate ──


@pytest.mark.asyncio
async def test_two_independent_unknown_primary_web_sources_corroborate_same_factual_proposition():
    """Verify that two independent web sources with PrimarySourceStatus.UNKNOWN
    corroborating the same factual proposition can achieve verification naturally
    under the EXISTING canonical scorer without artificial 90/100 authority scores.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=10)

    # Realistic moderate publisher authority (60.0, not artificial 90/100)
    authority = ManualAuthorityProvider({
        "engineering-standards.org": 60.0,
        "materials-research.org": 60.0,
    })

    cand_a = DiscoveryCandidate(
        canonical_url="https://engineering-standards.org/concrete-plastic-shrinkage",
        title="Concrete Cracking Mechanisms",
        publisher="engineering-standards.org",
        snippet="Evaporation before set causes plastic shrinkage.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=datetime.now(UTC),
    )
    doc_a = ExtractedResearchDocument(
        canonical_url="https://engineering-standards.org/concrete-plastic-shrinkage",
        title="Concrete Cracking Mechanisms",
        publisher="engineering-standards.org",
        extracted_content="In civil engineering concrete cracks occur when rapid surface evaporation before set produces plastic shrinkage cracking.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=datetime.now(UTC),
    )

    cand_b = DiscoveryCandidate(
        canonical_url="https://materials-research.org/concrete-cracking-mechanisms",
        title="Concrete Cracking Mechanisms",
        publisher="materials-research.org",
        snippet="Shrinkage cracks occur when water evaporates.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=datetime.now(UTC),
    )
    doc_b = ExtractedResearchDocument(
        canonical_url="https://materials-research.org/concrete-cracking-mechanisms",
        title="Concrete Cracking Mechanisms",
        publisher="materials-research.org",
        extracted_content="Civil engineering studies show that concrete cracks develop from plastic shrinkage when evaporation exceeds bleeding before set.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=datetime.now(UTC),
    )

    provider = InMemoryDiscoveryProvider(seeded_candidates=[cand_a, cand_b])
    extractor = InMemoryResearchContentExtractor(seeded_documents=[doc_a, doc_b])

    await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=10,
        max_rounds=1,
    )

    # 1 canonical claim reconciled with 2 independent evidence links
    claims = session.claims
    assert len(claims) == 1
    canonical_claim = claims[0]
    assert len(canonical_claim.evidence) == 2

    # Verification status and confidence metrics under existing scorer
    assert canonical_claim.is_verified is True
    assert canonical_claim.independent_sources_count == 2
    assert canonical_claim.supporting_sources_count == 2
    assert canonical_claim.confidence_score >= 70.0
    # Expected exact confidence calculation:
    # indep_comp = 2 * 35 = 70 (0.40 * 70 = 28.0)
    # quality = 0.35 * 60 + 40 = 61.0 (0.30 * 61.0 = 18.3)
    # strength = 80.0 (0.30 * 80.0 = 24.0)
    # total = 28.0 + 18.3 + 24.0 = 70.30
    assert canonical_claim.confidence_score == 70.3
    assert canonical_claim.confidence_band == "HIGH"
    assert "MULTI_SOURCE_INDEPENDENT_CONSENSUS" in canonical_claim.reasons
    assert "DIRECT_EXCERPT_EVIDENCE" in canonical_claim.reasons


# ── 20. Phase F: Single Unknown Web Source Remains Unverified ──


@pytest.mark.asyncio
async def test_single_unknown_primary_source_remains_unverified():
    """Verify that a single arbitrary web source with PrimarySourceStatus.UNKNOWN
    CANNOT verify under the existing scorer even with high publisher authority.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=10)

    # Even with authority score 70.0
    authority = ManualAuthorityProvider({"single-source.org": 70.0})

    cand = DiscoveryCandidate(
        canonical_url="https://single-source.org/concrete-plastic-shrinkage",
        title="Single Web Page",
        publisher="single-source.org",
        snippet="Plastic shrinkage cracking in concrete.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=datetime.now(UTC),
    )
    doc = ExtractedResearchDocument(
        canonical_url="https://single-source.org/concrete-plastic-shrinkage",
        title="Single Web Page",
        publisher="single-source.org",
        extracted_content="In civil engineering concrete cracks occur when rapid surface evaporation before set produces plastic shrinkage cracking.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=datetime.now(UTC),
    )

    provider = InMemoryDiscoveryProvider(seeded_candidates=[cand])
    extractor = InMemoryResearchContentExtractor(seeded_documents=[doc])

    await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=10,
        max_rounds=1,
    )

    claims = session.claims
    assert len(claims) == 1
    single_claim = claims[0]
    # Single source has n_independent = 1, confidence < 70, must be unverified
    assert single_claim.independent_sources_count == 1
    assert single_claim.confidence_score < 70.0
    assert single_claim.is_verified is False


# ── 21. Phase D: Syndicated / Same-Cluster Sources Do Not Count Independently ──


@pytest.mark.asyncio
async def test_syndicated_same_cluster_sources_do_not_count_independently():
    """Verify that two syndicated sources in the same independence cluster
    do not receive multi-source independent consensus and cannot verify.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=10)

    authority = ManualAuthorityProvider({"syndicated-wire.org": 60.0})

    # Both sources from the same domain / syndicated provider
    cand_a = DiscoveryCandidate(
        canonical_url="https://syndicated-wire.org/wire/article-1",
        title="Wire Article 1",
        publisher="syndicated-wire.org",
        snippet="Plastic shrinkage cracking.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=datetime.now(UTC),
    )
    doc_a = ExtractedResearchDocument(
        canonical_url="https://syndicated-wire.org/wire/article-1",
        title="Wire Article 1",
        publisher="syndicated-wire.org",
        extracted_content="In civil engineering concrete cracks occur when rapid surface evaporation before set produces plastic shrinkage cracking.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=datetime.now(UTC),
    )

    cand_b = DiscoveryCandidate(
        canonical_url="https://syndicated-wire.org/wire/article-2",
        title="Wire Article 2",
        publisher="syndicated-wire.org",
        snippet="Plastic shrinkage cracking.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=datetime.now(UTC),
    )
    doc_b = ExtractedResearchDocument(
        canonical_url="https://syndicated-wire.org/wire/article-2",
        title="Wire Article 2",
        publisher="syndicated-wire.org",
        extracted_content="Civil engineering studies show that concrete cracks develop from plastic shrinkage when evaporation exceeds bleeding before set.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=datetime.now(UTC),
    )

    provider = InMemoryDiscoveryProvider(seeded_candidates=[cand_a, cand_b])
    extractor = InMemoryResearchContentExtractor(seeded_documents=[doc_a, doc_b])

    await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=10,
        max_rounds=1,
    )

    claims = session.claims
    assert len(claims) == 1
    claim = claims[0]
    # Reconciled to 2 supporting evidence items, but only 1 independent cluster!
    assert len(claim.evidence) == 2
    assert claim.supporting_sources_count == 2
    assert claim.independent_sources_count == 1
    assert "MULTI_SOURCE_INDEPENDENT_CONSENSUS" not in claim.reasons
    assert claim.is_verified is False


# ── 22. Phase B: Conservative Claim Merge & Provenance ──


def test_conservative_claim_merge_matches_equivalent_propositions():
    """Verify that conservative proposition matching recognizes equivalent explanatory
    statements across independent sources while preserving exact excerpts.
    """
    text_a = "Rapid surface evaporation before set produces plastic shrinkage cracking."
    text_b = "Plastic shrinkage cracks develop when evaporation exceeds bleeding before concrete sets."

    assert are_propositions_corroborating(text_a, ClaimType.FACT, text_b, ClaimType.FACT) is True


def test_near_topic_different_proposition_does_not_merge():
    """Verify that statements on the same broad topic with different propositions
    do NOT merge.
    """
    text_a = "Plastic shrinkage occurs before concrete sets."
    text_b = "Plastic shrinkage cracks are commonly shallow."

    assert are_propositions_corroborating(text_a, ClaimType.FACT, text_b, ClaimType.FACT) is False


def test_contradiction_does_not_merge():
    """Verify that contradictory propositions with opposite polarity do NOT merge."""
    text_a = "Settlement cracking occurs when concrete consolidates around rebar."
    text_b = "Settlement cracking does not occur around rebar."

    assert are_propositions_corroborating(text_a, ClaimType.FACT, text_b, ClaimType.FACT) is False


# ── 23. Phase K: Claim Reconciliation Idempotency ──


@pytest.mark.asyncio
async def test_duplicate_retry_does_not_add_evidence_twice():
    """Verify that executing reconciliation repeatedly for the same source
    is idempotent and does not duplicate evidence links or inflate counts.
    """
    session, req, channel_id, topic_id = make_test_fixture()
    source = ResearchSource(
        id=uuid.uuid4(),
        research_request_id=req.id,
        channel_id=channel_id,
        source_type=ResearchSourceType.WEB_SEARCH.value,
        title="Source A",
        publisher="Pub A",
        content_excerpt="Rapid surface evaporation before set produces plastic shrinkage cracking.",
        quality_score=60.0,
        primary_source_status=PrimarySourceStatus.UNKNOWN.value,
    )
    session.sources.append(source)

    extracted = [
        {
            "claim_text": "Rapid surface evaporation before set produces plastic shrinkage cracking.",
            "claim_type": ClaimType.FACT,
            "excerpt": "Rapid surface evaporation before set produces plastic shrinkage cracking.",
            "strength_score": 80.0,
            "source_location": None,
        }
    ]

    claims: list[ResearchClaim] = []
    # First reconciliation
    reconcile_source_extractions_into_claims(
        session=session,
        existing_claims=claims,
        extracted_items=extracted,
        source=source,
        channel_id=channel_id,
        request_id=req.id,
    )
    assert len(claims) == 1
    assert len(claims[0].evidence) == 1

    # Second reconciliation (retry)
    reconcile_source_extractions_into_claims(
        session=session,
        existing_claims=claims,
        extracted_items=extracted,
        source=source,
        channel_id=channel_id,
        request_id=req.id,
    )
    # Zero duplicate claims or evidence created!
    assert len(claims) == 1
    assert len(claims[0].evidence) == 1


# ── 24. Phase G: Five URLs / One Mechanism => Distinct Supported Entities = 1 ──


@pytest.mark.asyncio
async def test_five_urls_one_mechanism_results_in_one_distinct_family():
    """Verify that 5 URLs describing 1 mechanism result in exactly 1 distinct entity
    family, preventing duplicate-URL coverage inflation.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=10)
    authority = ManualAuthorityProvider({f"publisher-{i}.org": 60.0 for i in range(1, 6)})

    contexts = [
        (
            "Highway department field inspection reports document extensive surface defect formations across bridge deck pours in civil engineering.",
            "Immediate fog spraying is recommended.",
        ),
        (
            "University materials engineering laboratory analysis investigates early hydration shrinkage dynamics in fresh concrete.",
            "Windbreaks should be erected.",
        ),
        (
            "Commercial paving contractor technical bulletin reviews preventive measures against moisture evaporation in civil engineering.",
            "Ambient humidity must be tracked.",
        ),
        (
            "Structural engineering institute advisory note explains tensile strain accumulation in fresh concrete placements.",
            "Curing blankets must be deployed.",
        ),
        (
            "Federal transportation administration research summary outlines protective curing protocols for civil engineering infrastructure.",
            "Surface moisture sensors should be used.",
        ),
    ]
    prop = "Plastic shrinkage cracking occurs when rapid surface evaporation before set exceeds water bleeding."

    candidates = [
        DiscoveryCandidate(
            canonical_url=f"https://publisher-{i}.org/plastic-shrinkage",
            title=f"Plastic Shrinkage Article {i}",
            publisher=f"publisher-{i}.org",
            snippet="Plastic shrinkage cracking.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
            published_at=datetime.now(UTC),
        )
        for i in range(1, 6)
    ]
    documents = [
        ExtractedResearchDocument(
            canonical_url=f"https://publisher-{i}.org/plastic-shrinkage",
            title=f"Plastic Shrinkage Article {i}",
            publisher=f"publisher-{i}.org",
            extracted_content=f"{intro} {prop} {outro}",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
            published_at=datetime.now(UTC),
        )
        for i, (intro, outro) in enumerate(contexts, 1)
    ]

    provider = InMemoryDiscoveryProvider(seeded_candidates=candidates)
    extractor = InMemoryResearchContentExtractor(seeded_documents=documents)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=10,
        max_rounds=1,
    )

    # All 5 URLs corroborate the single plastic shrinkage mechanism
    assert result["final_supported_count"] == 1
    assert result["final_supported_families"] in (["plastic_shrinkage"], ["plastic shrinkage"])
    # Coverage is not fulfilled because promised_count = 5
    assert result["stop_reason"] in (
        ResearchCoverageStopReason.NUMERIC_COVERAGE_NOT_FULFILLED.value,
        ResearchCoverageStopReason.SEARCH_BUDGET_EXHAUSTED.value,
    )


# ── 25. Phase G: Five Genuinely Corroborated Distinct Mechanism Families => 5/5 ──


@pytest.mark.asyncio
async def test_five_genuinely_verified_mechanism_families_fulfills_coverage():
    """Verify that 5 distinct mechanisms, each corroborated by 2 independent sources,
    achieve 5/5 verified coverage and stop with COVERAGE_FULFILLED.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=15)

    mechanisms = [
        (
            "plastic shrinkage",
            "Plastic shrinkage cracking occurs when rapid surface evaporation before set exceeds water bleeding.",
            "Plastic shrinkage cracks develop when evaporation exceeding bleeding before set produces severe cracking.",
        ),
        (
            "drying shrinkage",
            "Drying shrinkage cracking occurs when long-term moisture loss from paste causes drying shrinkage cracking.",
            "Drying shrinkage cracks develop when continuous moisture loss from paste causes drying shrinkage cracking.",
        ),
        (
            "thermal contraction",
            "Thermal contraction cracking occurs when cooling from peak hydration temperatures produces thermal contraction cracking.",
            "Thermal contraction cracks develop when cooling from peak hydration temperatures produces thermal contraction cracking.",
        ),
        (
            "alkali-silica reaction",
            "Alkali-silica reaction cracking occurs when internal gel swelling produces alkali-silica reaction cracking.",
            "Alkali-silica reaction cracks develop when internal gel swelling produces alkali-silica reaction cracking.",
        ),
        (
            "chemical sulfate attack",
            "Chemical sulfate attack cracking occurs when ettringite expansion produces chemical sulfate attack cracking.",
            "Chemical sulfate attack cracks develop when ettringite expansion produces chemical sulfate attack cracking.",
        ),
    ]

    authority_scores = {}
    candidates = []
    documents = []

    for idx, (mech_name, prop_a, prop_b) in enumerate(mechanisms, 1):
        pub_a = f"journal-a-{idx}.org"
        pub_b = f"journal-b-{idx}.org"
        authority_scores[pub_a] = 60.0
        authority_scores[pub_b] = 60.0

        url_a = f"https://{pub_a}/{mech_name.replace(' ', '-')}"
        url_b = f"https://{pub_b}/{mech_name.replace(' ', '-')}"

        text_a = (
            f"Field inspection handbook covers early concrete deterioration mechanisms under drying winds in civil engineering. "
            f"{prop_a} Preventive jobsite practices help minimize this defect."
        )
        text_b = (
            f"Academic laboratory research studies concrete curing behavior and stress development in slabs for civil engineering projects. "
            f"{prop_b} Rigorous test protocols confirm these findings."
        )

        candidates.extend([
            DiscoveryCandidate(
                canonical_url=url_a,
                title=f"{mech_name} Article A",
                publisher=pub_a,
                snippet=f"{mech_name} causes concrete cracks.",
                primary_source_status=PrimarySourceStatus.UNKNOWN,
                published_at=datetime.now(UTC),
            ),
            DiscoveryCandidate(
                canonical_url=url_b,
                title=f"{mech_name} Article B",
                publisher=pub_b,
                snippet=f"{mech_name} causes concrete cracks.",
                primary_source_status=PrimarySourceStatus.UNKNOWN,
                published_at=datetime.now(UTC),
            ),
        ])

        documents.extend([
            ExtractedResearchDocument(
                canonical_url=url_a,
                title=f"{mech_name} Article A",
                publisher=pub_a,
                extracted_content=text_a,
                primary_source_status=PrimarySourceStatus.UNKNOWN,
                published_at=datetime.now(UTC),
            ),
            ExtractedResearchDocument(
                canonical_url=url_b,
                title=f"{mech_name} Article B",
                publisher=pub_b,
                extracted_content=text_b,
                primary_source_status=PrimarySourceStatus.UNKNOWN,
                published_at=datetime.now(UTC),
            ),
        ])

    authority = ManualAuthorityProvider(authority_scores)
    provider = InMemoryDiscoveryProvider(seeded_candidates=candidates)
    extractor = InMemoryResearchContentExtractor(seeded_documents=documents)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
        max_candidates_per_query=10,
        max_accepted_sources_per_round=10,
        max_total_acquired_sources=15,
        max_rounds=2,
    )
    assert result["final_supported_count"] == 5
    assert len(result["final_supported_families"]) == 5
    assert result["stop_reason"] == ResearchCoverageStopReason.COVERAGE_FULFILLED.value
    assert result["brief"].outcome == ResearchOutcome.SUFFICIENT


# ── 26. Phase H: Discovery Metadata Authority Firewall ──


@pytest.mark.asyncio
async def test_discovery_metadata_cannot_inject_claims():
    """Verify that malicious or accidental 'claims' keys in DiscoveryCandidate.metadata
    are never interpreted as canonical claims or evidence.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=5)
    authority = ManualAuthorityProvider({"untrusted-discovery.org": 60.0})

    cand = DiscoveryCandidate(
        canonical_url="https://untrusted-discovery.org/page",
        title="Untrusted Discovery Page",
        publisher="untrusted-discovery.org",
        snippet="Snippet text.",
        metadata={
            "claims": [
                {"text": "Fabricated mechanism proposition injected through discovery metadata"}
            ],
            "evidence": [{"fake": "data"}],
            "is_verified": True,
        },
        primary_source_status=PrimarySourceStatus.UNKNOWN,
    )
    doc = ExtractedResearchDocument(
        canonical_url="https://untrusted-discovery.org/page",
        title="Untrusted Discovery Page",
        publisher="untrusted-discovery.org",
        extracted_content="Rapid surface evaporation before set produces plastic shrinkage cracking in concrete.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
    )

    provider = InMemoryDiscoveryProvider(seeded_candidates=[cand])
    extractor = InMemoryResearchContentExtractor(seeded_documents=[doc])

    await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=5,
        max_rounds=1,
    )

    for claim in session.claims:
        assert "Fabricated mechanism" not in claim.claim_text
        for ev in claim.evidence:
            assert "Fabricated mechanism" not in ev.excerpt


# ── 27. Phase I: Trusted Manual Structured Claims Preserved ──


def test_trusted_manual_structured_claims_still_work():
    """Verify that trusted MANUAL / IMPORT sources with structured claims in metadata
    continue to be extracted correctly.
    """
    metadata = {
        "claims": [
            {
                "text": "Trusted manual engineering standard statement on concrete cracking.",
                "type": "FACT",
                "strength_score": 85.0,
            }
        ]
    }
    extracted = extract_deterministic_claims_from_source(
        source_title="Manual Standard",
        source_excerpt="Valid manual technical prose.",
        metadata=metadata,
        source_type=ResearchSourceType.MANUAL,
    )
    assert len(extracted) == 1
    assert extracted[0]["claim_text"] == "Trusted manual engineering standard statement on concrete cracking."
    assert extracted[0]["strength_score"] == 85.0


# ── 28. Phase J: 10,000-char Extracted Document Canonical Bounding ──


@pytest.mark.asyncio
async def test_ten_k_document_ingestion_canonical_bounding():
    """Verify that an ExtractedResearchDocument with 10,000 characters is deterministically
    bounded to <= 5,000 characters before ResearchSourceCreate validation.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=5)
    authority = ManualAuthorityProvider({"long-doc.org": 60.0})

    ten_k_content = ("Concrete cracks in civil engineering explanation sentence. " * 200)[:9999]
    assert len(ten_k_content) > 9000

    cand = DiscoveryCandidate(
        canonical_url="https://long-doc.org/ten-k-page",
        title="Ten K Document Page",
        publisher="long-doc.org",
        snippet="A very long document snippet.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
    )
    doc = ExtractedResearchDocument(
        canonical_url="https://long-doc.org/ten-k-page",
        title="Ten K Document Page",
        publisher="long-doc.org",
        extracted_content=ten_k_content,
        primary_source_status=PrimarySourceStatus.UNKNOWN,
    )

    provider = InMemoryDiscoveryProvider(seeded_candidates=[cand])
    extractor = InMemoryResearchContentExtractor(seeded_documents=[doc])

    # Must complete without Pydantic validation error
    await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        authority_provider=authority,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=5,
        max_rounds=1,
    )

    assert len(session.sources) == 1
    stored_source = session.sources[0]
    assert len(stored_source.content_excerpt) <= 5000
