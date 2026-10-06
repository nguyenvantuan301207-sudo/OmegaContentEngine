"""Tavily Search & Content Extraction Provider (P0.3b).

Implements Tavily as Omega's real automatic research discovery and content
extraction provider over direct HTTPS REST via httpx.

Invariants:
1. Search is for DISCOVERY only; search snippets never become canonical evidence.
2. Content acquisition is performed via Extract; Extract raw content is the evidence boundary.
3. Fixed-host provider boundary: all outbound requests target https://api.tavily.com strictly.
4. Response bodies and request timeouts are strictly bounded.
5. PrimarySourceStatus always defaults to UNKNOWN.
6. API keys are strictly redacted and never logged, returned, or persisted in metadata.
7. Bounded retries for 429 and transient 5xx errors; fail-closed on 401/403 and invalid responses.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from omega.application.research_discovery import (
    DiscoveryProviderError,
    DiscoveryProviderUnavailableError,
)
from omega.application.source_normalizer import normalize_url
from omega.domain.research import (
    DiscoveryCandidate,
    ExtractedResearchDocument,
    PrimarySourceStatus,
)
from omega.logging import get_logger

logger = get_logger(service="omega-tavily-provider")

TAVILY_HOST = "api.tavily.com"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RESPONSE_BYTES = 5_000_000  # 5MB safe bound
MAX_RETRIES = 2
MAX_RETRY_AFTER_SECONDS = 5.0


def derive_publisher_from_url(url: str) -> str:
    """Deterministically derive a publisher name from a URL hostname."""
    try:
        parsed = urlparse(url)
        netloc = (parsed.netloc or "").lower().strip()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        if ":" in netloc:
            netloc = netloc.split(":")[0]
        return netloc or "unknown"
    except Exception:
        return "unknown"


def _sanitize_secret(message: str, secret: str | None) -> str:
    """Sanitize any occurrence of secret from string message."""
    if not secret or not secret.strip():
        return message
    return message.replace(secret.strip(), "***REDACTED***")


# ── Error Hierarchy ──


class TavilyError(DiscoveryProviderError):
    """Base exception for Tavily provider operations."""

    def __init__(self, message: str, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class TavilyAuthenticationError(TavilyError):
    """401/403 authentication failure with sanitized error message."""

    def __init__(self, message: str = "Tavily authentication failed: invalid or unauthorized API key") -> None:
        super().__init__(message, error_code="AUTHENTICATION_FAILURE")


class TavilyRateLimitError(TavilyError):
    """429 rate limit exceeded."""

    def __init__(self, message: str = "Tavily API rate limit exceeded") -> None:
        super().__init__(message, error_code="RATE_LIMITED")


class TavilyUpstreamError(TavilyError):
    """Upstream 4xx/5xx HTTP error."""

    pass


class TavilyTimeoutError(TavilyError):
    """Request timeout."""

    def __init__(self, message: str = "Tavily request timed out") -> None:
        super().__init__(message, error_code="TIMEOUT")


class TavilyNetworkError(TavilyError):
    """Network connection failure."""

    def __init__(self, message: str = "Tavily network connection error") -> None:
        super().__init__(message, error_code="NETWORK_ERROR")


class TavilyInvalidResponseError(TavilyError):
    """Invalid JSON or invalid response shape."""

    pass


class TavilyResponseTooLargeError(TavilyError):
    """Response payload exceeded configured maximum bytes."""

    def __init__(self, message: str = "Tavily response exceeded maximum allowed bytes") -> None:
        super().__init__(message, error_code="RESPONSE_TOO_LARGE")


# ── REST Client ──


class TavilyClient:
    """Narrow, secure REST client strictly targeting https://api.tavily.com."""

    def __init__(
        self,
        api_key: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        client: httpx.AsyncClient | None = None,
        retry_delay_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise TavilyAuthenticationError("Tavily API key cannot be empty")
        self._api_key = api_key.strip()
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._client = client
        self._owns_client = client is None
        self._retry_delay_fn = retry_delay_fn or asyncio.sleep

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _send_request(self, endpoint_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute POST request with strict host check, bounded retry, and size validation."""
        parsed = urlparse(endpoint_url)
        if parsed.scheme != "https" or parsed.netloc != TAVILY_HOST:
            raise TavilyError(
                f"Destination forbidden: expected host '{TAVILY_HOST}', got '{parsed.netloc}'",
                error_code="NETWORK_SECURITY_VIOLATION",
            )

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        client = await self._get_client()
        last_exception: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                async with client.stream("POST", endpoint_url, json=payload, headers=headers) as response:
                    # 1. Pre-check Content-Length header before reading body if present
                    content_len = response.headers.get("Content-Length")
                    if content_len is not None:
                        try:
                            if int(content_len) > self._max_response_bytes:
                                raise TavilyResponseTooLargeError(
                                    f"Response Content-Length ({content_len}) exceeds limit ({self._max_response_bytes})"
                                )
                        except ValueError:
                            pass

                    # 2. Check early status codes
                    if response.status_code in (401, 403):
                        # No retry on authentication failure
                        raise TavilyAuthenticationError()

                    if response.status_code == 429:
                        if attempt < MAX_RETRIES:
                            retry_after_hdr = response.headers.get("Retry-After")
                            delay = 1.0 * (attempt + 1)
                            if retry_after_hdr:
                                try:
                                    parsed_delay = float(retry_after_hdr)
                                    delay = min(parsed_delay, MAX_RETRY_AFTER_SECONDS)
                                except ValueError:
                                    pass
                            await self._retry_delay_fn(delay)
                            continue
                        raise TavilyRateLimitError()

                    # 3. Stream and accumulate chunks enforcing bound while reading
                    chunks: list[bytes] = []
                    bytes_accumulated = 0
                    async for chunk in response.aiter_bytes():
                        bytes_accumulated += len(chunk)
                        if bytes_accumulated > self._max_response_bytes:
                            raise TavilyResponseTooLargeError(
                                f"Response body bytes ({bytes_accumulated}) exceeds limit ({self._max_response_bytes})"
                            )
                        chunks.append(chunk)

                    body_bytes = b"".join(chunks)

                    if 400 <= response.status_code < 500:
                        # Non-retriable client errors
                        err_text = body_bytes[:300].decode("utf-8", errors="replace")
                        err_msg = _sanitize_secret(err_text, self._api_key)
                        raise TavilyUpstreamError(
                            f"Tavily returned client error {response.status_code}: {err_msg}",
                            error_code="UPSTREAM_4XX",
                        )

                    if response.status_code >= 500:
                        if attempt < MAX_RETRIES:
                            delay = 0.5 * (attempt + 1)
                            await self._retry_delay_fn(delay)
                            continue
                        err_text = body_bytes[:300].decode("utf-8", errors="replace")
                        err_msg = _sanitize_secret(err_text, self._api_key)
                        raise TavilyUpstreamError(
                            f"Tavily returned server error {response.status_code}: {err_msg}",
                            error_code="UPSTREAM_5XX",
                        )

                    try:
                        data = json.loads(body_bytes.decode("utf-8"))
                    except Exception as json_exc:
                        raise TavilyInvalidResponseError(
                            "Tavily response could not be parsed as JSON",
                            error_code="INVALID_JSON",
                        ) from json_exc

                    if not isinstance(data, dict):
                        raise TavilyInvalidResponseError(
                            f"Expected JSON object response, got {type(data).__name__}",
                            error_code="INVALID_RESPONSE_SHAPE",
                        )

                    return data

            except httpx.TimeoutException as exc:
                last_exception = exc
                if attempt < MAX_RETRIES:
                    await self._retry_delay_fn(0.5 * (attempt + 1))
                    continue
                raise TavilyTimeoutError() from exc
            except httpx.NetworkError as exc:
                last_exception = exc
                if attempt < MAX_RETRIES:
                    await self._retry_delay_fn(0.5 * (attempt + 1))
                    continue
                raise TavilyNetworkError(_sanitize_secret(str(exc), self._api_key)) from exc
            except (TavilyError, DiscoveryProviderError):
                raise
            except Exception as exc:
                sanitized = _sanitize_secret(str(exc), self._api_key)
                raise TavilyError(f"Unexpected provider error: {sanitized}") from exc

        if last_exception:
            raise TavilyError(
                _sanitize_secret(f"Retries exhausted: {last_exception}", self._api_key)
            )
        raise TavilyError("Request execution failed unexpectedly")

    async def post_search(
        self,
        query: str,
        search_depth: str = "basic",
        max_results: int = 10,
        include_answer: bool = False,
    ) -> dict[str, Any]:
        """Execute discovery search against https://api.tavily.com/search."""
        payload = {
            "query": query,
            "search_depth": search_depth,
            "max_results": max_results,
            "include_answer": include_answer,
        }
        return await self._send_request(TAVILY_SEARCH_URL, payload)

    async def post_extract(
        self,
        urls: list[str],
        extract_depth: str = "basic",
    ) -> dict[str, Any]:
        """Execute content extraction against https://api.tavily.com/extract."""
        payload = {
            "urls": urls,
            "extract_depth": extract_depth,
        }
        return await self._send_request(TAVILY_EXTRACT_URL, payload)


# ── Discovery Provider Adapter ──


class TavilyDiscoveryProvider:
    """Tavily search discovery provider conforming to ResearchDiscoveryProvider.

    Discovery snippets are prospective only and must NEVER become evidence.
    """

    def __init__(
        self,
        api_key: str,
        search_depth: str = "basic",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        client: httpx.AsyncClient | TavilyClient | None = None,
        retry_delay_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._search_depth = search_depth
        if isinstance(client, TavilyClient):
            self._client = client
        else:
            self._client = TavilyClient(
                api_key=api_key,
                timeout_seconds=timeout_seconds,
                max_response_bytes=max_response_bytes,
                client=client,
                retry_delay_fn=retry_delay_fn,
            )

    async def close(self) -> None:
        await self._client.close()

    async def search(
        self,
        query: str,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[DiscoveryCandidate]:
        """Execute search query and map results to prospective DiscoveryCandidates."""
        bounded_limit = min(max(1, limit), 20)
        try:
            data = await self._client.post_search(
                query=query,
                search_depth=self._search_depth,
                max_results=bounded_limit,
                include_answer=False,
            )
        except TavilyAuthenticationError:
            raise
        except TavilyError as exc:
            raise DiscoveryProviderUnavailableError(str(exc)) from exc

        results = data.get("results")
        if not isinstance(results, list):
            raise TavilyInvalidResponseError(
                "Tavily search response missing 'results' list",
                error_code="INVALID_RESPONSE_SHAPE",
            )

        candidates: list[DiscoveryCandidate] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            raw_url = item.get("url")
            if not raw_url or not isinstance(raw_url, str):
                continue

            canonical_url = normalize_url(raw_url)
            title = (item.get("title") or "Untitled").strip()
            snippet = (item.get("content") or "").strip()
            publisher = derive_publisher_from_url(canonical_url)
            score = item.get("score")

            candidates.append(
                DiscoveryCandidate(
                    canonical_url=canonical_url,
                    title=title,
                    publisher=publisher,
                    snippet=snippet,
                    primary_source_status=PrimarySourceStatus.UNKNOWN,
                    published_at=None,
                    metadata={
                        "discovery_score": score,
                        "discovery_provider": "tavily",
                    },
                )
            )

        return candidates


# ── Content Extractor Adapter ──


class TavilyResearchContentExtractor:
    """Tavily web page content extractor conforming to ResearchContentExtractor.

    Extract raw content is the evidence boundary. Search snippets are NEVER used
    as a fallback when extraction fails.
    """

    def __init__(
        self,
        api_key: str,
        extract_depth: str = "basic",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        client: httpx.AsyncClient | TavilyClient | None = None,
        retry_delay_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._extract_depth = extract_depth
        if isinstance(client, TavilyClient):
            self._client = client
        else:
            self._client = TavilyClient(
                api_key=api_key,
                timeout_seconds=timeout_seconds,
                max_response_bytes=max_response_bytes,
                client=client,
                retry_delay_fn=retry_delay_fn,
            )
        self._url_cache: dict[str, ExtractedResearchDocument | None] = {}

    async def close(self) -> None:
        await self._client.close()

    async def extract_document(
        self,
        candidate: DiscoveryCandidate,
    ) -> ExtractedResearchDocument | None:
        """Extract full web page content for candidate URL.

        Returns ExtractedResearchDocument on success, or None on failure.
        Never falls back to candidate snippet.
        """
        norm_url = candidate.canonical_url
        if norm_url in self._url_cache:
            return self._url_cache[norm_url]

        try:
            data = await self._client.post_extract(
                urls=[norm_url],
                extract_depth=self._extract_depth,
            )
        except TavilyAuthenticationError:
            raise
        except TavilyError as exc:
            logger.warning(
                "Tavily extract call failed for candidate",
                url=norm_url,
                error=str(exc),
            )
            self._url_cache[norm_url] = None
            return None

        results = data.get("results")
        if not isinstance(results, list) or not results:
            self._url_cache[norm_url] = None
            return None

        # Locate matching extraction result strictly by normalized URL
        matched_item: dict[str, Any] | None = None
        for item in results:
            if isinstance(item, dict) and normalize_url(item.get("url", "")) == norm_url:
                matched_item = item
                break

        if matched_item is None:
            self._url_cache[norm_url] = None
            return None

        raw_content = matched_item.get("raw_content")
        if not raw_content or not isinstance(raw_content, str) or len(raw_content.strip()) < 15:
            # Content unavailable or empty; MUST NOT fall back to snippet
            self._url_cache[norm_url] = None
            return None

        extracted_title = (matched_item.get("title") or candidate.title or "Untitled").strip()
        extracted_doc = ExtractedResearchDocument(
            canonical_url=norm_url,
            title=extracted_title,
            publisher=candidate.publisher,
            extracted_content=raw_content[:10000],
            content_provenance={
                "extractor": "tavily_extract",
                "extracted_url": norm_url,
                "extract_depth": self._extract_depth,
            },
            primary_source_status=PrimarySourceStatus.UNKNOWN,
            published_at=candidate.published_at,
        )
        self._url_cache[norm_url] = extracted_doc
        return extracted_doc
