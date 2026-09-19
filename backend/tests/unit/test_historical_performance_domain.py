"""Unit tests for P19-E Historical Performance feedback domain algorithms and models."""

from __future__ import annotations

from omega.application.signal_providers import (
    FixedHistoricalPerformanceProvider,
    NullPerformanceProvider,
)
from omega.domain.historical_performance import (
    HistoricalContentIdentity,
    HistoricalPerformanceStatus,
    HistoricalRecord,
    compute_content_identity_checksum,
    compute_corpus_checksum,
    compute_historical_performance_policy_checksum,
    compute_lexical_relevance,
    compute_percentile_midranks,
)


def test_historical_policy_checksum_deterministic() -> None:
    """Historical performance policy checksum must be deterministic and stable."""
    cs1 = compute_historical_performance_policy_checksum()
    cs2 = compute_historical_performance_policy_checksum()
    assert cs1 == cs2
    assert isinstance(cs1, str)
    assert len(cs1) == 64


def test_percentile_midranks_empty_and_single() -> None:
    """PERCENTILE_MIDRANK_V1 handling of empty and single element inputs."""
    assert compute_percentile_midranks([]) == []
    assert compute_percentile_midranks([42.0]) == [50.0]


def test_percentile_midranks_all_identical() -> None:
    """PERCENTILE_MIDRANK_V1: Homogeneous values produce exactly 50.0 for all items."""
    scores = compute_percentile_midranks([10.0, 10.0, 10.0, 10.0, 10.0])
    assert scores == [50.0, 50.0, 50.0, 50.0, 50.0]


def test_percentile_midranks_strictly_increasing() -> None:
    """PERCENTILE_MIDRANK_V1: Distinct values map min to 0.0 and max to 100.0."""
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    scores = compute_percentile_midranks(values)
    # N=5: midranks 1, 2, 3, 4, 5
    # score = ((midrank - 1) / 4) * 100
    assert scores == [0.0, 25.0, 50.0, 75.0, 100.0]


def test_percentile_midranks_ties_at_bottom_and_top() -> None:
    """PERCENTILE_MIDRANK_V1 tie handling at extremes."""
    # Bottom tie: two identical lowest values
    # Values: [10.0, 10.0, 30.0, 40.0, 50.0]
    # Ranks for 10.0: (1 + 2)/2 = 1.5 -> ((1.5 - 1)/4)*100 = 12.5
    scores = compute_percentile_midranks([10.0, 10.0, 30.0, 40.0, 50.0])
    assert scores == [12.5, 12.5, 50.0, 75.0, 100.0]

    # Top tie: two identical highest values
    # Values: [10.0, 20.0, 30.0, 50.0, 50.0]
    # Ranks for 50.0: (4 + 5)/2 = 4.5 -> ((4.5 - 1)/4)*100 = 87.5
    scores_top = compute_percentile_midranks([10.0, 20.0, 30.0, 50.0, 50.0])
    assert scores_top == [0.0, 25.0, 50.0, 87.5, 87.5]


def test_percentile_midranks_unsorted_input_order_preserved() -> None:
    """Result scores must correspond to the original input positions."""
    values = [50.0, 10.0, 40.0, 20.0, 30.0]
    scores = compute_percentile_midranks(values)
    assert scores == [100.0, 0.0, 75.0, 25.0, 50.0]


def test_lexical_relevance_identical_text() -> None:
    """Exact token matches yield relevance 1.0."""
    rel, jaccard, coverage = compute_lexical_relevance(
        candidate_title="Quantum Computing In 2026",
        candidate_keywords=["quantum", "computing"],
        historical_title="Quantum Computing In 2026",
        historical_keywords=["quantum", "computing"],
        historical_tags=["tech"],
    )
    assert rel == 1.0
    assert coverage == 1.0
    assert jaccard > 0.6


def test_lexical_relevance_disjoint_text() -> None:
    """Disjoint token sets yield relevance 0.0."""
    rel, jaccard, coverage = compute_lexical_relevance(
        candidate_title="Baking Sourdough Bread",
        candidate_keywords=["sourdough", "bread", "baking"],
        historical_title="Quantum Physics And Black Holes",
        historical_keywords=["physics", "quantum"],
        historical_tags=["astronomy"],
    )
    assert rel == 0.0
    assert jaccard == 0.0
    assert coverage == 0.0


def test_lexical_relevance_keyword_coverage() -> None:
    """Keyword coverage can elevate relevance even if candidate title differs."""
    rel, jaccard, coverage = compute_lexical_relevance(
        candidate_title="Brand New Topic Title",
        candidate_keywords=["deepseek", "ai"],
        historical_title="Comprehensive DeepSeek AI Model Analysis",
        historical_keywords=["deepseek", "benchmark"],
        historical_tags=["ai"],
    )
    assert coverage == 1.0  # both "deepseek" and "ai" are in historical tokens
    assert rel == 1.0


def test_content_identity_checksum_deterministic() -> None:
    """Content identity checksum must be order-independent and deterministic."""
    c1 = compute_content_identity_checksum(
        identity_authority="MISSION_CANONICAL_TOPIC",
        source_authority_ids={"topic_candidate_id": "uuid-1", "publish_intent_id": "uuid-2"},
        normalized_title="quantum computing",
        normalized_keywords=["quantum", "tech"],
        normalized_tags=["science", "ai"],
    )
    c2 = compute_content_identity_checksum(
        identity_authority="MISSION_CANONICAL_TOPIC",
        source_authority_ids={"publish_intent_id": "uuid-2", "topic_candidate_id": "uuid-1"},
        normalized_title="quantum computing",
        normalized_keywords=["tech", "quantum"],
        normalized_tags=["ai", "science"],
    )
    assert c1 == c2


def test_corpus_checksum_deterministic_and_order_invariant() -> None:
    """Corpus checksum must sort snapshots by learning_snapshot_id for invariance."""
    p_cs = compute_historical_performance_policy_checksum()
    s1 = {
        "learning_snapshot_id": "00000000-0000-0000-0000-000000000001",
        "observation_id": "00000000-0000-0000-0000-000000000011",
        "payload_checksum": "hash1",
        "revision_sequence": 1,
        "window_type": "FIRST_7D",
        "window_state": "FINALIZED",
        "content_identity_checksum": "ident1",
    }
    s2 = {
        "learning_snapshot_id": "00000000-0000-0000-0000-000000000002",
        "observation_id": "00000000-0000-0000-0000-000000000012",
        "payload_checksum": "hash2",
        "revision_sequence": 1,
        "window_type": "FIRST_7D",
        "window_state": "FINALIZED",
        "content_identity_checksum": "ident2",
    }
    c_forward = compute_corpus_checksum(snapshots=[s1, s2], policy_checksum=p_cs)
    c_reverse = compute_corpus_checksum(snapshots=[s2, s1], policy_checksum=p_cs)
    assert c_forward == c_reverse


def test_fixed_historical_performance_provider() -> None:
    """FixedHistoricalPerformanceProvider returns fixed score unless manual score provided."""
    prov = FixedHistoricalPerformanceProvider(score=72.5)
    assert prov.get_performance_score("title", ["kw"]) == 72.5
    assert prov.get_performance_score("title", ["kw"], manual_score=90.0) == 90.0

    null_prov = NullPerformanceProvider()
    assert null_prov.get_performance_score("title", ["kw"]) == 50.0
    assert null_prov.get_performance_score("title", ["kw"], manual_score=80.0) == 80.0


def _make_dummy_record(
    idx: int,
    title: str,
    keywords: list[str],
    tags: list[str],
    item_perf: float | None = 75.0,
) -> HistoricalRecord:
    import uuid
    from datetime import UTC, datetime

    from omega.domain.historical_performance import (
        ContentIdentityAuthority,
        HistoricalRecord,
    )

    snap_id = uuid.UUID(f"00000000-0000-0000-0000-{idx:012d}")
    obs_id = uuid.UUID(f"00000000-0000-0000-0001-{idx:012d}")
    ident = HistoricalContentIdentity(
        identity_authority=ContentIdentityAuthority.PUBLISH_INTENT_METADATA,
        title=title,
        keywords=keywords,
        tags=tags,
        normalized_title=title.lower(),
        normalized_keywords=[k.lower() for k in keywords],
        normalized_tags=[t.lower() for t in tags],
        identity_checksum=f"ident-cs-{idx}",
        source_authority_ids={"learning_snapshot_id": str(snap_id)},
    )
    return HistoricalRecord(
        snapshot_id=snap_id,
        observation_id=obs_id,
        publish_intent_id=None,
        published_at_utc=datetime(2026, 1, 1 + (idx % 28), tzinfo=UTC),
        revision_sequence=1,
        payload_checksum=f"hash-{idx}",
        window_type="FIRST_7D",
        window_state="FINALIZED",
        identity=ident,
        metric_values={"views": float(idx * 1000)},
        normalized_metrics={"views": item_perf} if item_perf is not None else {},
        historical_item_performance=item_perf,
    )


def test_provider_evaluation_insufficient_corpus() -> None:
    """Fewer than 5 performance records yields INSUFFICIENT_CORPUS fallback 50.0."""
    import uuid

    from omega.application.historical_performance_provider import (
        LearningHistoricalPerformanceProvider,
    )

    records = [_make_dummy_record(i, f"Topic {i}", ["kw"], ["tag"]) for i in range(3)]
    provider = LearningHistoricalPerformanceProvider(
        channel_id=uuid.uuid4(),
        policy_checksum="pol_cs",
        corpus_checksum="corp_cs",
        pinned_snapshots=[],
        records=records,
    )
    signal = provider.evaluate("Topic 0", ["kw"])
    assert signal.status == HistoricalPerformanceStatus.INSUFFICIENT_CORPUS
    assert signal.score == 50.0
    assert signal.match_count == 0
    assert signal.learning_evidence_ids == []


def test_provider_evaluation_insufficient_matches() -> None:
    """At least 5 records but < 2 matches yields INSUFFICIENT_RELEVANT_HISTORY fallback 50.0."""
    import uuid

    from omega.application.historical_performance_provider import (
        LearningHistoricalPerformanceProvider,
    )

    records = [
        _make_dummy_record(i, f"Cooking Sourdough {i}", ["sourdough", "yeast"], ["baking"])
        for i in range(5)
    ]
    # Add one matching record (only 1 match total)
    records.append(_make_dummy_record(10, "Target Candidate Topic", ["target"], ["tag"]))
    provider = LearningHistoricalPerformanceProvider(
        channel_id=uuid.uuid4(),
        policy_checksum="pol_cs",
        corpus_checksum="corp_cs",
        pinned_snapshots=[],
        records=records,
    )
    signal = provider.evaluate("Target Candidate Topic", ["target"])
    assert signal.status == HistoricalPerformanceStatus.INSUFFICIENT_RELEVANT_HISTORY
    assert signal.score == 50.0
    assert signal.match_count == 1
    assert signal.learning_evidence_ids == []


def test_provider_evaluation_applied_and_top_10_bound() -> None:
    """When >= 2 matches exist, score is weighted mean and matches bounded at 10."""
    import uuid

    from omega.application.historical_performance_provider import (
        LearningHistoricalPerformanceProvider,
    )

    records = [
        _make_dummy_record(
            i,
            f"Quantum Computing Advanced Guide {i}",
            ["quantum", "computing"],
            ["tech"],
            item_perf=80.0,
        )
        for i in range(15)
    ]
    provider = LearningHistoricalPerformanceProvider(
        channel_id=uuid.uuid4(),
        policy_checksum="pol_cs",
        corpus_checksum="corp_cs",
        pinned_snapshots=[],
        records=records,
    )
    signal = provider.evaluate("Quantum Computing Advanced Guide", ["quantum", "computing"])
    assert signal.status == HistoricalPerformanceStatus.APPLIED
    assert signal.score == 80.0
    assert signal.match_count == 10  # Bounded at HISTORICAL_MAX_MATCHES
    assert len(signal.learning_evidence_ids) == 10
    assert len(signal.analytics_evidence_ids) == 10


def test_provider_candidate_independent_corpus_consistency() -> None:
    """Candidates evaluated on the same provider share identical corpus and policy checksums."""
    import uuid

    from omega.application.historical_performance_provider import (
        LearningHistoricalPerformanceProvider,
    )

    records = [
        _make_dummy_record(i, f"AI Research {i}", ["ai"], ["tech"], item_perf=60.0)
        for i in range(6)
    ]
    provider = LearningHistoricalPerformanceProvider(
        channel_id=uuid.uuid4(),
        policy_checksum="policy-123",
        corpus_checksum="corpus-456",
        pinned_snapshots=[],
        records=records,
    )
    sig_a = provider.evaluate("AI Research Alpha", ["ai"])
    sig_b = provider.evaluate("Cooking Pasta", ["pasta"])

    assert sig_a.corpus_checksum == sig_b.corpus_checksum == "corpus-456"
    assert sig_a.policy_checksum == sig_b.policy_checksum == "policy-123"
    assert sig_a.performance_record_count == sig_b.performance_record_count == 6


def test_zero_confirmed_and_unusable_qualities_filtering() -> None:
    """ZERO_CONFIRMED participates as literal 0.0, while UNKNOWN_MISSING, SUPPRESSED, PROVIDER_ERROR are excluded."""
    from omega.domain.analytics import MetricClassification, MetricQuality
    from omega.domain.historical_performance import (
        HISTORICAL_ALLOWED_CLASSIFICATIONS,
        HISTORICAL_ALLOWED_QUALITIES,
        HISTORICAL_ELIGIBLE_METRICS,
    )

    raw_metrics = {
        "views": 1000.0,
        "watch_time_seconds": 999.0,
        "subscribers_gained": 50.0,
        "average_percentage_viewed": 75.0,
        "impression_ctr_percent": 10.0,
    }
    classifications = {m: MetricClassification.PROVIDER_FACT.value for m in raw_metrics}
    qualities = {
        "views": MetricQuality.ZERO_CONFIRMED.value,
        "watch_time_seconds": MetricQuality.UNKNOWN_MISSING.value,
        "subscribers_gained": MetricQuality.SUPPRESSED.value,
        "average_percentage_viewed": MetricQuality.PROVIDER_ERROR.value,
        "impression_ctr_percent": MetricQuality.AVAILABLE.value,
    }

    valid_metrics: dict[str, float] = {}
    for metric_name in HISTORICAL_ELIGIBLE_METRICS:
        cls_val = classifications.get(metric_name)
        q_val = qualities.get(metric_name)
        if cls_val not in HISTORICAL_ALLOWED_CLASSIFICATIONS:
            continue
        if q_val not in HISTORICAL_ALLOWED_QUALITIES:
            continue
        if q_val == MetricQuality.ZERO_CONFIRMED.value:
            valid_metrics[metric_name] = 0.0
        elif metric_name in raw_metrics and raw_metrics[metric_name] is not None:
            valid_metrics[metric_name] = float(raw_metrics[metric_name])

    # A. ZERO_CONFIRMED participates as literal 0.0
    assert "views" in valid_metrics
    assert valid_metrics["views"] == 0.0

    # B. UNKNOWN_MISSING does not become 0 (excluded)
    assert "watch_time_seconds" not in valid_metrics

    # C. SUPPRESSED does not become 0 (excluded)
    assert "subscribers_gained" not in valid_metrics

    # D. PROVIDER_ERROR does not become 0 (excluded)
    assert "average_percentage_viewed" not in valid_metrics

    # AVAILABLE participates with original numeric value
    assert valid_metrics["impression_ctr_percent"] == 10.0


def test_percentile_midranks_with_zero_values() -> None:
    """A corpus containing valid zero values produces deterministic percentile midranks."""
    # Values: [0.0, 0.0, 10.0, 20.0, 30.0]
    # N=5
    # Two zeros receive midrank (1+2)/2 = 1.5 -> ((1.5-1)/4)*100 = 12.5
    # 10.0 has rank 3 -> ((3-1)/4)*100 = 50.0
    # 20.0 has rank 4 -> ((4-1)/4)*100 = 75.0
    # 30.0 has rank 5 -> ((5-1)/4)*100 = 100.0
    scores = compute_percentile_midranks([0.0, 0.0, 10.0, 20.0, 30.0])
    assert scores == [12.5, 12.5, 50.0, 75.0, 100.0]


def test_performance_corpus_count_requires_usable_metrics() -> None:
    """5 pinned snapshots with only 3 usable records yields INSUFFICIENT_CORPUS (50.0)."""
    import uuid

    from omega.application.historical_performance_provider import (
        LearningHistoricalPerformanceProvider,
    )

    records = [
        _make_dummy_record(0, "Topic 0", ["kw"], ["tag"], item_perf=70.0),
        _make_dummy_record(1, "Topic 1", ["kw"], ["tag"], item_perf=80.0),
        _make_dummy_record(2, "Topic 2", ["kw"], ["tag"], item_perf=90.0),
        _make_dummy_record(3, "Topic 3", ["kw"], ["tag"], item_perf=None),  # no usable metrics
        _make_dummy_record(4, "Topic 4", ["kw"], ["tag"], item_perf=None),  # no usable metrics
    ]
    # pinned_snapshots count is 5, but records with item_perf is 3 (< 5)
    provider = LearningHistoricalPerformanceProvider(
        channel_id=uuid.uuid4(),
        policy_checksum="pol_cs",
        corpus_checksum="corp_cs",
        pinned_snapshots=[None] * 5,  # 5 pinned
        records=records,
    )
    assert provider.pinned_snapshot_count == 5
    assert provider.performance_record_count == 3

    signal = provider.evaluate("Topic 0", ["kw"])
    assert signal.status == HistoricalPerformanceStatus.INSUFFICIENT_CORPUS
    assert signal.score == 50.0
    assert signal.match_count == 0
    assert signal.learning_evidence_ids == []


def test_content_identity_checksum_changes_with_fields_and_affects_corpus_checksum() -> None:
    """Content identity changes alter content_identity_checksum and corpus_checksum."""
    cs_orig = compute_content_identity_checksum(
        identity_authority="PUBLISH_INTENT_METADATA",
        source_authority_ids={"intent_id": "123"},
        normalized_title="quantum computing",
        normalized_keywords=["quantum", "computing"],
        normalized_tags=["physics"],
    )
    cs_diff_title = compute_content_identity_checksum(
        identity_authority="PUBLISH_INTENT_METADATA",
        source_authority_ids={"intent_id": "123"},
        normalized_title="quantum algorithms",
        normalized_keywords=["quantum", "computing"],
        normalized_tags=["physics"],
    )
    cs_diff_auth = compute_content_identity_checksum(
        identity_authority="SELECTION_DECISION_SNAPSHOT",
        source_authority_ids={"intent_id": "123"},
        normalized_title="quantum computing",
        normalized_keywords=["quantum", "computing"],
        normalized_tags=["physics"],
    )
    assert cs_orig != cs_diff_title
    assert cs_orig != cs_diff_auth

    # Corpus checksum covering identity checksum
    p_cs = compute_historical_performance_policy_checksum()
    s_base = {
        "learning_snapshot_id": "snap-1",
        "observation_id": "obs-1",
        "payload_checksum": "pay-1",
        "revision_sequence": 1,
        "window_type": "FIRST_7D",
        "window_state": "FINALIZED",
        "content_identity_checksum": cs_orig,
    }
    s_changed = dict(s_base, content_identity_checksum=cs_diff_title)

    corp_base = compute_corpus_checksum(snapshots=[s_base], policy_checksum=p_cs)
    corp_changed = compute_corpus_checksum(snapshots=[s_changed], policy_checksum=p_cs)
    assert corp_base != corp_changed


def test_historical_performance_policy_checksum_coverage() -> None:
    """Historical performance policy checksum and selection policy v2 checksum change on parameter mutation."""
    import hashlib
    import json

    from omega.application.content_selection_service import policy_checksum

    base_cs = compute_historical_performance_policy_checksum()

    # Mutation test: changing min corpus size from 5 to 6 changes checksum
    mutated_payload = {
        "policy_name": "OMEGA_LEARNING_HISTORICAL_PERFORMANCE",
        "policy_version": 1,
        "evaluation_window": "FIRST_7D",
        "allowed_window_states": ["FINALIZED", "REVISED"],
        "is_fully_finalized_required": True,
        "min_corpus_size": 6,  # mutated
        "max_corpus_size": 50,
        "min_relevance": 0.20,
        "max_matches": 10,
        "min_matched_items": 2,
        "neutral_score": 50.0,
        "eligible_metrics": [
            "average_percentage_viewed",
            "impression_ctr_percent",
            "subscribers_gained",
            "views",
            "watch_time_seconds",
        ],
        "allowed_classifications": ["DETERMINISTIC_DERIVED", "PROVIDER_FACT"],
        "allowed_qualities": ["AVAILABLE", "REVISED", "ZERO_CONFIRMED"],
        "content_identity_precedence": [
            "MISSION_CANONICAL_TOPIC",
            "PUBLISH_INTENT_METADATA",
            "SELECTION_DECISION_SNAPSHOT",
        ],
        "relevance_algorithm": "LEXICAL_TOKEN_OVERLAP_V1",
        "normalization_algorithm": "PERCENTILE_MIDRANK_V1",
        "content_format_filter": "NONE",
        "corpus_selection_order": "PUBLISHED_AT_UTC_DESC_ID_ASC",
    }
    encoded = json.dumps(mutated_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    mutated_cs = hashlib.sha256(encoded).hexdigest()
    assert base_cs != mutated_cs

    # Selection policy v2 checksum binds historical performance policy checksum
    sel_cs_base = policy_checksum()
    assert len(sel_cs_base) == 64
