"""Domain models, enums, statistical methods, and policies for OMEGA-013 Learning Engine."""

from __future__ import annotations

import enum
import hashlib
import json
import math
import struct
from typing import Any
from uuid import UUID

from omega.domain.analytics import WindowType

# ============================================================================
# Enums
# ============================================================================


class HypothesisStatus(enum.StrEnum):
    """Lifecycle states of an investigational hypothesis."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    INCONCLUSIVE = "INCONCLUSIVE"
    SUPPORTED = "SUPPORTED"
    WEAKENED = "WEAKENED"
    CONTRADICTED = "CONTRADICTED"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"


class ConfidenceClass(enum.StrEnum):
    """Discrete, deterministic confidence classification."""

    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class KnowledgeStatus(enum.StrEnum):
    """Lifecycle states of a stored institutional memory claim."""

    ACTIVE = "ACTIVE"
    WEAKENED = "WEAKENED"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"
    RETRACTED = "RETRACTED"


class EvidenceType(enum.StrEnum):
    """Epistemic nature of supporting evidence."""

    OBSERVATIONAL = "OBSERVATIONAL"
    CONTROLLED_EXPERIMENT = "CONTROLLED_EXPERIMENT"
    MANUAL_LABEL = "MANUAL_LABEL"
    EXTERNAL_PRIOR = "EXTERNAL_PRIOR"


class LearningEventType(enum.StrEnum):
    """Append-only audit event types."""

    OBSERVATION_INGESTED = "OBSERVATION_INGESTED"
    INPUT_SUPERSEDED = "INPUT_SUPERSEDED"
    FEATURE_CREATED = "FEATURE_CREATED"
    COHORT_REBUILT = "COHORT_REBUILT"
    BASELINE_CREATED = "BASELINE_CREATED"
    HYPOTHESIS_PROPOSED = "HYPOTHESIS_PROPOSED"
    HYPOTHESIS_EVALUATED = "HYPOTHESIS_EVALUATED"
    HYPOTHESIS_REVISED = "HYPOTHESIS_REVISED"
    KNOWLEDGE_CREATED = "KNOWLEDGE_CREATED"
    KNOWLEDGE_SUPERSEDED = "KNOWLEDGE_SUPERSEDED"
    SOURCE_REVISION_PROPAGATED = "SOURCE_REVISION_PROPAGATED"


class LearningJobStatus(enum.StrEnum):
    """Execution states for learning background jobs."""

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEFERRED = "DEFERRED"


class ContentFormat(enum.StrEnum):
    """Standardized video content formats."""

    SHORT = "SHORT"
    LONG_FORM = "LONG_FORM"


class DurationBucket(enum.StrEnum):
    """Coarse duration classification for comparability."""

    UNDER_1M = "UNDER_1M"
    ONE_TO_FIVE_M = "1M_TO_5M"
    FIVE_TO_FIFTEEN_M = "5M_TO_15M"
    FIFTEEN_TO_THIRTY_M = "15M_TO_30M"
    OVER_30M = "OVER_30M"


# ============================================================================
# Versioned Policies
# ============================================================================

EFFECT_POLICY_ID: str = "OMEGA_EFFECT_POLICY_V1"
EFFECT_POLICY_VERSION: int = 1

CONFIDENCE_POLICY_ID: str = "OMEGA_CONFIDENCE_V1"
CONFIDENCE_POLICY_VERSION: int = 1

SELECTION_POLICY_VERSION: int = 1

OMEGA_EFFECT_POLICY_V1: dict[str, dict[str, float | None]] = {
    "views": {
        "min_cliffs_delta": 0.28,
        "min_relative_delta_percent": 15.0,
        "min_absolute_delta": 100.0,
    },
    "watch_time_seconds": {
        "min_cliffs_delta": 0.28,
        "min_relative_delta_percent": 15.0,
        "min_absolute_delta": 300.0,
    },
    "average_view_percentage": {
        "min_cliffs_delta": 0.28,
        "min_relative_delta_percent": None,
        "min_absolute_delta": 5.0,
    },
    "ctr_percent": {
        "min_cliffs_delta": 0.20,
        "min_relative_delta_percent": None,
        "min_absolute_delta": 1.0,
    },
    "subscribers_gained": {
        "min_cliffs_delta": 0.30,
        "min_relative_delta_percent": 20.0,
        "min_absolute_delta": 5.0,
    },
}


# ============================================================================
# Deterministic Hashing & Advisory Lock Helpers
# ============================================================================


def compute_advisory_lock_key(namespace: str, *parts: str) -> int:
    """Derive deterministic signed 64-bit integer key for pg_advisory_xact_lock.

    Uses SHA-256 digest of UTF-8 encoded string. Never uses Python's process-randomized hash().
    """
    raw = f"{namespace}:" + ":".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return struct.unpack(">q", digest[:8])[0]


def compute_input_dedupe_key(
    observation_id: UUID, window_type: WindowType, revision_sequence: int
) -> str:
    raw = f"{observation_id}:{window_type.value}:{revision_sequence}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_feature_snapshot_key(
    input_snapshot_id: UUID, feature_schema_version: int, extractor_version: str
) -> str:
    raw = f"{input_snapshot_id}:{feature_schema_version}:{extractor_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_cohort_member_key(cohort_id: UUID, feature_snapshot_id: UUID) -> str:
    raw = f"{cohort_id}:{feature_snapshot_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_baseline_key(
    cohort_id: UUID,
    outcome_metric: str,
    evaluation_window: WindowType,
    baseline_version: int,
    member_ids_checksum: str,
) -> str:
    raw = f"{cohort_id}:{outcome_metric}:{evaluation_window.value}:{baseline_version}:{member_ids_checksum}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_hypothesis_definition_checksum(
    channel_id: UUID,
    cohort_id: UUID,
    factor_name: str,
    treatment_definition: dict[str, Any],
    control_definition: dict[str, Any],
    target_outcome_metric: str,
    target_evaluation_window: WindowType,
) -> str:
    payload = {
        "channel_id": str(channel_id),
        "cohort_id": str(cohort_id),
        "factor_name": factor_name,
        "treatment": treatment_definition,
        "control": control_definition,
        "target_outcome_metric": target_outcome_metric,
        "target_evaluation_window": target_evaluation_window.value,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_evaluation_key(
    hypothesis_id: UUID, evaluation_version: int, member_set_checksum: str
) -> str:
    raw = f"{hypothesis_id}:{evaluation_version}:{member_set_checksum}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_manifest_checksum(
    evaluation_id: UUID,
    treatment_snapshot_ids: list[UUID],
    control_snapshot_ids: list[UUID],
    treatment_vals: list[float],
    control_vals: list[float],
    member_set_checksum: str,
) -> str:
    payload = {
        "evaluation_id": str(evaluation_id),
        "treatment_snapshot_ids": sorted(str(u) for u in treatment_snapshot_ids),
        "control_snapshot_ids": sorted(str(u) for u in control_snapshot_ids),
        "treatment_vals": treatment_vals,
        "control_vals": control_vals,
        "member_set_checksum": member_set_checksum,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_knowledge_key(knowledge_family_id: UUID, revision_number: int) -> str:
    raw = f"{knowledge_family_id}:{revision_number}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_event_dedupe_key(
    event_type: str, aggregate_type: str, aggregate_id: UUID, sequence_or_time: str
) -> str:
    raw = f"{event_type}:{aggregate_type}:{aggregate_id}:{sequence_or_time}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_job_dedupe_key(channel_id: UUID, job_type: str, execution_identity: str) -> str:
    raw = f"{channel_id}:{job_type}:{execution_identity}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ============================================================================
# Statistical Methods
# ============================================================================


def compute_cliffs_delta(treatment: list[float], control: list[float]) -> float:
    """Compute Cliff's Delta effect size for ordinal/continuous non-parametric data.

    Returns float in [-1.0, 1.0].
    """
    n_t = len(treatment)
    n_c = len(control)
    if n_t == 0 or n_c == 0:
        return 0.0

    greater = 0
    less = 0
    for t in treatment:
        for c in control:
            if t > c:
                greater += 1
            elif t < c:
                less += 1

    return (greater - less) / (n_t * n_c)


def compute_hodges_lehmann_median_difference(treatment: list[float], control: list[float]) -> float:
    """Compute Hodges-Lehmann location estimator (median of all pairwise differences)."""
    if not treatment or not control:
        return 0.0

    diffs = [t - c for t in treatment for c in control]
    diffs.sort()
    n = len(diffs)
    mid = n // 2
    if n % 2 == 1:
        return diffs[mid]
    return (diffs[mid - 1] + diffs[mid]) / 2.0


def compute_mann_whitney_u(treatment: list[float], control: list[float]) -> tuple[float, float]:
    """Perform Mann-Whitney U test with tie correction and asymptotic normal approximation.

    Returns (u_stat, two_sided_p_value).
    """
    n1 = len(treatment)
    n2 = len(control)
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0

    # Combine and rank
    combined: list[tuple[float, int]] = [(val, 1) for val in treatment] + [
        (val, 2) for val in control
    ]
    combined.sort(key=lambda x: x[0])

    n_total = n1 + n2
    ranks = [0.0] * n_total
    i = 0
    tie_correction_sum = 0
    while i < n_total:
        j = i
        while j < n_total - 1 and combined[j][0] == combined[j + 1][0]:
            j += 1
        tie_length = j - i + 1
        avg_rank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        if tie_length > 1:
            tie_correction_sum += tie_length**3 - tie_length
        i = j + 1

    # Sum of ranks for treatment
    r1 = sum(ranks[k] for k in range(n_total) if combined[k][1] == 1)

    u1 = r1 - (n1 * (n1 + 1)) / 2.0
    u2 = (n1 * n2) - u1
    u = min(u1, u2)

    # Asymptotic normal approximation with continuity correction
    mu = (n1 * n2) / 2.0
    var_denominator = n_total * (n_total - 1)
    if var_denominator > 0:
        tie_adjustment = tie_correction_sum / (12 * var_denominator)
        sigma = math.sqrt(((n1 * n2) / 12.0) * ((n_total + 1) - tie_adjustment))
    else:
        sigma = 0.0

    if sigma == 0.0:
        return u, 1.0

    # Continuity correction
    z = (abs(u - mu) - 0.5) / sigma
    # Two-sided p-value using standard normal CDF approximation
    p_value = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))
    p_value = max(0.0, min(1.0, p_value))

    return u, p_value


def adjust_p_values_benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Adjust raw p-values using the Benjamini-Hochberg False Discovery Rate procedure."""
    m = len(p_values)
    if m == 0:
        return []

    # Sort indices by p-value
    sorted_indices = sorted(range(m), key=lambda k: p_values[k])
    sorted_p = [p_values[k] for k in sorted_indices]

    adjusted = [1.0] * m
    cummin = 1.0
    for i in range(m - 1, -1, -1):
        rank = i + 1
        adj = (sorted_p[i] * m) / rank
        cummin = min(cummin, adj)
        adjusted[sorted_indices[i]] = min(1.0, max(0.0, cummin))

    return adjusted


def compute_robust_z_score(
    value: float,
    median: float,
    mad: float,
    iqr: float,
    stddev: float,
) -> float | None:
    """Compute robust Z-score with deterministic fallback cascade.

    1. MAD > 0 -> (value - median) / (1.4826 * mad)
    2. IQR > 0 -> (value - median) / (iqr / 1.349)
    3. StdDev > 0 -> (value - median) / stddev
    4. Homogeneous sample (abs(value - median) < 1e-6) -> 0.0
    5. Degenerate -> None
    """
    if mad > 1e-6:
        return (value - median) / (1.4826 * mad)
    if iqr > 1e-6:
        return (value - median) / (iqr / 1.349)
    if stddev > 1e-6:
        return (value - median) / stddev
    if abs(value - median) < 1e-6:
        return 0.0
    return None


def compute_confidence(
    n_treatment: int,
    n_control: int,
    p_adjusted: float,
    cliffs_delta: float,
    min_cliffs_delta: float,
    consecutive_cycles_stable: int = 1,
    distinct_dna_revisions: int = 1,
    evidence_type: str = "OBSERVATIONAL",
) -> ConfidenceClass:
    """Evaluate deterministic ConfidenceClass per OMEGA_CONFIDENCE_V1."""
    min_n = min(n_treatment, n_control)

    if min_n < 10 or p_adjusted > 0.05 or abs(cliffs_delta) < min_cliffs_delta:
        return ConfidenceClass.VERY_LOW

    if min_n < 20 or consecutive_cycles_stable < 2:
        return ConfidenceClass.LOW

    if min_n < 35 or p_adjusted > 0.01 or consecutive_cycles_stable < 3:
        return ConfidenceClass.MODERATE

    if min_n < 50 or p_adjusted > 0.001 or distinct_dna_revisions < 2:
        return ConfidenceClass.HIGH

    if distinct_dna_revisions >= 2 or evidence_type == "CONTROLLED_EXPERIMENT":
        return ConfidenceClass.VERY_HIGH

    return ConfidenceClass.HIGH
