"""Research Discovery Provider abstraction, candidate filtering, and neutral test implementations.

Provides pluggable search candidate discovery without embedding third-party dependencies
or treating unverified search snippets as verified claims.
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlparse

from omega.application.source_normalizer import normalize_url
from omega.domain.research import (
    DiscoveryCandidate,
    ExtractedResearchDocument,
    ResearchSourceType,
)
from omega.logging import get_logger

logger = get_logger(service="omega-research-discovery")


class DiscoveryProviderError(Exception):
    """Base exception for discovery provider errors."""

    pass


class DiscoveryProviderUnavailableError(DiscoveryProviderError):
    """Raised when a discovery provider is unavailable, unconfigured, or failing."""

    pass


class ContentExtractionError(DiscoveryProviderError):
    """Base exception for research document content extraction errors."""

    pass


class ResearchDiscoveryProvider(Protocol):
    """Protocol for discovering candidate research sources for a query."""

    async def search(
        self,
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[DiscoveryCandidate]:
        """Execute a discovery query and return candidate sources.

        Note: Candidates represent prospective sources for normalization and ingestion.
        Discovery candidates MUST NOT bypass the normal evidence/claim verification pipeline.
        """
        ...


class ResearchContentExtractor(Protocol):
    """Protocol for extracting full document content from prospective sources."""

    async def extract_document(
        self,
        candidate: DiscoveryCandidate,
    ) -> ExtractedResearchDocument | None:
        """Extract full document for a candidate.

        Returns ExtractedResearchDocument on success, or None if extraction fails or is unavailable.
        """
        ...


class NullDiscoveryProvider:
    """Default neutral discovery provider that returns no results."""

    async def search(
        self,
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[DiscoveryCandidate]:
        return []


class NullResearchContentExtractor:
    """Default null content extractor that returns no extracted document."""

    async def extract_document(
        self,
        candidate: DiscoveryCandidate,
    ) -> ExtractedResearchDocument | None:
        return None


class InMemoryDiscoveryProvider:
    """Deterministic in-memory discovery provider for testing and controlled simulations."""

    def __init__(
        self,
        seeded_candidates: dict[str, list[DiscoveryCandidate]] | list[DiscoveryCandidate] | None = None,
        fail: bool = False,
        failure_message: str = "Configured test provider failure",
    ) -> None:
        self._seeded: dict[str, list[DiscoveryCandidate]] = {}
        self._global_pool: list[DiscoveryCandidate] = []
        if isinstance(seeded_candidates, dict):
            self._seeded = seeded_candidates
        elif isinstance(seeded_candidates, list):
            self._global_pool = list(seeded_candidates)
        self._fail = fail
        self._failure_message = failure_message
        self.call_history: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[DiscoveryCandidate]:
        self.call_history.append({"query": query, "limit": limit, "filters": filters})
        if self._fail:
            raise DiscoveryProviderUnavailableError(self._failure_message)

        # 1. Exact query match
        query_clean = query.strip().lower()
        if query_clean in self._seeded:
            return list(self._seeded[query_clean][:limit])

        # 2. Substring query match across seeded dict
        for key, candidates in self._seeded.items():
            if key in query_clean or query_clean in key:
                return list(candidates[:limit])

        # 3. Fallback to global pool if provided
        if self._global_pool:
            return list(self._global_pool[:limit])

        return []


class InMemoryResearchContentExtractor:
    """Deterministic in-memory content extractor for testing and controlled simulations."""

    def __init__(
        self,
        seeded_documents: dict[str, ExtractedResearchDocument] | list[ExtractedResearchDocument] | None = None,
        fail: bool = False,
        failure_message: str = "Configured extractor failure",
    ) -> None:
        self._seeded: dict[str, ExtractedResearchDocument] = {}
        if isinstance(seeded_documents, dict):
            for k, doc in seeded_documents.items():
                self._seeded[normalize_url(k)] = doc
        elif isinstance(seeded_documents, list):
            for doc in seeded_documents:
                self._seeded[normalize_url(doc.canonical_url)] = doc
        self._fail = fail
        self._failure_message = failure_message
        self.call_history: list[str] = []

    async def extract_document(
        self,
        candidate: DiscoveryCandidate,
    ) -> ExtractedResearchDocument | None:
        self.call_history.append(candidate.canonical_url)
        if self._fail:
            raise ContentExtractionError(self._failure_message)
        norm_u = normalize_url(candidate.canonical_url)
        return self._seeded.get(norm_u)


def filter_and_deduplicate_candidates(
    candidates: list[DiscoveryCandidate],
    already_seen_urls: set[str],
    max_accepted: int = 5,
) -> tuple[list[DiscoveryCandidate], list[str]]:
    """Deterministically filter and deduplicate discovery candidates.

    Filters for:
    - Unsupported URL scheme (only http/https allowed)
    - Duplicate normalized URL across previous rounds or current batch
    - Empty or low-information snippet (< 15 chars if present)
    - Short/invalid titles (< 3 chars)

    Returns:
        (accepted_candidates, rejection_reasons)
    """
    accepted: list[DiscoveryCandidate] = []
    reasons: list[str] = []
    seen_in_batch: set[str] = set()

    for c in candidates:
        if len(accepted) >= max_accepted:
            reasons.append(f"CAPPED_AT_MAX_ACCEPTED: '{c.canonical_url}'")
            continue

        raw_url = c.canonical_url.strip()
        parsed = urlparse(raw_url)
        if parsed.scheme.lower() not in ("http", "https"):
            reasons.append(f"UNSUPPORTED_SCHEME: '{c.canonical_url}'")
            continue

        norm_url = normalize_url(raw_url)
        if not norm_url:
            reasons.append(f"INVALID_URL: '{c.canonical_url}'")
            continue

        if norm_url in already_seen_urls or norm_url in seen_in_batch:
            reasons.append(f"DUPLICATE_URL: '{norm_url}'")
            continue

        if len(c.title.strip()) < 3:
            reasons.append(f"LOW_INFORMATION_TITLE: '{c.title}'")
            continue

        if c.snippet is not None and len(c.snippet.strip()) < 15:
            reasons.append(f"LOW_INFORMATION_SNIPPET: '{c.canonical_url}'")
            continue

        seen_in_batch.add(norm_url)
        accepted.append(c)

    return accepted, reasons


class ExternalSearchAdapterContract:
    """Specification contract for future third-party search integrations.

    Required Capabilities:
    1. Query execution returning structured search hits with title, URL, publisher domain,
       published timestamp, and representative content snippet.
    2. HTTP client resilience with timeout, rate limit handling, and structured error mapping.
    3. Strict credential isolation: API keys passed via runtime environment/settings,
       never printed in logs, never stored in DB metadata, never sent to frontend.
    """

    INTERFACE_VERSION = "1.0.0"
    SUPPORTED_SOURCE_TYPE = ResearchSourceType.WEB_SEARCH


def build_research_discovery_stack(
    settings: Any,
    client: Any = None,
    retry_delay_fn: Any = None,
) -> tuple[ResearchDiscoveryProvider, ResearchContentExtractor]:
    """Build canonical discovery and extraction stack according to runtime settings.

    Invariants:
    1. 'NONE' returns (NullDiscoveryProvider(), NullResearchContentExtractor()).
    2. 'TAVILY' builds (TavilyDiscoveryProvider, TavilyResearchContentExtractor) if key configured.
    3. 'TAVILY' without valid API key fails closed with DiscoveryProviderUnavailableError.
    4. Unsupported provider raises DiscoveryProviderUnavailableError.
    """
    provider_type = getattr(settings, "research_discovery_provider", "NONE")
    provider_str = (provider_type or "NONE").upper().strip()

    if provider_str == "NONE":
        return NullDiscoveryProvider(), NullResearchContentExtractor()

    if provider_str == "TAVILY":
        api_key = getattr(settings, "tavily_api_key", None)
        if not api_key or not str(api_key).strip():
            raise DiscoveryProviderUnavailableError(
                "TAVILY research discovery provider requested but TAVILY_API_KEY is not configured"
            )

        from omega.infrastructure.tavily_provider import (
            TavilyDiscoveryProvider,
            TavilyResearchContentExtractor,
        )

        search_depth = getattr(settings, "research_tavily_search_depth", "basic")
        extract_depth = getattr(settings, "research_tavily_extract_depth", "basic")
        timeout_seconds = getattr(settings, "research_tavily_timeout_seconds", 30.0)
        max_bytes = getattr(settings, "research_tavily_max_response_bytes", 5_000_000)

        discovery_provider = TavilyDiscoveryProvider(
            api_key=str(api_key).strip(),
            search_depth=search_depth,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_bytes,
            client=client,
            retry_delay_fn=retry_delay_fn,
        )
        content_extractor = TavilyResearchContentExtractor(
            api_key=str(api_key).strip(),
            extract_depth=extract_depth,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_bytes,
            client=client,
            retry_delay_fn=retry_delay_fn,
        )
        return discovery_provider, content_extractor

    raise DiscoveryProviderUnavailableError(
        f"Unsupported research discovery provider: '{provider_str}'"
    )
