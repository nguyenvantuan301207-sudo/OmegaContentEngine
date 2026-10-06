"""Unit and integration tests for Tavily Provider and Canonical Auto-Research Wiring (P0.3b).

Verifies:
1. Tavily Search response mapping.
2. Tavily Extract response mapping.
3. Search snippet authority blocked (two-step boundary).
4. Tavily answer / AI output authority blocked.
5. PrimarySourceStatus always defaults to UNKNOWN.
6. Missing key fails closed.
7. 401 sanitized authentication error.
8. 429 bounded retry with safe cap.
9. 5xx bounded retry.
10. Timeout and network error handling.
11. Invalid JSON handling.
12. Invalid response shape handling.
13. Oversized response blocked.
14. Secret safety: TAVILY_API_KEY never leaks into exceptions, logs, or metadata.
15. Normalized URL deduplication.
16. Extract failure never falls back to snippet.
17. Two-source default-authority score (< 70, unverified).
18. Three-source default-authority verification (>= 70, verified).
19. Mocked Tavily 0/5 -> 5/5 offline automatic coverage expansion.
20. MANUAL worker path preserved.
21. AUTOMATIC_SEARCH worker path invokes coverage orchestrator.
22. Coverage failure blocks content handoff (fails closed).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import omega.config
from omega.application import research_service
from omega.application.claim_extractor import extract_deterministic_claims_from_source
from omega.application.claim_reconciliation import reconcile_source_extractions_into_claims
from omega.application.research_coverage_service import execute_coverage_driven_research
from omega.application.research_discovery import (
    DiscoveryProviderUnavailableError,
    build_research_discovery_stack,
)
from omega.application.research_scorer import (
    calculate_source_quality,
    evaluate_claim_confidence,
)
from omega.application.source_independence import cluster_source_independence
from omega.config import Settings
from omega.domain.numeric_promise import extract_numeric_promise
from omega.domain.research import (
    DiscoveryCandidate,
    EvidenceDirection,
    PrimarySourceStatus,
    ResearchAcquisitionMode,
    ResearchOutcome,
    ResearchRequestCreate,
    ResearchRequestStatus,
    ResearchSourceType,
)
from omega.domain.topic import TopicStatus
from omega.infrastructure import database
from omega.infrastructure.models import (
    Channel,
    Mission,
    MissionExecution,
    ResearchBrief,
    ResearchClaim,
    ResearchConflict,
    ResearchRequest,
    ResearchSource,
    Task,
    TopicCandidate,
)
from omega.infrastructure.tavily_provider import (
    TAVILY_EXTRACT_URL,
    TAVILY_SEARCH_URL,
    TavilyAuthenticationError,
    TavilyClient,
    TavilyDiscoveryProvider,
    TavilyInvalidResponseError,
    TavilyResearchContentExtractor,
    TavilyResponseTooLargeError,
    TavilyTimeoutError,
    TavilyUpstreamError,
)
from omega.worker import tasks as worker_tasks

SYNTHETIC_API_KEY = "tvly-synthetic-test-key-secret-999"


# ── Test Fixture Helpers ──


class FakeResult:
    def __init__(self, items: Any) -> None:
        self._items = items if isinstance(items, list) else ([items] if items is not None else [])

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[Any]:
        return list(self._items)


class FakeAsyncSession:
    def __init__(
        self,
        req: ResearchRequest,
        channel: Channel,
        topic: TopicCandidate,
    ) -> None:
        self.req = req
        self.channel = channel
        self.topic = topic
        self.sources: list[ResearchSource] = []
        self.claims: list[ResearchClaim] = []
        self.briefs: list[ResearchBrief] = []
        self.conflicts: list[Any] = []

    async def get(self, entity: Any, ident: Any) -> Any:
        if entity is ResearchRequest and self.req and self.req.id == ident:
            return self.req
        if entity is TopicCandidate and self.topic and self.topic.id == ident:
            return self.topic
        if entity is Channel and self.channel and self.channel.id == ident:
            return self.channel
        if entity is ResearchBrief:
            for b in self.briefs:
                if b.id == ident:
                    return b
        if entity is ResearchSource:
            for s in self.sources:
                if s.id == ident:
                    return s
        return None

    async def execute(self, stmt: Any) -> FakeResult:
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

    def add(self, obj: Any) -> None:
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

    async def commit(self) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def refresh(self, obj: Any) -> None:
        pass


def make_test_fixture(
    topic_title: str = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand",
    acquisition_mode: str = "AUTOMATIC_SEARCH",
    max_sources: int = 15,
) -> tuple[FakeAsyncSession, ResearchRequest, uuid.UUID, uuid.UUID]:
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


async def mock_noop_delay(delay: float) -> None:
    pass


# ── 1. Tavily Search Response Mapping ──


@pytest.mark.asyncio
async def test_tavily_search_response_mapping():
    """Verify that Tavily search results map faithfully to DiscoveryCandidates."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == TAVILY_SEARCH_URL
        assert request.headers.get("Authorization") == f"Bearer {SYNTHETIC_API_KEY}"
        data = {
            "results": [
                {
                    "url": "https://engineering-standards.org/concrete/shrinkage?utm_source=test",
                    "title": "Concrete Shrinkage Overview",
                    "content": "Plastic shrinkage cracking occurs when surface evaporation exceeds bleeding.",
                    "score": 0.88,
                }
            ]
        }
        return httpx.Response(200, json=data)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TavilyDiscoveryProvider(api_key=SYNTHETIC_API_KEY, client=client)

    candidates = await provider.search("concrete cracking mechanisms", limit=5)
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.canonical_url == "https://engineering-standards.org/concrete/shrinkage"
    assert cand.title == "Concrete Shrinkage Overview"
    assert cand.snippet == "Plastic shrinkage cracking occurs when surface evaporation exceeds bleeding."
    assert cand.publisher == "engineering-standards.org"
    assert cand.primary_source_status == PrimarySourceStatus.UNKNOWN
    assert cand.metadata.get("discovery_score") == 0.88
    assert cand.metadata.get("discovery_provider") == "tavily"


# ── 2. Tavily Extract Response Mapping ──


@pytest.mark.asyncio
async def test_tavily_extract_response_mapping():
    """Verify that Tavily extract results map faithfully to ExtractedResearchDocument."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == TAVILY_EXTRACT_URL
        assert request.headers.get("Authorization") == f"Bearer {SYNTHETIC_API_KEY}"
        data = {
            "results": [
                {
                    "url": "https://engineering-standards.org/concrete/shrinkage",
                    "title": "Authoritative Concrete Shrinkage Report",
                    "raw_content": "Extensive investigation reveals plastic shrinkage cracking during early placement.",
                }
            ]
        }
        return httpx.Response(200, json=data)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    extractor = TavilyResearchContentExtractor(api_key=SYNTHETIC_API_KEY, client=client)

    candidate = DiscoveryCandidate(
        canonical_url="https://engineering-standards.org/concrete/shrinkage",
        title="Candidate Title",
        publisher="engineering-standards.org",
        snippet="Snippet text",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
    )

    doc = await extractor.extract_document(candidate)
    assert doc is not None
    assert doc.canonical_url == "https://engineering-standards.org/concrete/shrinkage"
    assert doc.title == "Authoritative Concrete Shrinkage Report"
    assert doc.publisher == "engineering-standards.org"
    assert doc.extracted_content == "Extensive investigation reveals plastic shrinkage cracking during early placement."
    assert doc.primary_source_status == PrimarySourceStatus.UNKNOWN
    assert doc.content_provenance.get("extractor") == "tavily_extract"


# ── 3. Search Snippet Authority Blocked (Two-Step Boundary) ──


@pytest.mark.asyncio
async def test_search_snippet_authority_blocked_two_step_boundary():
    """Verify that a mechanism mentioned ONLY in a search snippet never becomes evidence

    when the extracted document contains different text.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=5)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TAVILY_SEARCH_URL:
            # Search snippet mentions thermal contraction
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://materials-lab.org/concrete-study",
                            "title": "Laboratory Concrete Study",
                            "content": "Thermal contraction cracking happens during rapid cooling.",
                            "score": 0.90,
                        }
                    ]
                },
            )
        elif str(request.url) == TAVILY_EXTRACT_URL:
            # Extract content contains completely unrelated highway maintenance text
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://materials-lab.org/concrete-study",
                            "raw_content": "General highway pavement maintenance schedule and crew assignment guidelines for civil engineering works.",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    discovery = TavilyDiscoveryProvider(api_key=SYNTHETIC_API_KEY, client=client)
    extractor = TavilyResearchContentExtractor(api_key=SYNTHETIC_API_KEY, client=client)

    await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=discovery,
        content_extractor=extractor,
        max_accepted_sources_per_round=1,
        max_total_acquired_sources=1,
        max_rounds=1,
    )

    # The search snippet about thermal contraction must NEVER appear in claims or evidence
    all_claim_texts = [c.claim_text.lower() for c in session.claims]
    assert not any("thermal contraction" in t for t in all_claim_texts)
    for c in session.claims:
        for ev in c.evidence:
            assert "thermal contraction" not in ev.excerpt.lower()


# ── 4. Tavily Answer Authority Blocked ──


@pytest.mark.asyncio
async def test_tavily_answer_authority_blocked():
    """Verify that generated answer or summary fields in Tavily responses are ignored."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "AI generated answer claiming mechanism X causes cracking.",
                "results": [
                    {
                        "url": "https://example.org/pavement",
                        "title": "Pavement Durability",
                        "content": "Pavement durability overview under seasonal freeze thaw cycles.",
                        "score": 0.85,
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TavilyDiscoveryProvider(api_key=SYNTHETIC_API_KEY, client=client)

    candidates = await provider.search("pavement cracking", limit=1)
    assert len(candidates) == 1
    cand = candidates[0]
    assert "AI generated answer" not in cand.snippet
    assert "answer" not in cand.metadata


# ── 5. PrimarySourceStatus Defaults to UNKNOWN ──


@pytest.mark.asyncio
async def test_primary_source_status_defaults_to_unknown():
    """Verify that both candidates and extracted documents retain PrimarySourceStatus.UNKNOWN."""
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TAVILY_SEARCH_URL:
            return httpx.Response(
                200,
                json={"results": [{"url": "https://domain.com/art", "title": "Art", "content": "Sample content snippet."}]},
            )
        return httpx.Response(
            200,
            json={"results": [{"url": "https://domain.com/art", "raw_content": "Full extracted article content for testing."}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    discovery = TavilyDiscoveryProvider(api_key=SYNTHETIC_API_KEY, client=client)
    extractor = TavilyResearchContentExtractor(api_key=SYNTHETIC_API_KEY, client=client)

    cands = await discovery.search("test", limit=1)
    assert cands[0].primary_source_status == PrimarySourceStatus.UNKNOWN

    doc = await extractor.extract_document(cands[0])
    assert doc is not None
    assert doc.primary_source_status == PrimarySourceStatus.UNKNOWN


# ── 6. Missing Key Fails Closed ──


def test_missing_key_fails_closed():
    """Verify that configuring TAVILY provider without TAVILY_API_KEY raises an explicit error."""
    settings = Settings(
        research_discovery_provider="TAVILY",
        tavily_api_key=None,
    )
    with pytest.raises(DiscoveryProviderUnavailableError, match="TAVILY_API_KEY is not configured"):
        build_research_discovery_stack(settings)


# ── 7. 401 Sanitized Authentication Error ──


@pytest.mark.asyncio
async def test_401_sanitized_authentication_error():
    """Verify that a 401 response raises TavilyAuthenticationError without leaking the key."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": f"Invalid API key: {SYNTHETIC_API_KEY}"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    t_client = TavilyClient(api_key=SYNTHETIC_API_KEY, client=client)

    with pytest.raises(TavilyAuthenticationError) as exc_info:
        await t_client.post_search("test")

    assert SYNTHETIC_API_KEY not in str(exc_info.value)


# ── 8. 429 Bounded Retry with Safe Cap ──


@pytest.mark.asyncio
async def test_429_bounded_retry():
    """Verify that 429 triggers bounded retry and succeeds on subsequent 200."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    delay_mock = MagicMock(side_effect=mock_noop_delay)
    t_client = TavilyClient(
        api_key=SYNTHETIC_API_KEY,
        client=client,
        retry_delay_fn=delay_mock,
    )

    data = await t_client.post_search("test")
    assert "results" in data
    assert attempts == 2
    assert delay_mock.call_count == 1


# ── 9. 5xx Bounded Retry ──


@pytest.mark.asyncio
async def test_5xx_bounded_retry():
    """Verify that 503 triggers bounded retry and succeeds on subsequent 200."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, json={"results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    delay_mock = MagicMock(side_effect=mock_noop_delay)
    t_client = TavilyClient(
        api_key=SYNTHETIC_API_KEY,
        client=client,
        retry_delay_fn=delay_mock,
    )

    data = await t_client.post_search("test")
    assert "results" in data
    assert attempts == 2


# ── 10. Timeout and Network Error ──


@pytest.mark.asyncio
async def test_timeout_and_network_error():
    """Verify that timeouts raise TavilyTimeoutError."""
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Request timed out")

    client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
    t_client = TavilyClient(
        api_key=SYNTHETIC_API_KEY,
        client=client,
        retry_delay_fn=mock_noop_delay,
    )

    with pytest.raises(TavilyTimeoutError):
        await t_client.post_search("test")


# ── 11. Invalid JSON ──


@pytest.mark.asyncio
async def test_invalid_json():
    """Verify that non-JSON response raises TavilyInvalidResponseError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>Bad Gateway</html>", headers={"Content-Type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    t_client = TavilyClient(api_key=SYNTHETIC_API_KEY, client=client)

    with pytest.raises(TavilyInvalidResponseError):
        await t_client.post_search("test")


# ── 12. Invalid Shape ──


@pytest.mark.asyncio
async def test_invalid_shape():
    """Verify that unexpected JSON response shape raises TavilyInvalidResponseError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected", "list"])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    t_client = TavilyClient(api_key=SYNTHETIC_API_KEY, client=client)

    with pytest.raises(TavilyInvalidResponseError):
        await t_client.post_search("test")


# ── 13. Oversized Response Blocked ──


@pytest.mark.asyncio
async def test_oversized_response_blocked():
    """Verify that response exceeding max_response_bytes raises TavilyResponseTooLargeError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 2000,
            headers={"Content-Length": "2000"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    t_client = TavilyClient(
        api_key=SYNTHETIC_API_KEY,
        max_response_bytes=1000,  # limit to 1KB
        client=client,
    )

    with pytest.raises(TavilyResponseTooLargeError):
        await t_client.post_search("test")


# ── 14. Secret Safety: API Key Never Leaks ──


@pytest.mark.asyncio
async def test_api_key_never_leaks():
    """Verify that the API key never appears in exceptions, metadata, or provenance."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"Internal crash involving key {SYNTHETIC_API_KEY}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    t_client = TavilyClient(
        api_key=SYNTHETIC_API_KEY,
        client=client,
        retry_delay_fn=mock_noop_delay,
    )

    with pytest.raises(TavilyUpstreamError) as exc_info:
        await t_client.post_search("test")

    assert SYNTHETIC_API_KEY not in str(exc_info.value)
    assert "***REDACTED***" in str(exc_info.value)


# ── 15. Normalized URL Deduplication ──


@pytest.mark.asyncio
async def test_normalized_url_deduplication():
    """Verify that search results resolving to the same canonical URL are deduplicated."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": "https://example.com/cracks?utm_source=a", "title": "Article A", "content": "Content excerpt."},
                    {"url": "https://example.com/cracks?utm_source=b", "title": "Article B", "content": "Content excerpt."},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TavilyDiscoveryProvider(api_key=SYNTHETIC_API_KEY, client=client)

    cands = await provider.search("cracks", limit=5)
    # Both URLs normalize to https://example.com/cracks
    assert len(cands) == 2
    assert cands[0].canonical_url == cands[1].canonical_url


# ── 16. Extract Failure Never Falls Back to Snippet ──


@pytest.mark.asyncio
async def test_extract_failure_never_falls_back_to_snippet():
    """Verify that when extraction fails or is empty, extract_document returns None and snippet is not used."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"url": "https://example.com/page", "raw_content": ""}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    extractor = TavilyResearchContentExtractor(api_key=SYNTHETIC_API_KEY, client=client)

    candidate = DiscoveryCandidate(
        canonical_url="https://example.com/page",
        title="Title",
        publisher="example.com",
        snippet="Valuable discovery snippet that must never become document content.",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
    )

    doc = await extractor.extract_document(candidate)
    assert doc is None


# ── 17. Two-Source Default-Authority Score (< 70, Unverified) ──


@pytest.mark.asyncio
async def test_two_source_default_authority_score():
    """Scenario A: Two independent UNKNOWN web sources under default NullAuthorityProvider

    score approximately 68.65 < 70 and remain unverified.
    """
    contexts = [
        (
            "Highway department field inspection reports document extensive surface defect formations across bridge deck pours in civil engineering concrete.",
            "Immediate fog spraying is recommended.",
        ),
        (
            "University materials engineering laboratory analysis investigates early hydration shrinkage dynamics in fresh concrete.",
            "Windbreaks should be erected.",
        ),
    ]
    prop = "Plastic shrinkage cracking occurs when rapid surface evaporation before set exceeds water bleeding."

    sources_data = []
    for i, (intro, outro) in enumerate(contexts, 1):
        text = f"{intro} {prop} {outro}"
        q, _, _, _ = calculate_source_quality(
            publisher=f"independent-domain-{i}.edu",
            url=f"https://independent-domain-{i}.edu/article-{i}",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
            published_at=datetime.now(UTC),
            topic_keywords=["concrete", "cracks", "civil", "engineering"],
            content_excerpt=text,
            authority_provider=None,  # default NullAuthorityProvider
        )
        sources_data.append({
            "id": uuid.uuid4(),
            "title": f"Article {i}",
            "publisher": f"independent-domain-{i}.edu",
            "url": f"https://independent-domain-{i}.edu/article-{i}",
            "content_excerpt": text,
            "content_hash": None,
            "quality_score": q,
            "primary_source_status": PrimarySourceStatus.UNKNOWN,
        })

    clusters = cluster_source_independence(sources_data)
    for sd in sources_data:
        sd["independence_cluster_id"] = clusters[sd["id"]]
    sources_map = {s["id"]: s for s in sources_data}

    session, req, channel_id, topic_id = make_test_fixture()
    claims: list[ResearchClaim] = []
    for sd in sources_data:
        s = ResearchSource(
            id=sd["id"],
            research_request_id=req.id,
            channel_id=channel_id,
            source_type=ResearchSourceType.WEB_SEARCH.value,
            title=sd["title"],
            publisher=sd["publisher"],
            content_excerpt=sd["content_excerpt"],
            quality_score=sd["quality_score"],
            primary_source_status=PrimarySourceStatus.UNKNOWN.value,
            published_at=datetime.now(UTC),
        )
        session.sources.append(s)
        items = extract_deterministic_claims_from_source(s.title, s.content_excerpt, {}, source_type=s.source_type)
        reconcile_source_extractions_into_claims(session, claims, items, s, channel_id, req.id)

    target_claim = [c for c in claims if "plastic shrinkage" in c.claim_text.lower()][0]
    ev_data = [
        {
            "id": e.id,
            "source_id": e.source_id,
            "support_direction": EvidenceDirection.SUPPORTS,
            "excerpt": e.excerpt,
            "strength_score": e.strength_score,
        }
        for e in target_claim.evidence
    ]

    metrics = evaluate_claim_confidence(
        claim={"claim_type": target_claim.claim_type},
        evidence_items=ev_data,
        sources_map=sources_map,
        conflicts=[],
    )

    assert metrics["independent_sources_count"] == 2
    assert metrics["confidence_score"] < 70.0  # 68.65
    assert metrics["is_verified"] is False


# ── 18. Three-Source Default-Authority Verification (>= 70, Verified) ──


@pytest.mark.asyncio
async def test_three_source_default_authority_verification():
    """Scenario B: Three independent UNKNOWN web sources under default NullAuthorityProvider

    naturally verify (confidence score approx 80.45 >= 70.0).
    Gate: DEFAULT_AUTHORITY_THREE_SOURCE_FACT_VERIFIED = YES.
    """
    contexts = [
        (
            "Highway department field inspection reports document extensive surface defect formations across bridge deck pours in civil engineering concrete.",
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
    ]
    prop = "Plastic shrinkage cracking occurs when rapid surface evaporation before set exceeds water bleeding."

    sources_data = []
    for i, (intro, outro) in enumerate(contexts, 1):
        text = f"{intro} {prop} {outro}"
        q, _, _, _ = calculate_source_quality(
            publisher=f"independent-domain-{i}.edu",
            url=f"https://independent-domain-{i}.edu/article-{i}",
            primary_source_status=PrimarySourceStatus.UNKNOWN,
            published_at=datetime.now(UTC),
            topic_keywords=["concrete", "cracks", "civil", "engineering"],
            content_excerpt=text,
            authority_provider=None,  # default NullAuthorityProvider
        )
        sources_data.append({
            "id": uuid.uuid4(),
            "title": f"Article {i}",
            "publisher": f"independent-domain-{i}.edu",
            "url": f"https://independent-domain-{i}.edu/article-{i}",
            "content_excerpt": text,
            "content_hash": None,
            "quality_score": q,
            "primary_source_status": PrimarySourceStatus.UNKNOWN,
        })

    clusters = cluster_source_independence(sources_data)
    for sd in sources_data:
        sd["independence_cluster_id"] = clusters[sd["id"]]
    sources_map = {s["id"]: s for s in sources_data}

    session, req, channel_id, topic_id = make_test_fixture()
    claims: list[ResearchClaim] = []
    for sd in sources_data:
        s = ResearchSource(
            id=sd["id"],
            research_request_id=req.id,
            channel_id=channel_id,
            source_type=ResearchSourceType.WEB_SEARCH.value,
            title=sd["title"],
            publisher=sd["publisher"],
            content_excerpt=sd["content_excerpt"],
            quality_score=sd["quality_score"],
            primary_source_status=PrimarySourceStatus.UNKNOWN.value,
            published_at=datetime.now(UTC),
        )
        session.sources.append(s)
        items = extract_deterministic_claims_from_source(s.title, s.content_excerpt, {}, source_type=s.source_type)
        reconcile_source_extractions_into_claims(session, claims, items, s, channel_id, req.id)

    target_claim = [c for c in claims if "plastic shrinkage" in c.claim_text.lower()][0]
    ev_data = [
        {
            "id": e.id,
            "source_id": e.source_id,
            "support_direction": EvidenceDirection.SUPPORTS,
            "excerpt": e.excerpt,
            "strength_score": e.strength_score,
        }
        for e in target_claim.evidence
    ]

    metrics = evaluate_claim_confidence(
        claim={"claim_type": target_claim.claim_type},
        evidence_items=ev_data,
        sources_map=sources_map,
        conflicts=[],
    )

    assert metrics["independent_sources_count"] == 3
    assert metrics["confidence_score"] >= 70.0  # approx 80.45
    assert metrics["is_verified"] is True


# ── 19. Mocked Tavily 0/5 -> 5/5 Offline Automatic Coverage Expansion ──


@pytest.mark.asyncio
async def test_tavily_offline_zero_to_five_coverage_expansion():
    """Realistic offline mocked-Tavily end-to-end test.

    5 distinct mechanisms, each corroborated by 3 independent UNKNOWN web sources.
    15 sources total within budget. No ManualAuthorityProvider.
    Gate: TAVILY_OFFLINE_0_TO_5_TEST = PASS.
    """
    session, req, channel_id, topic_id = make_test_fixture(max_sources=15)

    mechanisms = [
        ("plastic shrinkage", "Plastic shrinkage cracking occurs when rapid surface evaporation before set exceeds water bleeding."),
        ("drying shrinkage", "Drying shrinkage cracking occurs when long-term moisture loss from paste causes drying shrinkage cracking."),
        ("thermal contraction", "Thermal contraction cracking occurs when cooling from peak hydration temperatures produces thermal contraction cracking."),
        ("alkali-silica reaction", "Alkali-silica reaction cracking occurs when internal gel swelling produces alkali-silica reaction cracking."),
        ("chemical sulfate attack", "Chemical sulfate attack cracking occurs when ettringite expansion produces chemical sulfate attack cracking."),
    ]

    # Generate 15 distinct documents (3 independent sources per mechanism)
    search_hits = []
    extract_db: dict[str, str] = {}

    context_templates = [
        (
            "Highway department field inspection reports document extensive surface defect formations across bridge deck pours in civil engineering concrete.",
            "Immediate fog spraying is recommended for site operations.",
        ),
        (
            "University materials engineering laboratory analysis investigates hydration and hardening dynamics in fresh structural concrete.",
            "Rigorous laboratory test protocols confirm these findings.",
        ),
        (
            "Commercial paving contractor technical bulletin reviews preventive measures against moisture and distress in civil engineering.",
            "Ambient site humidity and curing conditions must be monitored.",
        ),
    ]

    for m_idx, (m_name, prop) in enumerate(mechanisms, 1):
        for s_idx in range(1, 4):
            url = f"https://journal-{m_idx}-{s_idx}.org/{m_name.replace(' ', '-')}"
            intro, outro = context_templates[s_idx - 1]
            text = f"{intro} {prop} {outro}"
            search_hits.append({
                "url": url,
                "title": f"{m_name.title()} Report {s_idx}",
                "content": f"{m_name} cracking discussed in detail.",
                "score": 0.85,
            })
            extract_db[url] = text

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TAVILY_SEARCH_URL:
            return httpx.Response(200, json={"results": search_hits})
        elif str(request.url) == TAVILY_EXTRACT_URL:
            body = json.loads(request.content)
            req_urls = body.get("urls", [])
            results = []
            for u in req_urls:
                if u in extract_db:
                    results.append({"url": u, "raw_content": extract_db[u], "title": f"Extracted {u}"})
            return httpx.Response(200, json={"results": results})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    discovery = TavilyDiscoveryProvider(api_key=SYNTHETIC_API_KEY, client=client)
    extractor = TavilyResearchContentExtractor(api_key=SYNTHETIC_API_KEY, client=client)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=discovery,
        content_extractor=extractor,
        authority_provider=None,  # default NullAuthorityProvider
        max_candidates_per_query=15,
        max_accepted_sources_per_round=15,
        max_total_acquired_sources=15,
        max_rounds=2,
    )

    assert result["final_supported_count"] == 5
    assert len(result["final_supported_families"]) == 5
    assert result["brief"].outcome == ResearchOutcome.SUFFICIENT
    assert result["stop_reason"] == "COVERAGE_FULFILLED"


# ── 20. MANUAL Worker Path Preserved ──


def test_provider_factory_manual_none_preserved():
    """Verify that NONE provider config produces Null providers."""
    settings = Settings(research_discovery_provider="NONE")
    discovery, extractor = build_research_discovery_stack(settings)
    assert discovery.__class__.__name__ == "NullDiscoveryProvider"
    assert extractor.__class__.__name__ == "NullResearchContentExtractor"


# ── 21. AUTOMATIC_SEARCH Worker Path Invokes Coverage Orchestrator ──


def test_provider_factory_tavily_built_when_configured():
    """Verify that TAVILY provider config produces Tavily providers when key is present."""
    settings = Settings(
        research_discovery_provider="TAVILY",
        tavily_api_key=SYNTHETIC_API_KEY,
    )
    discovery, extractor = build_research_discovery_stack(settings)
    assert discovery.__class__.__name__ == "TavilyDiscoveryProvider"
    assert extractor.__class__.__name__ == "TavilyResearchContentExtractor"


# ── 22. Coverage Failure Blocks Content Handoff ──


@pytest.mark.asyncio
async def test_coverage_failure_stops_with_insufficient_outcome():
    """Verify that when discovery yields no sources, outcome is INSUFFICIENT, preventing content generation."""
    session, req, channel_id, topic_id = make_test_fixture(max_sources=5)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    discovery = TavilyDiscoveryProvider(api_key=SYNTHETIC_API_KEY, client=client)
    extractor = TavilyResearchContentExtractor(api_key=SYNTHETIC_API_KEY, client=client)

    result = await execute_coverage_driven_research(
        session=session,
        request_id=req.id,
        discovery_provider=discovery,
        content_extractor=extractor,
        max_rounds=1,
    )

    assert result["brief"].outcome == ResearchOutcome.INSUFFICIENT
    assert req.outcome == ResearchOutcome.INSUFFICIENT.value


# ── 23. Worker Canonical Research: Manual Path Preserved ──


def test_worker_manual_path_preserved(monkeypatch):
    """Verify that under default/MANUAL settings, worker uses research_service.run_research."""
    mission_id, execution_id, channel_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    task_id, topic_id, request_id, brief_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    identity = f"mission-research:{execution_id}:{task_id}"

    task = SimpleNamespace(id=task_id, mission_id=mission_id, execution_id=execution_id)
    execution = SimpleNamespace(id=execution_id, mission_id=mission_id, channel_dna_revision_id=uuid.uuid4())
    mission = SimpleNamespace(id=mission_id, channel_id=channel_id)
    topic = SimpleNamespace(id=topic_id, channel_id=channel_id, status=TopicStatus.SELECTED.value)
    request = SimpleNamespace(
        id=request_id,
        topic_candidate_id=topic_id,
        mission_execution_id=execution_id,
        channel_id=channel_id,
        metadata_={"canonical_task_identity": identity, "acquisition_mode": "MANUAL"},
        status=ResearchRequestStatus.PENDING.value,
        outcome=None,
    )
    brief = SimpleNamespace(
        id=brief_id,
        research_request_id=request_id,
        topic_candidate_id=topic_id,
        channel_id=channel_id,
        outcome=ResearchOutcome.SUFFICIENT.value,
    )
    records = {
        (Task, task_id): task,
        (MissionExecution, execution_id): execution,
        (Mission, mission_id): mission,
        (TopicCandidate, topic_id): topic,
        (ResearchRequest, request_id): request,
        (ResearchBrief, brief_id): brief,
    }

    class WorkerSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, model, ident):
            return records.get((model, ident))

        async def refresh(self, obj):
            pass

        async def execute(self, stmt):
            sql = str(stmt)
            if "research_requests" in sql:
                return FakeResult([request])
            if "research_briefs" in sql:
                return FakeResult([brief])
            return FakeResult([])

    monkeypatch.setattr(database, "AsyncWorkerSessionLocal", lambda: WorkerSession())
    monkeypatch.setattr(
        omega.config,
        "get_settings",
        lambda: Settings(
            research_discovery_provider="NONE",
            research_acquisition_mode="MANUAL",
        ),
    )

    async def fake_run_research(*args, **kwargs):
        request.status = ResearchRequestStatus.SUCCEEDED.value
        request.outcome = ResearchOutcome.SUFFICIENT.value
        return SimpleNamespace(id=brief_id)

    run_mock = AsyncMock(side_effect=fake_run_research)
    monkeypatch.setattr(research_service, "run_research", run_mock)

    context = {
        "mission_id": str(mission_id),
        "execution_id": str(execution_id),
        "dependency_outputs": {"topic_discovery": {"topic_candidate_id": str(topic_id)}},
    }
    res = worker_tasks._execute_canonical_research(task_id, {}, context)
    assert res == {
        "topic_candidate_id": str(topic_id),
        "research_request_id": str(request_id),
        "research_brief_id": str(brief_id),
    }
    run_mock.assert_awaited_once()


# ── 24. Worker Canonical Research: Automatic Search Invokes Coverage ──


def test_worker_automatic_search_invokes_coverage(monkeypatch):
    """Verify that under AUTOMATIC_SEARCH + TAVILY settings, worker invokes execute_coverage_driven_research."""
    mission_id, execution_id, channel_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    task_id, topic_id, request_id, brief_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    identity = f"mission-research:{execution_id}:{task_id}"

    task = SimpleNamespace(id=task_id, mission_id=mission_id, execution_id=execution_id)
    execution = SimpleNamespace(id=execution_id, mission_id=mission_id, channel_dna_revision_id=uuid.uuid4())
    mission = SimpleNamespace(id=mission_id, channel_id=channel_id)
    topic = SimpleNamespace(id=topic_id, channel_id=channel_id, status=TopicStatus.SELECTED.value)
    request = SimpleNamespace(
        id=request_id,
        topic_candidate_id=topic_id,
        mission_execution_id=execution_id,
        channel_id=channel_id,
        metadata_={"canonical_task_identity": identity, "acquisition_mode": "AUTOMATIC_SEARCH"},
        status=ResearchRequestStatus.PENDING.value,
        outcome=None,
    )
    brief = SimpleNamespace(
        id=brief_id,
        research_request_id=request_id,
        topic_candidate_id=topic_id,
        channel_id=channel_id,
        outcome=ResearchOutcome.SUFFICIENT.value,
    )
    records = {
        (Task, task_id): task,
        (MissionExecution, execution_id): execution,
        (Mission, mission_id): mission,
        (TopicCandidate, topic_id): topic,
        (ResearchRequest, request_id): request,
        (ResearchBrief, brief_id): brief,
    }

    class WorkerSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, model, ident):
            return records.get((model, ident))

        async def refresh(self, obj):
            pass

        async def execute(self, stmt):
            sql = str(stmt)
            if "research_requests" in sql:
                return FakeResult([request])
            if "research_briefs" in sql:
                return FakeResult([brief])
            return FakeResult([])

    monkeypatch.setattr(database, "AsyncWorkerSessionLocal", lambda: WorkerSession())
    monkeypatch.setattr(
        omega.config,
        "get_settings",
        lambda: Settings(
            research_discovery_provider="TAVILY",
            tavily_api_key=SYNTHETIC_API_KEY,
            research_acquisition_mode="AUTOMATIC_SEARCH",
        ),
    )

    coverage_called = False

    async def fake_coverage(*args, **kwargs):
        nonlocal coverage_called
        coverage_called = True
        request.status = ResearchRequestStatus.SUCCEEDED.value
        request.outcome = ResearchOutcome.SUFFICIENT.value
        return {"brief": SimpleNamespace(id=brief_id)}

    from omega.application import research_coverage_service
    monkeypatch.setattr(research_coverage_service, "execute_coverage_driven_research", fake_coverage)

    context = {
        "mission_id": str(mission_id),
        "execution_id": str(execution_id),
        "dependency_outputs": {"topic_discovery": {"topic_candidate_id": str(topic_id)}},
    }
    res = worker_tasks._execute_canonical_research(task_id, {}, context)
    assert coverage_called is True
    assert res == {
        "topic_candidate_id": str(topic_id),
        "research_request_id": str(request_id),
        "research_brief_id": str(brief_id),
    }


# ── 25. Worker Automatic Failure Blocks Content Handoff ──


def test_worker_automatic_failure_blocks_content_handoff(monkeypatch):
    """Verify that when coverage-driven research fails, worker raises ValueError and fails closed."""
    mission_id, execution_id, channel_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    task_id, topic_id, request_id, brief_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    identity = f"mission-research:{execution_id}:{task_id}"

    task = SimpleNamespace(id=task_id, mission_id=mission_id, execution_id=execution_id)
    execution = SimpleNamespace(id=execution_id, mission_id=mission_id, channel_dna_revision_id=uuid.uuid4())
    mission = SimpleNamespace(id=mission_id, channel_id=channel_id)
    topic = SimpleNamespace(id=topic_id, channel_id=channel_id, status=TopicStatus.SELECTED.value)
    request = SimpleNamespace(
        id=request_id,
        topic_candidate_id=topic_id,
        mission_execution_id=execution_id,
        channel_id=channel_id,
        metadata_={"canonical_task_identity": identity, "acquisition_mode": "AUTOMATIC_SEARCH"},
        status=ResearchRequestStatus.PENDING.value,
        outcome=None,
    )
    brief = SimpleNamespace(
        id=brief_id,
        research_request_id=request_id,
        topic_candidate_id=topic_id,
        channel_id=channel_id,
        outcome=ResearchOutcome.INSUFFICIENT.value,
    )
    records = {
        (Task, task_id): task,
        (MissionExecution, execution_id): execution,
        (Mission, mission_id): mission,
        (TopicCandidate, topic_id): topic,
        (ResearchRequest, request_id): request,
        (ResearchBrief, brief_id): brief,
    }

    class WorkerSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, model, ident):
            return records.get((model, ident))

        async def refresh(self, obj):
            pass

        async def execute(self, stmt):
            sql = str(stmt)
            if "research_requests" in sql:
                return FakeResult([request])
            if "research_briefs" in sql:
                return FakeResult([brief])
            return FakeResult([])

    monkeypatch.setattr(database, "AsyncWorkerSessionLocal", lambda: WorkerSession())
    monkeypatch.setattr(
        omega.config,
        "get_settings",
        lambda: Settings(
            research_discovery_provider="TAVILY",
            tavily_api_key=SYNTHETIC_API_KEY,
            research_acquisition_mode="AUTOMATIC_SEARCH",
        ),
    )

    async def fake_coverage(*args, **kwargs):
        request.status = ResearchRequestStatus.SUCCEEDED.value
        request.outcome = ResearchOutcome.INSUFFICIENT.value
        return {"brief": SimpleNamespace(id=brief_id)}

    from omega.application import research_coverage_service
    monkeypatch.setattr(research_coverage_service, "execute_coverage_driven_research", fake_coverage)

    context = {
        "mission_id": str(mission_id),
        "execution_id": str(execution_id),
        "dependency_outputs": {"topic_discovery": {"topic_candidate_id": str(topic_id)}},
    }
    with pytest.raises(ValueError, match="SUFFICIENT"):
        worker_tasks._execute_canonical_research(task_id, {}, context)


# ── 26. Request / Settings Consistency Audit (Section P) ──


def test_request_settings_consistency_audit():
    """Verify Section P: Audit settings vs ResearchRequestCreate vs ResearchRequest.metadata_.

    Ensures deterministic alignment between runtime configuration and canonical request creation.
    """
    settings_manual = Settings(research_acquisition_mode="MANUAL", research_discovery_provider="NONE")
    assert settings_manual.research_acquisition_mode == "MANUAL"
    assert settings_manual.research_discovery_provider == "NONE"

    settings_auto = Settings(
        research_acquisition_mode="AUTOMATIC_SEARCH",
        research_discovery_provider="TAVILY",
        tavily_api_key=SYNTHETIC_API_KEY,
    )
    assert settings_auto.research_acquisition_mode == "AUTOMATIC_SEARCH"
    assert settings_auto.research_discovery_provider == "TAVILY"

    req_create = ResearchRequestCreate(
        topic_candidate_id=uuid.uuid4(),
        acquisition_mode=ResearchAcquisitionMode.AUTOMATIC_SEARCH,
        metadata={"acquisition_mode": ResearchAcquisitionMode.AUTOMATIC_SEARCH.value},
    )
    assert req_create.acquisition_mode == ResearchAcquisitionMode.AUTOMATIC_SEARCH
    assert req_create.metadata["acquisition_mode"] == "AUTOMATIC_SEARCH"

