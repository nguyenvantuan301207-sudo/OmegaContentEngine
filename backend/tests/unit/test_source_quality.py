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


# ── Phase L Calibrated Source Quality Test Matrix ──


def test_irrelevant_web_source_rejected() -> None:
    """1. Irrelevant web source (0 keywords) must remain strictly below threshold (< 50.0)."""
    kws = ["concrete cracking", "shrinkage", "thermal cracking", "structural engineering", "construction materials"]
    q, rel, _, _ = calculate_source_quality(
        publisher="Generic Blog",
        url="https://genericblog.com/chocolate-cake",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=None,
        topic_keywords=kws,
        content_excerpt="A completely unrelated recipe for chocolate cake with sugar, flour and baking soda.",
    )
    assert rel == 0.0
    assert q < 50.0
    assert q == 34.5


def test_relevant_low_traceability_source_rejected() -> None:
    """2. Relevant but low-traceability source (no URL, short snippet) must remain below threshold."""
    kws = ["concrete cracking", "shrinkage", "thermal cracking", "structural engineering", "construction materials"]
    q, _, _, _ = calculate_source_quality(
        publisher="Offline Snippet",
        url=None,
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=None,
        topic_keywords=kws,
        content_excerpt="Concrete cracking and shrinkage overview.",  # < 50 chars
    )
    assert q < 50.0  # 42.5 (rel=70, trace=40)


def test_relevant_traceable_unknown_primary_web_source_qualifies() -> None:
    """3. Relevant traceable UNKNOWN-primary web source must have a plausible path to >= 50.0."""
    kws = ["concrete cracking", "shrinkage", "thermal cracking", "structural engineering", "construction materials"]
    # Matches 3 of 5 keywords conceptually: 'concrete cracking', 'shrinkage', 'structural engineering'
    text = "This paper examines concrete cracking mechanisms under structural engineering constraints in civil projects, focusing on plastic shrinkage."
    q, rel, _, _ = calculate_source_quality(
        publisher="Civil Engineering Digest",
        url="https://cedigest.org/articles/crack-mechanisms",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=None,
        topic_keywords=kws,
        content_excerpt=text,
    )
    assert rel == 90.0
    assert q >= 50.0
    assert q == 52.5


def test_confirmed_primary_outranks_unknown() -> None:
    """4. CONFIRMED primary source must strictly outperform UNKNOWN."""
    kws = ["concrete cracking", "shrinkage", "thermal cracking", "structural engineering", "construction materials"]
    text = "This paper examines concrete cracking mechanisms under structural engineering constraints in civil projects, focusing on plastic shrinkage."
    q_unk, _, _, _ = calculate_source_quality(
        publisher="FHWA",
        url="https://fhwa.dot.gov/pavements/pccp",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=None,
        topic_keywords=kws,
        content_excerpt=text,
    )
    q_conf, _, _, reasons = calculate_source_quality(
        publisher="FHWA",
        url="https://fhwa.dot.gov/pavements/pccp",
        primary_source_status=PrimarySourceStatus.CONFIRMED,
        published_at=None,
        topic_keywords=kws,
        content_excerpt=text,
    )
    assert q_conf > q_unk
    assert q_conf - q_unk == 25.0  # 0.25 * 100.0
    assert "CONFIRMED_PRIMARY_SOURCE" in reasons


def test_missing_published_at_safe() -> None:
    """5. Missing published_at is mildly conservative but safe (does not kill a relevant source)."""
    kws = ["concrete cracking", "shrinkage", "thermal cracking", "structural engineering", "construction materials"]
    text = "This paper examines concrete cracking mechanisms under structural engineering constraints in civil projects, focusing on plastic shrinkage."
    q_none, _, fresh_none, _ = calculate_source_quality(
        publisher="Engineering Portal",
        url="https://engportal.org/article",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=None,
        topic_keywords=kws,
        content_excerpt=text,
    )
    q_now, _, fresh_now, _ = calculate_source_quality(
        publisher="Engineering Portal",
        url="https://engportal.org/article",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=datetime.now(UTC),
        topic_keywords=kws,
        content_excerpt=text,
    )
    assert fresh_none == 70.0
    assert fresh_now == 100.0
    assert q_none >= 50.0  # 52.5
    assert q_now == 55.5  # +3.0 freshness bonus


def test_full_keyword_match_scaling() -> None:
    """6. Full keyword match (5/5) scales cleanly to high score."""
    kws = ["concrete cracking", "shrinkage", "thermal cracking", "structural engineering", "construction materials"]
    text = "Concrete cracking caused by shrinkage and thermal cracking requires rigorous structural engineering and quality construction materials."
    q, rel, _, _ = calculate_source_quality(
        publisher="Materials Journal",
        url="https://matjournal.com/pavements",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=None,
        topic_keywords=kws,
        content_excerpt=text,
    )
    assert rel == 100.0
    assert q == 54.5


def test_partial_keyword_match_scaling() -> None:
    """7. Partial keyword match (3/5) scores between partial and full."""
    kws = ["concrete cracking", "shrinkage", "thermal cracking", "structural engineering", "construction materials"]
    text_3 = "Concrete cracking caused by shrinkage requires rigorous structural engineering evaluation."
    text_5 = "Concrete cracking caused by shrinkage and thermal cracking requires rigorous structural engineering and quality construction materials."
    q_3, rel_3, _, _ = calculate_source_quality(
        publisher="Materials Journal",
        url="https://matjournal.com/pavements",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=None,
        topic_keywords=kws,
        content_excerpt=text_3,
    )
    q_5, rel_5, _, _ = calculate_source_quality(
        publisher="Materials Journal",
        url="https://matjournal.com/pavements",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=None,
        topic_keywords=kws,
        content_excerpt=text_5,
    )
    assert rel_3 == 90.0
    assert rel_5 == 100.0
    assert 50.0 <= q_3 < q_5


def test_zero_keyword_match_zero_relevance() -> None:
    """8. Zero keyword match receives zero relevance score."""
    kws = ["concrete cracking", "shrinkage", "thermal cracking", "structural engineering", "construction materials"]
    q, rel, _, _ = calculate_source_quality(
        publisher="Farming Weekly",
        url="https://farmingweekly.com/soil",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=None,
        topic_keywords=kws,
        content_excerpt="Discussion of organic farming methods and sustainable agriculture practices in soil management.",
    )
    assert rel == 0.0
    assert q < 50.0
    assert q == 34.5


def test_manual_source_path_preserved() -> None:
    """9. Manual authority provider and CLAIMED status are preserved and score highly."""
    prov = ManualAuthorityProvider({"ACI Standards": 90.0})
    kws = ["concrete cracking", "shrinkage", "thermal cracking", "structural engineering", "construction materials"]
    text = "Concrete cracking caused by shrinkage and thermal cracking requires rigorous structural engineering and quality construction materials."
    q, _, _, reasons = calculate_source_quality(
        publisher="ACI Standards",
        url="https://concrete.org/standards",
        primary_source_status=PrimarySourceStatus.CLAIMED,
        published_at=None,
        topic_keywords=kws,
        content_excerpt=text,
        authority_provider=prov,
    )
    assert q > 70.0  # 73.5
    assert "HIGH_AUTHORITY_PUBLISHER" in reasons
    assert "CLAIMED_PRIMARY_SOURCE" in reasons


def test_unrelated_topic_domain_rejected() -> None:
    """10. Unrelated topic/domain yields zero relevance and rejects."""
    quantum_kws = ["quantum entanglement", "superposition", "qubit", "wavefunction collapse", "schrodinger"]
    concrete_text = "Concrete cracking caused by shrinkage and thermal cracking requires structural engineering and construction materials."
    q, rel, _, _ = calculate_source_quality(
        publisher="Concrete Journal",
        url="https://concretejournal.org/paper",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=None,
        topic_keywords=quantum_kws,
        content_excerpt=concrete_text,
    )
    assert rel == 0.0
    assert q < 50.0
    assert q == 34.5


def test_conceptual_multi_word_keyword_matching() -> None:
    """11. Multi-word topic keywords match conceptually when constituents appear across technical phrasing."""
    kws = ["concrete cracking", "thermal cracking"]
    # Does not have exact adjacent "concrete cracking" or "thermal cracking", but has all constituent terms
    text = "Investigation into premature cracking in mass concrete pavements subject to severe thermal stresses during hydration."
    q, rel, _, _ = calculate_source_quality(
        publisher="Pavement Tech",
        url="https://pavementtech.org/report",
        primary_source_status=PrimarySourceStatus.UNKNOWN,
        published_at=None,
        topic_keywords=kws,
        content_excerpt=text,
    )
    assert rel == 100.0  # 2 of 2 matched conceptually
    assert q >= 50.0
    assert q == 54.5
