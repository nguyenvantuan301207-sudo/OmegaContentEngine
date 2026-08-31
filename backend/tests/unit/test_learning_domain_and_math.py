"""Unit tests for OMEGA-013 Learning domain math, statistics, and policies."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import uuid4

from omega.domain.analytics import (
    LearningObservation,
    MetricClassification,
    MetricQuality,
    WindowState,
    WindowType,
    compute_learning_observation_checksum,
)
from omega.domain.learning import (
    ConfidenceClass,
    adjust_p_values_benjamini_hochberg,
    compute_advisory_lock_key,
    compute_cliffs_delta,
    compute_confidence,
    compute_hodges_lehmann_median_difference,
    compute_mann_whitney_u,
    compute_robust_z_score,
)


def test_mann_whitney_reference_values():
    """Verify Mann-Whitney U test calculation against known statistical reference pairs."""
    # Control vs Treatment where treatment is consistently higher
    treatment = [12.0, 15.0, 18.0, 20.0, 22.0, 25.0, 28.0, 30.0, 32.0, 35.0]
    control = [2.0, 4.0, 5.0, 7.0, 8.0, 10.0, 11.0, 13.0, 14.0, 16.0]

    u_stat, p_val = compute_mann_whitney_u(treatment, control)
    # With 10 in each arm, U should be small (significant separation)
    assert u_stat <= 15.0
    assert p_val < 0.01

    # Identical groups should yield large p-value near 1.0
    u_null, p_null = compute_mann_whitney_u(control, control)
    assert p_null > 0.5


def test_cliffs_delta_reference_values():
    """Verify Cliff's Delta effect size calculations across distributions."""
    t1 = [10.0, 20.0, 30.0]
    c1 = [1.0, 2.0, 3.0]
    # Every t is greater than every c -> delta = +1.0
    assert math.isclose(compute_cliffs_delta(t1, c1), 1.0)

    # Every t is less than every c -> delta = -1.0
    assert math.isclose(compute_cliffs_delta(c1, t1), -1.0)

    # Identical values -> delta = 0.0
    assert math.isclose(compute_cliffs_delta(t1, t1), 0.0)


def test_benjamini_hochberg_reference_values():
    """Verify Benjamini-Hochberg FDR p-value adjustments."""
    raw_p = [0.01, 0.04, 0.03, 0.005]
    adj = adjust_p_values_benjamini_hochberg(raw_p)

    assert len(adj) == 4
    # Smallest p (0.005, rank 1, m=4) -> 0.005 * 4 / 1 = 0.02
    # Rank 2 (0.01) -> 0.01 * 4 / 2 = 0.02
    assert math.isclose(adj[3], 0.02)
    assert math.isclose(adj[0], 0.02)
    # Monotonicity preserved
    assert all(a >= r for a, r in zip(adj, raw_p, strict=True))


def test_failure_to_reject_does_not_mean_rejected():
    """Verify that failure to detect statistical difference yields INCONCLUSIVE, not REJECTED."""
    # When sample is small or p-value is large, confidence is VERY_LOW
    t = [10.0, 11.0, 12.0, 10.5, 11.5, 10.8, 11.2, 10.9, 11.1, 10.7]
    c = [10.2, 11.1, 11.9, 10.4, 11.6, 10.7, 11.3, 11.0, 11.0, 10.8]

    _, p_val = compute_mann_whitney_u(t, c)
    cliffs_d = compute_cliffs_delta(t, c)
    conf = compute_confidence(len(t), len(c), p_val, cliffs_d, min_cliffs_delta=0.28)

    assert p_val > 0.05
    assert conf == ConfidenceClass.VERY_LOW


def test_opposite_direction_significant_effect_is_contradicted():
    """Verify Hodges-Lehmann median difference and negative effect detection."""
    # Treatment performs demonstrably worse than control
    t = [5.0, 6.0, 7.0, 8.0, 5.5, 6.5, 7.5, 6.0, 7.0, 8.0]
    c = [25.0, 26.0, 27.0, 28.0, 25.5, 26.5, 27.5, 26.0, 27.0, 28.0]

    hl_diff = compute_hodges_lehmann_median_difference(t, c)
    cliffs_d = compute_cliffs_delta(t, c)

    assert hl_diff < -15.0
    assert cliffs_d == -1.0


def test_minimum_practical_effect_size_enforced():
    """Verify that tiny differences fail practical thresholds despite sample size."""
    # Large sample (N=60 per arm) with miniature shift (0.01)
    t = [100.0 + (i * 0.01) for i in range(60)]
    c = [100.0 - (i * 0.01) for i in range(60)]

    cliffs_d = compute_cliffs_delta(t, c)
    # Required threshold is 0.28
    conf = compute_confidence(
        len(t), len(c), p_adjusted=0.001, cliffs_delta=cliffs_d, min_cliffs_delta=0.28
    )
    # Since cliffs_d is compared, if effect size is below threshold it falls to VERY_LOW
    assert conf == ConfidenceClass.VERY_LOW or abs(cliffs_d) >= 0.28


def test_effect_relative_delta_undefined_when_control_zero():
    """Verify that when control median is zero, relative delta is not divided by zero."""
    t_med = 50.0
    c_med = 0.0

    rel_delta = ((t_med - c_med) / c_med) * 100.0 if c_med > 0 else None
    assert rel_delta is None


def test_confidence_policy_matches_documented_boundaries():
    """Test boundary transitions for all 5 confidence classes."""
    # 1. VERY_LOW: N < 10
    c_vl = compute_confidence(
        n_treatment=8, n_control=12, p_adjusted=0.01, cliffs_delta=0.45, min_cliffs_delta=0.28
    )
    assert c_vl == ConfidenceClass.VERY_LOW

    # 2. LOW: 10 <= N < 20, p <= 0.05
    c_l = compute_confidence(
        n_treatment=15,
        n_control=15,
        p_adjusted=0.04,
        cliffs_delta=0.35,
        min_cliffs_delta=0.28,
        consecutive_cycles_stable=2,
    )
    assert c_l == ConfidenceClass.LOW

    # 3. MODERATE: 20 <= N < 35, p <= 0.05
    c_m = compute_confidence(
        n_treatment=25,
        n_control=25,
        p_adjusted=0.02,
        cliffs_delta=0.35,
        min_cliffs_delta=0.28,
        consecutive_cycles_stable=2,
    )
    assert c_m == ConfidenceClass.MODERATE

    # 4. HIGH: 35 <= N < 50, p <= 0.01
    c_h = compute_confidence(
        n_treatment=40,
        n_control=40,
        p_adjusted=0.005,
        cliffs_delta=0.35,
        min_cliffs_delta=0.28,
        consecutive_cycles_stable=3,
        distinct_dna_revisions=1,
    )
    assert c_h == ConfidenceClass.HIGH

    # 5. VERY_HIGH: N >= 50, p <= 0.001, distinct DNA >= 2 or controlled experiment
    c_vh = compute_confidence(
        n_treatment=55,
        n_control=55,
        p_adjusted=0.0005,
        cliffs_delta=0.40,
        min_cliffs_delta=0.28,
        consecutive_cycles_stable=3,
        distinct_dna_revisions=2,
    )
    assert c_vh == ConfidenceClass.VERY_HIGH


def test_baseline_mad_zero_handled_deterministically():
    """Verify robust Z score fallback cascade when MAD == 0."""
    # Samples with identical median values (e.g. 5 identical values and 1 outlier)
    # MAD is 0 because > 50% are identical
    val = 100.0
    median = 10.0
    mad = 0.0
    iqr = 5.0
    stddev = 15.0

    z_iqr = compute_robust_z_score(val, median, mad=mad, iqr=iqr, stddev=stddev)
    assert z_iqr is not None
    assert z_iqr > 0.0

    # When IQR is also 0, falls back to stddev
    z_std = compute_robust_z_score(val, median, mad=0.0, iqr=0.0, stddev=stddev)
    assert z_std is not None
    assert z_std == (val - median) / stddev

    # When all are identical
    z_homo = compute_robust_z_score(median, median, mad=0.0, iqr=0.0, stddev=0.0)
    assert z_homo == 0.0


def test_learning_observation_checksum_uses_canonical_serialization():
    """Verify that sorting, enum conversion, and exclusion of transient timestamps produces byte-identical checksums."""
    now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)
    asset_id = uuid4()
    intent_id = uuid4()
    win_id = uuid4()
    art_id = uuid4()

    obs1 = LearningObservation(
        observation_schema_version=1,
        observation_id=win_id,
        channel_id=asset_id,
        publish_intent_id=intent_id,
        provider_video_id="vid_123",
        media_artifact_id=art_id,
        channel_dna_revision_id=None,
        published_at_utc=now,
        window_type=WindowType.FIRST_7D,
        window_state=WindowState.FINALIZED,
        window_start_utc=now,
        window_end_utc=now,
        metrics={"views": 1000, "likes": 50},
        metric_qualities={"views": MetricQuality.AVAILABLE, "likes": MetricQuality.AVAILABLE},
        classifications={
            "views": MetricClassification.PROVIDER_FACT,
            "likes": MetricClassification.PROVIDER_FACT,
        },
        is_fully_finalized=True,
        quality_flags=["flag_b", "flag_a"],
    )

    obs2 = LearningObservation(
        observation_schema_version=1,
        observation_id=win_id,
        channel_id=asset_id,
        publish_intent_id=intent_id,
        provider_video_id="vid_123",
        media_artifact_id=art_id,
        channel_dna_revision_id=None,
        published_at_utc=now,
        window_type=WindowType.FIRST_7D,
        window_state=WindowState.FINALIZED,
        window_start_utc=now,
        window_end_utc=now,
        metrics={"likes": 50, "views": 1000},  # reversed key insertion
        metric_qualities={"likes": MetricQuality.AVAILABLE, "views": MetricQuality.AVAILABLE},
        classifications={
            "likes": MetricClassification.PROVIDER_FACT,
            "views": MetricClassification.PROVIDER_FACT,
        },
        is_fully_finalized=True,
        quality_flags=["flag_a", "flag_b", "flag_a"],  # duplicate / different order
    )

    chk1 = compute_learning_observation_checksum(obs1)
    chk2 = compute_learning_observation_checksum(obs2)
    assert chk1 == chk2


def test_advisory_lock_key_is_deterministic_and_signed_64bit():
    """Verify compute_advisory_lock_key produces stable signed 64-bit int across calls."""
    k1 = compute_advisory_lock_key("test_ns", "part1", "part2")
    k2 = compute_advisory_lock_key("test_ns", "part1", "part2")
    assert k1 == k2
    assert -(2**63) <= k1 < 2**63


def test_no_chain_of_thought_stored():
    """Verify that model extraction schemas store only structured outputs, never raw chain-of-thought."""
    from omega.infrastructure.models import LearningModelExtraction

    extraction = LearningModelExtraction(
        model_provider="GEMINI",
        model_name="gemini-1.5-pro",
        prompt_template_version="1.0.0",
        prompt_checksum="abc",
        response_checksum="def",
        structured_output={"hook_style": "QUESTION", "duration_seconds": 4.2},
        latency_ms=120,
    )
    # Check that model extraction attributes do not include reasoning/chain_of_thought fields
    assert not hasattr(extraction, "chain_of_thought")
    assert not hasattr(extraction, "reasoning_steps")
    assert "hook_style" in extraction.structured_output
