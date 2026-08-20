"""Unit tests for local Content Gap Analyzer."""

from __future__ import annotations

from omega.application.content_gap_analyzer import analyze_content_gap


def test_content_gap_on_empty_memory() -> None:
    """Test content gap on channel with no historical topic memories."""
    score, reasons = analyze_content_gap(
        candidate_title="Advanced Rust Concurrency",
        candidate_keywords=["Rust", "concurrency"],
        content_pillars=["Rust Tutorials", "Architecture"],
        memory_records=[],
    )
    # Zero existing memories means the pillar has 0 previous topics -> max gap score (100.0)
    assert score == 100.0
    assert any("UNDERSERVED_CONTENT_PILLAR" in r for r in reasons)


def test_content_gap_identifies_underserved_pillar() -> None:
    """Test content gap identifies an underserved pillar compared to a saturated one."""
    mock_memory = [
        {"canonical_topic": "Rust Tutorials 1", "keywords": ["Rust"]},
        {"canonical_topic": "Rust Tutorials 2", "keywords": ["Rust"]},
        {"canonical_topic": "Rust Tutorials 3", "keywords": ["Rust"]},
        # Architecture has 0 topics
    ]

    score_rust, _ = analyze_content_gap(
        candidate_title="Rust Basics Tutorial",
        candidate_keywords=["Rust"],
        content_pillars=["Rust Tutorials", "Architecture"],
        memory_records=mock_memory,
    )

    score_arch, reasons_arch = analyze_content_gap(
        candidate_title="Clean Architecture Systems",
        candidate_keywords=["Architecture"],
        content_pillars=["Rust Tutorials", "Architecture"],
        memory_records=mock_memory,
    )

    # Architecture has 0 coverage, Rust has 3 -> Architecture should score significantly higher gap fill
    assert score_arch == 100.0
    assert score_rust < score_arch
    assert any("UNDERSERVED_CONTENT_PILLAR: architecture" in r for r in reasons_arch)
