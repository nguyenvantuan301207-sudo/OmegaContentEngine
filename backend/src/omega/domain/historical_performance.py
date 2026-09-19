"""Domain models, algorithms, and policies for P19-E historical performance feedback."""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from omega.application.duplicate_detector import normalize_text, tokenize
from omega.domain.analytics import MetricClassification, MetricQuality, WindowState, WindowType

HISTORICAL_PERFORMANCE_POLICY_NAME = "OMEGA_LEARNING_HISTORICAL_PERFORMANCE"
HISTORICAL_PERFORMANCE_POLICY_VERSION = 1

HISTORICAL_EVALUATION_WINDOW = WindowType.FIRST_7D.value
HISTORICAL_ALLOWED_WINDOW_STATES = (WindowState.FINALIZED.value, WindowState.REVISED.value)
HISTORICAL_FULLY_FINALIZED_REQUIRED = True

HISTORICAL_MAX_CORPUS_SIZE = 50
HISTORICAL_MIN_CORPUS_SIZE = 5
HISTORICAL_MIN_RELEVANCE = 0.20
HISTORICAL_MAX_MATCHES = 10
HISTORICAL_MIN_MATCHED_ITEMS = 2
HISTORICAL_NEUTRAL_SCORE = 50.0

HISTORICAL_ELIGIBLE_METRICS = (
    "average_percentage_viewed",
    "impression_ctr_percent",
    "subscribers_gained",
    "views",
    "watch_time_seconds",
)

HISTORICAL_ALLOWED_CLASSIFICATIONS = (
    MetricClassification.PROVIDER_FACT.value,
    MetricClassification.DETERMINISTIC_DERIVED.value,
)

HISTORICAL_ALLOWED_QUALITIES = (
    MetricQuality.AVAILABLE.value,
    MetricQuality.ZERO_CONFIRMED.value,
    MetricQuality.REVISED.value,
)

HISTORICAL_CONTENT_IDENTITY_PRECEDENCE = (
    "SELECTION_DECISION_SNAPSHOT",
    "MISSION_CANONICAL_TOPIC",
    "PUBLISH_INTENT_METADATA",
)
HISTORICAL_CONTENT_IDENTITY_VERSION = 1

HISTORICAL_RELEVANCE_ALGORITHM = "LEXICAL_TOKEN_OVERLAP_V1"
HISTORICAL_NORMALIZATION_ALGORITHM = "PERCENTILE_MIDRANK_V1"
HISTORICAL_CONTENT_FORMAT_FILTER = "NONE"
HISTORICAL_CORPUS_SELECTION_ORDER = "PUBLISHED_AT_UTC_DESC_ID_ASC"


class HistoricalPerformanceStatus(enum.StrEnum):
    """Operational statuses for historical-performance signal evaluation."""

    APPLIED = "APPLIED"
    INSUFFICIENT_CORPUS = "INSUFFICIENT_CORPUS"
    INSUFFICIENT_RELEVANT_HISTORY = "INSUFFICIENT_RELEVANT_HISTORY"


class ContentIdentityAuthority(enum.StrEnum):
    """Precedence authority for historical content identity."""

    SELECTION_DECISION_SNAPSHOT = "SELECTION_DECISION_SNAPSHOT"
    MISSION_CANONICAL_TOPIC = "MISSION_CANONICAL_TOPIC"
    PUBLISH_INTENT_METADATA = "PUBLISH_INTENT_METADATA"
    UNRESOLVED = "UNRESOLVED"


class HistoricalPerformanceIntegrityError(RuntimeError):
    """Raised when a pinned pointer and snapshot pair is internally inconsistent."""


def canonical_json_hash(payload: dict) -> str:
    """Compute deterministic SHA-256 over canonical sorted-key compact JSON."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compute_historical_performance_policy_checksum() -> str:
    """Deterministic checksum of stable policy properties affecting score calculation."""
    policy_payload = {
        "policy_name": HISTORICAL_PERFORMANCE_POLICY_NAME,
        "policy_version": HISTORICAL_PERFORMANCE_POLICY_VERSION,
        "source_authority": "LEARNING_INPUT_LATEST_POINTER",
        "evaluation_window": HISTORICAL_EVALUATION_WINDOW,
        "allowed_window_states": sorted(HISTORICAL_ALLOWED_WINDOW_STATES),
        "fully_finalized_required": HISTORICAL_FULLY_FINALIZED_REQUIRED,
        "corpus_selection_order": HISTORICAL_CORPUS_SELECTION_ORDER,
        "maximum_corpus_size": HISTORICAL_MAX_CORPUS_SIZE,
        "minimum_corpus_size": HISTORICAL_MIN_CORPUS_SIZE,
        "minimum_matched_history_size": HISTORICAL_MIN_MATCHED_ITEMS,
        "metric_set": sorted(HISTORICAL_ELIGIBLE_METRICS),
        "accepted_metric_qualities": sorted(HISTORICAL_ALLOWED_QUALITIES),
        "accepted_metric_classifications": sorted(HISTORICAL_ALLOWED_CLASSIFICATIONS),
        "content_identity_precedence": list(HISTORICAL_CONTENT_IDENTITY_PRECEDENCE),
        "content_identity_version": HISTORICAL_CONTENT_IDENTITY_VERSION,
        "content_format_filter": HISTORICAL_CONTENT_FORMAT_FILTER,
        "relevance_algorithm": {
            "name": HISTORICAL_RELEVANCE_ALGORITHM,
            "version": 1,
            "minimum_relevance_threshold": HISTORICAL_MIN_RELEVANCE,
            "maximum_relevant_records": HISTORICAL_MAX_MATCHES,
        },
        "performance_normalization_algorithm": {
            "name": HISTORICAL_NORMALIZATION_ALGORITHM,
            "version": 1,
            "min_valid_observations": HISTORICAL_MIN_CORPUS_SIZE,
            "formula": "((midrank - 1) / (N - 1)) * 100",
            "tie_handling": "ARITHMETIC_MIDRANK",
            "homogeneous_fallback": 50.0,
        },
        "neutral_score": HISTORICAL_NEUTRAL_SCORE,
    }
    return canonical_json_hash(policy_payload)


def compute_content_identity_checksum(
    *,
    identity_authority: str,
    source_authority_ids: dict[str, str | None],
    normalized_title: str,
    normalized_keywords: list[str],
    normalized_tags: list[str],
) -> str:
    """Deterministic hash of bounded content identity used for lexical relevance."""
    payload = {
        "identity_authority": identity_authority,
        "source_authority_ids": {k: v for k, v in sorted(source_authority_ids.items()) if v},
        "normalized_title": normalized_title,
        "normalized_keywords": sorted(normalized_keywords),
        "normalized_tags": sorted(normalized_tags),
    }
    return canonical_json_hash(payload)


def compute_corpus_checksum(
    *,
    snapshots: list[dict],
    policy_checksum: str,
) -> str:
    """Compute deterministic SHA-256 of pinned historical corpus inputs."""
    sorted_snapshots = sorted(snapshots, key=lambda s: str(s["learning_snapshot_id"]))
    payload = {
        "policy_checksum": policy_checksum,
        "snapshots": [
            {
                "learning_snapshot_id": str(s["learning_snapshot_id"]),
                "observation_id": str(s["observation_id"]),
                "payload_checksum": s["payload_checksum"],
                "revision_sequence": s["revision_sequence"],
                "window_type": s["window_type"],
                "window_state": s["window_state"],
                "content_identity_checksum": s["content_identity_checksum"],
            }
            for s in sorted_snapshots
        ],
    }
    return canonical_json_hash(payload)


def compute_percentile_midranks(values: list[float]) -> list[float]:
    """PERCENTILE_MIDRANK_V1: Map numeric values to [0, 100] percentile scores using midranks.

    Rules:
    - Sort ascending.
    - Equal values receive arithmetic midrank using 1-based ranks.
    - When N > 1:
        if all values are identical: score = 50.0
        else: score = ((midrank - 1.0) / (N - 1.0)) * 100.0
    - Clamped to [0.0, 100.0].
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [50.0]

    # Check for complete homogeneity
    min_val = min(values)
    max_val = max(values)
    if min_val == max_val:
        return [50.0] * n

    # Indexed values: (value, original_index)
    indexed = sorted(enumerate(values), key=lambda x: x[1])

    scores = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and indexed[j + 1][1] == indexed[j][1]:
            j += 1
        # Arithmetic midrank: 1-based
        # sum from (i+1) to (j+1) divided by count
        midrank = (i + 1 + j + 1) / 2.0
        percentile = ((midrank - 1.0) / (n - 1.0)) * 100.0
        clamped = max(0.0, min(100.0, percentile))
        for k in range(i, j + 1):
            orig_idx = indexed[k][0]
            scores[orig_idx] = clamped
        i = j + 1

    return scores


def extract_tokens_from_text(text: str) -> set[str]:
    """Tokenize normalized text into a deterministic set of words."""
    norm = normalize_text(text)
    return tokenize(norm)


def extract_tokens_from_list(items: list[str]) -> set[str]:
    """Tokenize a list of phrases into a union of token sets."""
    tokens: set[str] = set()
    for item in items:
        norm = normalize_text(item)
        tokens.update(tokenize(norm))
    return tokens


def compute_lexical_relevance(
    *,
    candidate_title: str,
    candidate_keywords: list[str],
    historical_title: str,
    historical_keywords: list[str],
    historical_tags: list[str],
) -> tuple[float, float, float]:
    """LEXICAL_TOKEN_OVERLAP_V1.

    Returns:
        (relevance_score, token_jaccard, keyword_coverage)
    """
    candidate_title_tokens = extract_tokens_from_text(candidate_title)
    candidate_keyword_tokens = extract_tokens_from_list(candidate_keywords)
    candidate_tokens = candidate_title_tokens | candidate_keyword_tokens

    historical_title_tokens = extract_tokens_from_text(historical_title)
    historical_keyword_tokens = extract_tokens_from_list(historical_keywords)
    historical_tag_tokens = extract_tokens_from_list(historical_tags)
    historical_tokens = (
        historical_title_tokens | historical_keyword_tokens | historical_tag_tokens
    )

    # Token Jaccard
    intersection = candidate_tokens & historical_tokens
    union = candidate_tokens | historical_tokens
    token_jaccard = len(intersection) / len(union) if union else 0.0

    # Keyword Coverage
    if candidate_keyword_tokens:
        keyword_intersection = candidate_keyword_tokens & historical_tokens
        keyword_coverage = len(keyword_intersection) / len(candidate_keyword_tokens)
    else:
        keyword_coverage = 0.0

    relevance = max(0.0, min(1.0, max(token_jaccard, keyword_coverage)))
    return relevance, token_jaccard, keyword_coverage


@dataclass(frozen=True)
class HistoricalContentIdentity:
    """Resolved bounded identity of a historical piece of content."""

    identity_authority: ContentIdentityAuthority
    title: str
    keywords: list[str]
    tags: list[str]
    normalized_title: str
    normalized_keywords: list[str]
    normalized_tags: list[str]
    identity_checksum: str
    source_authority_ids: dict[str, str | None]


@dataclass(frozen=True)
class HistoricalRecord:
    """Detached, immutable historical record in the pinned corpus."""

    snapshot_id: UUID
    observation_id: UUID  # AnalyticsWindow ID
    publish_intent_id: UUID | None
    published_at_utc: datetime
    revision_sequence: int
    payload_checksum: str
    window_type: str
    window_state: str
    identity: HistoricalContentIdentity
    metric_values: dict[str, float]  # raw valid numeric values
    normalized_metrics: dict[str, float]  # normalized [0, 100] percentile scores
    historical_item_performance: float | None  # mean of normalized metrics, or None if none eligible


@dataclass(frozen=True)
class MatchedEvidenceItem:
    """Bounded, auditable operational evidence item for a matched historical record."""

    learning_snapshot_id: str
    analytics_observation_id: str
    publish_intent_id: str | None
    identity_authority: str
    relevance: float
    token_jaccard: float
    keyword_coverage: float
    historical_item_performance: float
    metrics_used: list[str]


@dataclass(frozen=True)
class HistoricalPerformanceSignal:
    """Candidate-specific historical-performance evaluation result."""

    score: float
    status: HistoricalPerformanceStatus
    policy_name: str
    policy_version: int
    policy_checksum: str
    corpus_checksum: str
    pinned_snapshot_count: int
    performance_record_count: int
    match_count: int
    learning_evidence_ids: list[str]
    analytics_evidence_ids: list[str]
    source_publish_intent_ids: list[str]
    metric_names_used: list[str]
    matched_evidence: list[dict]
