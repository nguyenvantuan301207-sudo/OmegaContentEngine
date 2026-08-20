"""Unit tests for authoritative duplicate and similarity classification."""

from __future__ import annotations

import pytest

from omega.application.duplicate_detector import (
    classify_topic_similarity,
    compute_topic_fingerprint,
)
from omega.domain.topic import DuplicateStatus, TopicAngleCreate
from omega.domain.topic_scoring import SimilarityProfile


def test_similarity_profile_ordering_validation() -> None:
    """Test that SimilarityProfile enforces strict ordering: fresh < related <= new_angle_min < semantic < exact."""
    # Valid default profile
    valid = SimilarityProfile(
        fresh_threshold=0.25,
        related_threshold=0.50,
        new_angle_min_similarity=0.50,
        semantic_duplicate_threshold=0.75,
        exact_duplicate_threshold=0.98,
    )
    assert valid.fresh_threshold == 0.25

    # Invalid unordered profile
    with pytest.raises(ValueError, match="Similarity thresholds must satisfy"):
        SimilarityProfile(
            fresh_threshold=0.60,
            related_threshold=0.50,
            new_angle_min_similarity=0.50,
            semantic_duplicate_threshold=0.75,
            exact_duplicate_threshold=0.98,
        )


def test_exact_duplicate_via_fingerprint() -> None:
    """Test exact match via identical fingerprint."""
    fp = compute_topic_fingerprint("why rome fell", "en", ["Rome"], ["collapse"])
    status, sim = classify_topic_similarity(
        candidate_norm_title="why rome fell",
        candidate_entities=["Rome"],
        candidate_keywords=["collapse"],
        candidate_fingerprint=fp,
        candidate_angles=[],
        memory_norm_title="why rome fell",
        memory_entities=["Rome"],
        memory_keywords=["collapse"],
        memory_fingerprint=fp,
        memory_angles=[],
    )
    assert status == DuplicateStatus.EXACT_DUPLICATE
    assert sim == 1.0


def test_paraphrase_without_angle_is_semantic_duplicate() -> None:
    """Test that high-similarity paraphrase WITHOUT angle evidence is strictly classified as SEMANTIC_DUPLICATE."""
    cand_title = "complete guide to python concurrency and async"
    mem_title = "complete guide to python async concurrency"
    fp_cand = compute_topic_fingerprint(cand_title, "en", ["Python", "Async"], ["concurrency"])
    fp_mem = compute_topic_fingerprint(mem_title, "en", ["Python", "Async"], ["concurrency"])

    status, sim = classify_topic_similarity(
        candidate_norm_title=cand_title,
        candidate_entities=["Python", "Async"],
        candidate_keywords=["concurrency"],
        candidate_fingerprint=fp_cand,
        candidate_angles=[],  # No angle evidence provided
        memory_norm_title=mem_title,
        memory_entities=["Python", "Async"],
        memory_keywords=["concurrency"],
        memory_fingerprint=fp_mem,
        memory_angles=[],
    )

    # Paraphrase without angle evidence must be classified as SEMANTIC_DUPLICATE
    assert status == DuplicateStatus.SEMANTIC_DUPLICATE
    assert sim >= 0.75


def test_same_base_topic_with_distinct_angle_is_new_angle() -> None:
    """Test that high-similarity topic WITH distinct angle evidence is classified as SAME_TOPIC_NEW_ANGLE."""
    cand_title = "complete guide to python async concurrency"
    mem_title = "complete guide to python async concurrency"
    # Notice: different angle
    fp_cand = compute_topic_fingerprint(cand_title, "en", ["Python"], ["concurrency"])
    fp_mem = compute_topic_fingerprint(mem_title, "en", ["Python"], ["concurrency"])

    cand_angles = [
        TopicAngleCreate(
            angle="Event loop internals in Python 3.12",
            hook="How AsyncIO event loop actually schedules coroutines",
            audience_intent="Advanced Internals",
        )
    ]

    status, sim = classify_topic_similarity(
        candidate_norm_title=cand_title,
        candidate_entities=["Python"],
        candidate_keywords=["concurrency"],
        candidate_fingerprint=fp_cand,
        candidate_angles=cand_angles,  # Explicit distinct angle evidence!
        memory_norm_title=mem_title,
        memory_entities=["Python"],
        memory_keywords=["concurrency"],
        memory_fingerprint=fp_mem,
        memory_angles=[{"angle": "Asyncio network sockets"}],
    )

    assert status == DuplicateStatus.SAME_TOPIC_NEW_ANGLE
    assert sim >= 0.50


def test_related_topic_classification() -> None:
    """Test moderate overlap classified as RELATED_TOPIC."""
    cand_title = "the rise of constantinople and the byzantine empire"
    mem_title = "why the roman empire fell"
    fp_cand = compute_topic_fingerprint(cand_title, "en", ["Byzantine Empire"], ["history"])
    fp_mem = compute_topic_fingerprint(mem_title, "en", ["Roman Empire"], ["history"])

    status, sim = classify_topic_similarity(
        candidate_norm_title=cand_title,
        candidate_entities=["Byzantine Empire"],
        candidate_keywords=["history"],
        candidate_fingerprint=fp_cand,
        candidate_angles=[],
        memory_norm_title=mem_title,
        memory_entities=["Roman Empire"],
        memory_keywords=["history"],
        memory_fingerprint=fp_mem,
        memory_angles=[],
    )

    assert status == DuplicateStatus.RELATED_TOPIC
    assert 0.25 <= sim < 0.75


def test_fresh_topic_classification() -> None:
    """Test non-overlapping topic classified as FRESH_TOPIC."""
    cand_title = "quantum computing algorithms in 2026"
    mem_title = "why the roman empire fell"
    fp_cand = compute_topic_fingerprint(cand_title, "en", ["Quantum"], ["physics"])
    fp_mem = compute_topic_fingerprint(mem_title, "en", ["Rome"], ["history"])

    status, sim = classify_topic_similarity(
        candidate_norm_title=cand_title,
        candidate_entities=["Quantum"],
        candidate_keywords=["physics"],
        candidate_fingerprint=fp_cand,
        candidate_angles=[],
        memory_norm_title=mem_title,
        memory_entities=["Rome"],
        memory_keywords=["history"],
        memory_fingerprint=fp_mem,
        memory_angles=[],
    )

    assert status == DuplicateStatus.FRESH_TOPIC
    assert sim < 0.25
