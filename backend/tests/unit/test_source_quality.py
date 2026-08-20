"""Unit tests for Source Quality and Excerpt Bounding."""

from __future__ import annotations

from datetime import UTC, datetime

from omega.application.research_scorer import calculate_source_quality
from omega.application.source_normalizer import (
    MAX_EXCERPT_LENGTH,
    bound_excerpt,
    compute_source_content_hash,
    normalize_source_text,
)
from omega.application.source_provider import ManualAuthorityProvider, NullAuthorityProvider
from omega.domain.research import PrimarySourceStatus


def test_excerpt_bounding() -> None:
    """Test that text exceeding MAX_EXCERPT_LENGTH is strictly bounded."""
    long_text = "A" * (MAX_EXCERPT_LENGTH + 500)
    bounded = bound_excerpt(long_text)
    assert len(bounded) == MAX_EXCERPT_LENGTH

    short_text = "Short valid excerpt."
    assert bound_excerpt(short_text) == short_text


def test_content_hash_deterministic_stability() -> None:
    """Test that source content hash is versioned and deterministic across calls."""
    text1 = "Clean Architecture with Python & PostgreSQL."
    text2 = "clean architecture with python & postgresql."
    h1 = compute_source_content_hash(normalize_source_text(text1))
    h2 = compute_source_content_hash(normalize_source_text(text2))
    assert h1 == h2
    assert len(h1) == 64


def test_source_quality_confirmed_vs_claimed_bonus() -> None:
    """Test that CONFIRMED primary source receives full bonus while CLAIMED receives limited bonus."""
    pub = "Python Software Foundation"
    url = "https://docs.python.org/3/library/asyncio.html"
    kws = ["asyncio", "python"]
    excerpt = "Asyncio provides event loop infrastructure for running asynchronous tasks in Python."

    provider = ManualAuthorityProvider({pub: 90.0})

    # CONFIRMED
    q_conf, rel_conf, fresh_conf, r_conf = calculate_source_quality(
        publisher=pub,
        url=url,
        primary_source_status=PrimarySourceStatus.CONFIRMED,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        topic_keywords=kws,
        content_excerpt=excerpt,
        authority_provider=provider,
    )

    # CLAIMED (manual declaration)
    q_claim, _, _, r_claim = calculate_source_quality(
        publisher=pub,
        url=url,
        primary_source_status=PrimarySourceStatus.CLAIMED,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        topic_keywords=kws,
        content_excerpt=excerpt,
        authority_provider=provider,
    )

    # UNKNOWN
    q_unk, _, _, _ = calculate_source_quality(
        publisher=pub,
        url=url,
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        topic_keywords=kws,
        content_excerpt=excerpt,
        authority_provider=provider,
    )

    assert q_conf > q_claim > q_unk
    assert "CONFIRMED_PRIMARY_SOURCE" in r_conf
    assert "CLAIMED_PRIMARY_SOURCE" in r_claim


def test_null_authority_provider_default() -> None:
    """Test that NullAuthorityProvider returns neutral 50.0 score."""
    provider = NullAuthorityProvider()
    score = provider.get_authority_score("Random Tech Blog", "https://randomblog.com/post")
    assert score == 50.0
