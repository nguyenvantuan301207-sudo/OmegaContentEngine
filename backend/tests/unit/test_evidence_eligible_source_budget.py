"""Unit tests for Evidence-Eligible Source Budget Repair (P0.3c.2).

Verifies:
1. Low-quality candidates (< minimum_source_quality) are rejected before persistence.
2. Low-quality candidates do NOT consume the accepted-source budget or round budget.
3. Qualifying candidates later in the discovered pool are evaluated and accepted up to round limit.
4. If all discovered candidates are below threshold, zero sources are persisted and stop is fail-closed.
5. Mixed rejections (duplicates, extraction failures, quality rejections) are cleanly observable.
6. Effective max_sources bounds total persisted sources.
7. Pre-score equals persisted quality score.
8. Live canary fixture replay proves orchestrator does not stop on early sub-50 candidates.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from omega.application.research_coverage_service import execute_coverage_driven_research
from omega.application.research_discovery import (
    InMemoryDiscoveryProvider,
    InMemoryResearchContentExtractor,
)
from omega.application.research_scorer import calculate_source_quality
from omega.application.source_normalizer import bound_excerpt, normalize_url
from omega.domain.numeric_promise import extract_numeric_promise
from omega.domain.research import (
    DiscoveryCandidate,
    ExtractedResearchDocument,
    PrimarySourceStatus,
    ResearchCoverageStopReason,
    ResearchRequestStatus,
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


class ChunkedDiscoveryProvider:
    """Discovery provider that returns consecutive chunks of candidates across queries."""

    def __init__(self, candidates: list[DiscoveryCandidate], chunk_size: int = 5):
        self._chunks = [candidates[i : i + chunk_size] for i in range(0, len(candidates), chunk_size)]
        self._call_idx = 0

    async def search(self, query: str, limit: int = 5, filters: dict[str, Any] | None = None) -> list[DiscoveryCandidate]:
        if self._call_idx < len(self._chunks):
            res = self._chunks[self._call_idx][:limit]
            self._call_idx += 1
            return res
        return []

    async def close(self) -> None:
        pass


def make_test_fixture(
    topic_title: str = CANONICAL_TOPIC,
    acquisition_mode: str = "AUTOMATIC_SEARCH",
    max_sources: int = 15,
    minimum_source_quality: float = 50.0,
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
        minimum_source_quality=minimum_source_quality,
        minimum_claim_confidence=60.0,
        metadata_={
            "acquisition_mode": acquisition_mode,
            **({"numeric_contract": contract.to_dict()} if contract else {}),
        },
    )
    req.topic_candidate = topic
    req.sources = []
    session = FakeAsyncSession(req=req, channel=channel, topic=topic)
    return session, req, channel_id, topic_id


# ── Test Cases ──


@pytest.mark.asyncio
async def test_early_low_quality_candidates_do_not_block_later_qualifying_sources():
    """Verify that when the first 5 candidates in a round are low-quality (< 50),
    they are rejected and do NOT consume the round budget. Later qualifying candidates (>= 50)
    in the same round are evaluated and accepted.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=15)

    # 5 low-quality candidates (score 40.5: no topic keywords, NullAuthorityProvider)
    low_candidates = [
        DiscoveryCandidate(
            canonical_url=f"https://lowqual.org/article-{i}",
            title=f"Unrelated Article {i}",
            publisher="LowQual Org",
            snippet="General construction tips and guidelines without specific technical keywords.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 6)
    ]
    low_docs = [
        ExtractedResearchDocument(
            canonical_url=f"https://lowqual.org/article-{i}",
            title=f"Unrelated Article {i}",
            publisher="LowQual Org",
            extracted_content="General construction tips and guidelines without specific technical keywords.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 6)
    ]

    # 5 qualifying candidates (score >= 50: matches topic keywords)
    high_candidates = [
        DiscoveryCandidate(
            canonical_url=f"https://highqual.org/mechanism-{i}",
            title=f"Concrete Cracks in Civil Engineering Guide {i}",
            publisher="HighQual Press",
            snippet=f"Detailed concrete cracks analysis in civil engineering explaining mechanism {i}.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 6)
    ]
    high_docs = [
        ExtractedResearchDocument(
            canonical_url=f"https://highqual.org/mechanism-{i}",
            title=f"Concrete Cracks in Civil Engineering Guide {i}",
            publisher="HighQual Press",
            extracted_content=f"Detailed concrete cracks analysis in civil engineering explaining mechanism {i}.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 6)
    ]

    all_candidates = low_candidates + high_candidates
    all_docs = low_docs + high_docs

    provider = ChunkedDiscoveryProvider(candidates=all_candidates)
    extractor = InMemoryResearchContentExtractor(seeded_documents=all_docs)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=15,
        max_rounds=1,
    )

    # Exactly 5 qualifying sources accepted
    assert len(session.sources) == 5
    for s in session.sources:
        assert s.quality_score >= 50.0
        assert "highqual.org" in s.url

    # Truth record shows 5 rejected for quality and 5 accepted
    truth = result["coverage_truth"][0]
    assert truth["sources_accepted"] == 5
    assert truth["sources_rejected"] == 5
    assert any("SOURCE_QUALITY_BELOW_MINIMUM" in r for r in truth["rejection_reasons"])


@pytest.mark.asyncio
async def test_round_with_eight_low_quality_and_five_qualifying():
    """Verify that a round with 8 low-quality and 5 qualifying candidates accepts exactly 5."""
    session, req, channel_id, topic_id = make_test_fixture(max_sources=15)

    low_candidates = [
        DiscoveryCandidate(
            canonical_url=f"https://unrelated.org/page-{i}",
            title=f"Page {i}",
            publisher="Generic Blog",
            snippet="Generic summary text.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 9)
    ]
    low_docs = [
        ExtractedResearchDocument(
            canonical_url=f"https://unrelated.org/page-{i}",
            title=f"Page {i}",
            publisher="Generic Blog",
            extracted_content="Generic summary text without engineering terms.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 9)
    ]

    high_candidates = [
        DiscoveryCandidate(
            canonical_url=f"https://engineering.org/crack-mech-{i}",
            title=f"Concrete Cracks Civil Engineering {i}",
            publisher="Engineering Org",
            snippet=f"Concrete cracks civil engineering detailed analysis for mechanism {i}.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 6)
    ]
    high_docs = [
        ExtractedResearchDocument(
            canonical_url=f"https://engineering.org/crack-mech-{i}",
            title=f"Concrete Cracks Civil Engineering {i}",
            publisher="Engineering Org",
            extracted_content=f"Concrete cracks civil engineering detailed analysis for distinct mechanism {i}.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 6)
    ]

    provider = ChunkedDiscoveryProvider(candidates=low_candidates + high_candidates)
    extractor = InMemoryResearchContentExtractor(seeded_documents=low_docs + high_docs)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=15,
        max_rounds=1,
    )

    assert len(session.sources) == 5
    assert all(s.quality_score >= 50.0 for s in session.sources)
    assert result["coverage_truth"][0]["sources_accepted"] == 5


@pytest.mark.asyncio
async def test_all_fifteen_candidates_low_quality_fails_closed():
    """Verify that if all 15 candidates in a round are low-quality, zero are persisted
    and execution stops fail-closed without fabricating progress.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=15)

    low_candidates = [
        DiscoveryCandidate(
            canonical_url=f"https://lowscore.org/item-{i}",
            title=f"Item {i}",
            publisher="Low Publisher",
            snippet="Item snippet lacking keywords.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 16)
    ]
    low_docs = [
        ExtractedResearchDocument(
            canonical_url=f"https://lowscore.org/item-{i}",
            title=f"Item {i}",
            publisher="Low Publisher",
            extracted_content="Item text lacking keywords.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 16)
    ]

    provider = ChunkedDiscoveryProvider(candidates=low_candidates)
    extractor = InMemoryResearchContentExtractor(seeded_documents=low_docs)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=15,
        max_rounds=1,
    )

    assert len(session.sources) == 0
    assert result["coverage_truth"][0]["sources_accepted"] == 0
    assert result["coverage_truth"][0]["sources_rejected"] == 15
    assert result["stop_reason"] == ResearchCoverageStopReason.NO_NEW_RELEVANT_SOURCES.value


@pytest.mark.asyncio
async def test_mixed_rejection_categories_observable():
    """Verify observable reporting across duplicates, extraction failure, and quality rejection."""
    session, req, channel_id, topic_id = make_test_fixture(max_sources=15)

    c_dup1 = DiscoveryCandidate(
        canonical_url="https://site.org/duplicate-url",
        title="Valid Title",
        publisher="Pub",
        snippet="Valid snippet content for testing.",
    )
    c_dup2 = DiscoveryCandidate(
        canonical_url="https://site.org/duplicate-url",
        title="Valid Title 2",
        publisher="Pub",
        snippet="Valid snippet content for testing 2.",
    )
    c_fail = DiscoveryCandidate(
        canonical_url="https://site.org/extraction-fail",
        title="Fail Page",
        publisher="Pub",
        snippet="Valid snippet content for testing fail.",
    )
    c_low = DiscoveryCandidate(
        canonical_url="https://site.org/low-quality",
        title="Low Quality",
        publisher="Pub",
        snippet="Valid snippet without keywords.",
    )
    c_good = DiscoveryCandidate(
        canonical_url="https://site.org/qualifying",
        title="Concrete Cracks Civil Engineering Good",
        publisher="Pub",
        snippet="Concrete cracks in civil engineering structural analysis.",
    )

    doc_low = ExtractedResearchDocument(
        canonical_url="https://site.org/low-quality",
        title="Low Quality",
        publisher="Pub",
        extracted_content="Text without keywords.",
    )
    doc_good = ExtractedResearchDocument(
        canonical_url="https://site.org/qualifying",
        title="Concrete Cracks Civil Engineering Good",
        publisher="Pub",
        extracted_content="Concrete cracks in civil engineering structural analysis.",
    )

    provider = InMemoryDiscoveryProvider(seeded_candidates=[c_dup1, c_dup2, c_fail, c_low, c_good])
    extractor = InMemoryResearchContentExtractor(
        seeded_documents=[doc_low, doc_good]  # c_fail has no document -> CONTENT_UNAVAILABLE
    )

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=15,
        max_rounds=1,
    )

    assert len(session.sources) == 1
    assert session.sources[0].url == "https://site.org/qualifying"

    reasons = result["coverage_truth"][0]["rejection_reasons"]
    assert any("DUPLICATE_URL" in r for r in reasons)
    assert any("CONTENT_UNAVAILABLE" in r for r in reasons)
    assert any("SOURCE_QUALITY_BELOW_MINIMUM" in r for r in reasons)


@pytest.mark.asyncio
async def test_prescore_equals_persisted_score():
    """Verify regression proving PRE_SCORE == PERSISTED_SCORE for the same source input."""
    session, req, channel_id, topic_id = make_test_fixture(max_sources=5)

    cand = DiscoveryCandidate(
        canonical_url="https://testpub.org/crack-mechanisms",
        title="Concrete Cracks Civil Engineering Mechanism",
        publisher="Test Publisher",
        snippet="Concrete cracks civil engineering text.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
    )
    doc = ExtractedResearchDocument(
        canonical_url="https://testpub.org/crack-mechanisms",
        title="Concrete Cracks Civil Engineering Mechanism",
        publisher="Test Publisher",
        extracted_content="Concrete cracks civil engineering text explaining mechanics.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
    )

    # Compute pre-score explicitly
    norm_url = normalize_url(cand.canonical_url)
    bounded_content = bound_excerpt(doc.extracted_content)
    pre_score, _, _, _ = calculate_source_quality(
        publisher=doc.publisher,
        url=norm_url,
        primary_source_status=doc.primary_source_status,
        published_at=doc.published_at,
        topic_keywords=req.topic_candidate.keywords,
        content_excerpt=bounded_content,
        authority_provider=None,
    )

    provider = InMemoryDiscoveryProvider(seeded_candidates=[cand])
    extractor = InMemoryResearchContentExtractor(seeded_documents=[doc])

    await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=5,
        max_rounds=1,
    )

    assert len(session.sources) == 1
    persisted = session.sources[0]
    assert persisted.quality_score == pre_score


@pytest.mark.asyncio
async def test_canary_fixture_replay_matching_live_failure_ordering():
    """Replay representative quality ordering from the real P0.3c canary:
    Early candidates have scores ~40.5, 44.5 (< 50).
    Candidate 4 has score 52.5 (specchem.com equivalent).
    Candidates 5..10 have scores ~40.5 (< 50).
    Candidates 11..14 have scores >= 50.

    Prove that the repaired orchestrator does NOT stop after the first 5 candidates,
    rejects sub-50 candidates, and persists evidence-eligible sources up to the round limit.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=15)

    # 3 sub-50 candidates (matching scribd, mdpi, gilson in canary)
    c1 = DiscoveryCandidate(
        canonical_url="https://scribd.com/source-1",
        title="Scribd Document",
        publisher="scribd.com",
        snippet="Snippet lacking topic keywords.",
    )
    d1 = ExtractedResearchDocument(
        canonical_url="https://scribd.com/source-1",
        title="Scribd Document",
        publisher="scribd.com",
        extracted_content="Snippet lacking topic keywords.",
    )

    c2 = DiscoveryCandidate(
        canonical_url="https://mdpi.com/article-2",
        title="MDPI Article",
        publisher="mdpi.com",
        snippet="Article lacking topic keywords.",
    )
    d2 = ExtractedResearchDocument(
        canonical_url="https://mdpi.com/article-2",
        title="MDPI Article",
        publisher="mdpi.com",
        extracted_content="Article lacking topic keywords.",
    )

    c3 = DiscoveryCandidate(
        canonical_url="https://globalgilson.com/blog-3",
        title="Gilson Overview",
        publisher="globalgilson.com",
        snippet="Concrete overview with one keyword.",
    )
    d3 = ExtractedResearchDocument(
        canonical_url="https://globalgilson.com/blog-3",
        title="Gilson Overview",
        publisher="globalgilson.com",
        extracted_content="Concrete overview text with only concrete mentioned.",
    )

    # 1 qualifying candidate (matching specchem with 52.5)
    c4 = DiscoveryCandidate(
        canonical_url="https://specchem.com/understanding-concrete-cracking",
        title="Understanding Concrete Cracking",
        publisher="specchem.com",
        snippet="Concrete cracks in civil engineering explained.",
    )
    d4 = ExtractedResearchDocument(
        canonical_url="https://specchem.com/understanding-concrete-cracking",
        title="Understanding Concrete Cracking",
        publisher="specchem.com",
        extracted_content="Concrete cracks in civil engineering explained in depth.",
    )

    # 5 more sub-50 candidates (polito, jackson, usbr, etc.)
    sub_50_cands = [
        DiscoveryCandidate(
            canonical_url=f"https://sub50-{i}.org/page",
            title=f"Sub50 Page {i}",
            publisher=f"sub50-{i}.org",
            snippet="Technical paper without target keywords.",
        )
        for i in range(5, 10)
    ]
    sub_50_docs = [
        ExtractedResearchDocument(
            canonical_url=f"https://sub50-{i}.org/page",
            title=f"Sub50 Page {i}",
            publisher=f"sub50-{i}.org",
            extracted_content="Technical paper without target keywords.",
        )
        for i in range(5, 10)
    ]

    # 4 more qualifying candidates (>= 50)
    qual_cands = [
        DiscoveryCandidate(
            canonical_url=f"https://qualifying-{i}.org/concrete-cracks-guide",
            title=f"Civil Engineering Concrete Cracks Guide {i}",
            publisher=f"qualifying-{i}.org",
            snippet=f"Concrete cracks in civil engineering guide {i}.",
        )
        for i in range(10, 14)
    ]
    qual_docs = [
        ExtractedResearchDocument(
            canonical_url=f"https://qualifying-{i}.org/concrete-cracks-guide",
            title=f"Civil Engineering Concrete Cracks Guide {i}",
            publisher=f"qualifying-{i}.org",
            extracted_content=f"Concrete cracks in civil engineering guide {i}.",
        )
        for i in range(10, 14)
    ]

    all_c = [c1, c2, c3, c4] + sub_50_cands + qual_cands
    all_d = [d1, d2, d3, d4] + sub_50_docs + qual_docs

    provider = ChunkedDiscoveryProvider(candidates=all_c)
    extractor = InMemoryResearchContentExtractor(seeded_documents=all_d)

    await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=15,
        max_rounds=1,
    )

    # All 5 accepted sources are qualifying (specchem + 4 more)
    assert len(session.sources) == 5
    for s in session.sources:
        assert s.quality_score >= 50.0

    urls = [s.url for s in session.sources]
    assert "https://specchem.com/understanding-concrete-cracking" in urls
    assert all("sub50" not in u for u in urls)
    assert "https://scribd.com/source-1" not in urls
    assert "https://mdpi.com/article-2" not in urls


@pytest.mark.asyncio
async def test_total_persisted_sources_never_exceeds_budget_cap():
    """Verify that total automatic persisted sources never exceeds max_total_acquired_sources (15)."""
    session, req, channel_id, topic_id = make_test_fixture(max_sources=20)

    # 40 qualifying candidates across rounds
    cands = [
        DiscoveryCandidate(
            canonical_url=f"https://qualpub.org/source-{i}",
            title=f"Civil Engineering Concrete Cracks {i}",
            publisher="Qual Pub",
            snippet=f"Concrete cracks civil engineering text {i}.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 41)
    ]
    docs = [
        ExtractedResearchDocument(
            canonical_url=f"https://qualpub.org/source-{i}",
            title=f"Civil Engineering Concrete Cracks {i}",
            publisher="Qual Pub",
            extracted_content=f"Concrete cracks civil engineering detailed mechanism text number {i}.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 41)
    ]

    provider = ChunkedDiscoveryProvider(candidates=cands)
    extractor = InMemoryResearchContentExtractor(seeded_documents=docs)

    await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=15,
        max_rounds=4,
    )

    assert len(session.sources) == 15
    assert all(s.quality_score >= 50.0 for s in session.sources)


@pytest.mark.asyncio
async def test_request_max_sources_lower_than_configured_cap_wins():
    """Verify that request.max_sources wins when lower than max_total_acquired_sources."""
    session, req, channel_id, topic_id = make_test_fixture(max_sources=3)

    cands = [
        DiscoveryCandidate(
            canonical_url=f"https://engineering.org/doc-{i}",
            title=f"Concrete Cracks Civil Engineering {i}",
            publisher="Engineering Org",
            snippet=f"Concrete cracks civil engineering text {i}.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 10)
    ]
    docs = [
        ExtractedResearchDocument(
            canonical_url=f"https://engineering.org/doc-{i}",
            title=f"Concrete Cracks Civil Engineering {i}",
            publisher="Engineering Org",
            extracted_content=f"Concrete cracks civil engineering detailed mechanism text number {i}.",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
        )
        for i in range(1, 10)
    ]

    provider = ChunkedDiscoveryProvider(candidates=cands)
    extractor = InMemoryResearchContentExtractor(seeded_documents=docs)

    await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=15,
        max_rounds=2,
    )

    assert len(session.sources) == 3


@pytest.mark.asyncio
async def test_low_quality_source_never_contributes_evidence_and_high_quality_flows():
    """Verify that a low-quality candidate is rejected before persistence,
    never produces claims or evidence, while high-quality source flows to claim extraction.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=5)

    c_low = DiscoveryCandidate(
        canonical_url="https://lowqual.org/bad-source",
        title="Bad Source Without Keywords",
        publisher="LowQual",
        snippet="Irrelevant construction text.",
    )
    d_low = ExtractedResearchDocument(
        canonical_url="https://lowqual.org/bad-source",
        title="Bad Source Without Keywords",
        publisher="LowQual",
        extracted_content="Plastic shrinkage happens when evaporation exceeds bleed rate.",
    )

    c_high = DiscoveryCandidate(
        canonical_url="https://highqual.org/good-source",
        title="Concrete Cracks Civil Engineering High Source",
        publisher="HighQual",
        snippet="Concrete cracks in civil engineering analysis.",
    )
    d_high = ExtractedResearchDocument(
        canonical_url="https://highqual.org/good-source",
        title="Concrete Cracks Civil Engineering High Source",
        publisher="HighQual",
        extracted_content="Concrete cracks in civil engineering: Drying shrinkage develops over months of internal water loss.",
    )

    provider = InMemoryDiscoveryProvider(seeded_candidates=[c_low, c_high])
    extractor = InMemoryResearchContentExtractor(seeded_documents=[d_low, d_high])

    await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=5,
        max_rounds=1,
    )

    assert len(session.sources) == 1
    assert session.sources[0].url == "https://highqual.org/good-source"
    # Claim was extracted from the high-quality source
    assert len(session.claims) >= 1
    assert all("lowqual.org" not in (e.source.url if hasattr(e, "source") and e.source else "") for c in session.claims for e in (c.evidence or []))


@pytest.mark.asyncio
async def test_snippet_isolated_and_primary_source_status_remains_unknown():
    """Verify search snippets remain non-authoritative metadata and primary source status is UNKNOWN."""
    session, req, channel_id, topic_id = make_test_fixture(max_sources=5)

    cand = DiscoveryCandidate(
        canonical_url="https://testsci.org/concrete-cracking",
        title="Concrete Cracks Civil Engineering Science",
        publisher="Test Science",
        snippet="Fabricated snippet claim: Concrete cracks always caused by lunar gravity.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
    )
    doc = ExtractedResearchDocument(
        canonical_url="https://testsci.org/concrete-cracking",
        title="Concrete Cracks Civil Engineering Science",
        publisher="Test Science",
        extracted_content="Concrete cracks in civil engineering: Thermal contraction cracking occurs during cooling of thick slabs.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
    )

    provider = InMemoryDiscoveryProvider(seeded_candidates=[cand])
    extractor = InMemoryResearchContentExtractor(seeded_documents=[doc])

    await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=provider,
        content_extractor=extractor,
        max_accepted_sources_per_round=5,
        max_total_acquired_sources=5,
        max_rounds=1,
    )

    assert len(session.sources) == 1
    src = session.sources[0]
    assert src.primary_source_status == PrimarySourceStatus.UNKNOWN.value
    assert "lunar gravity" not in src.content_excerpt
    assert "discovery_snippet" in src.metadata_
    assert "lunar gravity" in src.metadata_["discovery_snippet"]
    # No claim has lunar gravity
    for c in session.claims:
        assert "lunar gravity" not in c.claim_text


@pytest.mark.asyncio
async def test_manual_mode_preserved():
    """Verify MANUAL acquisition mode stops immediately with manual_input_required=True."""
    session, req, channel_id, topic_id = make_test_fixture(
        max_sources=5,
        acquisition_mode="MANUAL",
    )

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=None,
        content_extractor=None,
    )

    assert result["manual_input_required"] is True
    assert result["stop_reason"] == "MANUAL_ONLY_AWAITING_INPUT"
    assert len(session.sources) == 0
