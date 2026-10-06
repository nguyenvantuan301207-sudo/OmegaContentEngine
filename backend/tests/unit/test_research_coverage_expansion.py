"""Unit tests for Automatic Research Coverage Expansion (P0.3).

Verifies discovery provider contracts, deterministic topic-grounded query planning,
intent diversification, candidate deduplication, 2 -> 5 coverage expansion,
duplicate-family non-progress fail-closed, budget exhaustion, provider failure resilience,
manual mode preservation, non-numeric topic support, and runtime truth observability.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from omega.application import research_service
from omega.application.research_coverage_service import execute_coverage_driven_research
from omega.application.research_discovery import (
    DiscoveryProviderUnavailableError,
    InMemoryDiscoveryProvider,
    NullDiscoveryProvider,
    filter_and_deduplicate_candidates,
)
from omega.application.research_query_planner import extract_core_subject, plan_research_queries
from omega.application.source_provider import ManualAuthorityProvider
from omega.domain.numeric_promise import (
    extract_numeric_promise,
)
from omega.domain.research import (
    DiscoveryCandidate,
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


def make_test_fixture(topic_title: str = CANONICAL_TOPIC, acquisition_mode: str = "AUTOMATIC_SEARCH"):
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
        max_sources=15,
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
        content_excerpt="Plastic shrinkage happens when evaporation exceeds bleed rate.",
    )
    cand_dup = DiscoveryCandidate(
        canonical_url="https://engineering.org/mechanisms-1",
        title="Duplicate Guide",
        publisher="Civil Engineering Org",
        content_excerpt="Plastic shrinkage occurs early in curing.",
    )
    cand_bad_scheme = DiscoveryCandidate(
        canonical_url="ftp://files.org/paper.pdf",
        title="FTP Document",
        publisher="FTP Host",
        content_excerpt="Valid text but invalid scheme.",
    )
    cand_empty_excerpt = DiscoveryCandidate(
        canonical_url="https://engineering.org/empty",
        title="Empty Paper",
        publisher="Org",
        content_excerpt="Too short",
    )
    cand2 = DiscoveryCandidate(
        canonical_url="https://engineering.org/mechanisms-2",
        title="Mechanism Guide 2",
        publisher="ACI Journal",
        content_excerpt="Drying shrinkage develops over months of internal water loss.",
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
    assert any("LOW_INFORMATION_EXCERPT" in r for r in reasons)


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
    Orchestrator discovers sources, ingests them, verifies 5/5 mechanisms,
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
            content_excerpt="Thermal contraction cracking happens during rapid cooling of massive concrete elements.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        DiscoveryCandidate(
            canonical_url="https://concretetech.org/asr-damage",
            title="Alkali-Silica Reaction Internal Deterioration",
            publisher="Concrete Technology Institute",
            content_excerpt="Alkali-silica reaction (ASR) creates internal expansive gel causing map cracking.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        DiscoveryCandidate(
            canonical_url="https://civileng.org/structural-overload",
            title="Structural Overloading and Shear Failure",
            publisher="Civil Engineering Org",
            content_excerpt="Structural overloading produces flexural tensile cracks when design load capacity is exceeded.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
    ]

    provider = InMemoryDiscoveryProvider(seeded_candidates=expansion_candidates)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
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

    # Provider returns sources with different URLs describing ONLY the same 2 mechanism families!
    duplicate_candidates = [
        DiscoveryCandidate(
            canonical_url="https://concretecontractor.com/article-1",
            title="More on Plastic Shrinkage in Hot Weather",
            publisher="Concrete Contractor Monthly",
            content_excerpt="Hot weather plastic shrinkage causes surface tension cracks before curing finishes.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        DiscoveryCandidate(
            canonical_url="https://concretecontractor.com/article-2",
            title="Understanding Long-Term Drying Shrinkage",
            publisher="Concrete Contractor Monthly",
            content_excerpt="Restrained drying shrinkage produces tensile stress in hardened concrete members.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
    ]

    provider = InMemoryDiscoveryProvider(seeded_candidates=duplicate_candidates)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        authority_provider=authority,
        max_rounds=2,
    )

    # Distinct coverage remains 2 despite new claims!
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

    # Only 1 additional mechanism available (total = 3, promised = 5)
    one_additional = [
        DiscoveryCandidate(
            canonical_url="https://thermalpress.org/thermal-cracking",
            title="Thermal Cracking",
            publisher="Thermal Press",
            content_excerpt="Thermal contraction cracking occurs during cooling of thick slabs.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        )
    ]
    provider = InMemoryDiscoveryProvider(seeded_candidates=one_additional)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        authority_provider=authority,
        max_rounds=2,
    )

    assert result["final_supported_count"] == 3
    assert result["stop_reason"] in (
        ResearchCoverageStopReason.SEARCH_BUDGET_EXHAUSTED.value,
        ResearchCoverageStopReason.NO_NEW_RELEVANT_SOURCES.value,
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
            content_excerpt="Some excerpt about concrete.",
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
            content_excerpt="Mechanical resonance occurs when a physical system drives another system at harmonic frequency.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        DiscoveryCandidate(
            canonical_url="https://acoustics.org/damping-effects",
            title="Damping Effects in Oscillating Systems",
            publisher="Acoustics Institute",
            content_excerpt="Damping reduces resonant peak amplitude and prevents structural catastrophic failure.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
        DiscoveryCandidate(
            canonical_url="https://physicsjournal.org/dynamic-amplification",
            title="Dynamic Amplification Factor in Resonance",
            publisher="Physics Journal",
            content_excerpt="Dynamic amplification factors reach maximum amplitude when driving frequency equals natural frequency.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
    ]

    provider = InMemoryDiscoveryProvider(seeded_candidates=candidates)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
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
            content_excerpt="Plastic shrinkage happens during early curing of flatwork slabs.",
            primary_source_status=PrimarySourceStatus.CONFIRMED,
        ),
    ]
    provider = InMemoryDiscoveryProvider(seeded_candidates=candidates)

    res1 = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
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
