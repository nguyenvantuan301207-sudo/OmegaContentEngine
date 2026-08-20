"""Unit tests for deterministic topic normalization and versioned SHA-256 fingerprinting."""

from __future__ import annotations

from omega.application.duplicate_detector import compute_topic_fingerprint, normalize_text, tokenize


def test_text_normalization() -> None:
    """Test deterministic Unicode, casing, whitespace, and punctuation normalization."""
    # Punctuation stripping and case folding
    assert normalize_text("Why Rome Fell?") == "why rome fell"
    assert normalize_text("WHY ROME FELL!!!") == "why rome fell"
    assert normalize_text("Why   Rome   Fell...") == "why rome fell"
    assert normalize_text("Why-Rome-Fell") == "why-rome-fell"

    # Unicode NFKC normalization
    assert normalize_text("Café & Résumé") == "cafe resume"
    assert normalize_text("“Smart” Agents: A Guide!") == "smart agents a guide"


def test_tokenization() -> None:
    """Test tokenization of normalized text into sets."""
    tokens = tokenize("why rome fell and why it matters")
    assert "rome" in tokens
    assert "fell" in tokens
    assert "matters" in tokens
    # single letter tokens excluded
    assert "a" not in tokenize("a big dog")


def test_versioned_fingerprint_stability() -> None:
    """Test that omega-topic-fingerprint:v1 produces identical, stable SHA-256 digests across equivalent inputs."""
    fp1 = compute_topic_fingerprint(
        normalized_title="why rome fell",
        language="en",
        entities=["Roman Empire", "Ancient Rome"],
        keywords=["economy", "military", "collapse"],
    )

    # Reordered entities/keywords and different casing should yield EXACT same fingerprint
    fp2 = compute_topic_fingerprint(
        normalized_title="why rome fell",
        language="EN",
        entities=["ancient rome", "ROMAN EMPIRE"],
        keywords=["collapse", "Economy", "military"],
    )

    assert fp1 == fp2
    assert len(fp1) == 64  # Valid SHA-256 hex length


def test_fingerprint_distinguishes_different_topics() -> None:
    """Test that distinct topics generate distinct fingerprints."""
    fp_rome = compute_topic_fingerprint(
        normalized_title="why rome fell",
        language="en",
        entities=["Rome"],
        keywords=["history"],
    )

    fp_greece = compute_topic_fingerprint(
        normalized_title="why greece fell",
        language="en",
        entities=["Greece"],
        keywords=["history"],
    )

    assert fp_rome != fp_greece
