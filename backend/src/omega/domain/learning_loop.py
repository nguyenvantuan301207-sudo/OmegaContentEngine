"""P25-D Evidence-Based Learning Loop Domain Models, Enums, and Policies.

Answers:
"What has Omega learned from evidence, how strong is that learning,
and what should be considered for future productions?"

Core Invariants:
- Never converts correlation/association into causal truth.
- P25-C completed, mature experiments produce CAUSAL evidence (Tier 1/2).
- P25-B performance data produces DESCRIPTIVE / ASSOCIATIONAL evidence (Tier 3/4).
- Strictly non-mutating: produces CandidateAdaptation with status PROPOSED.
- Never directly mutates ChannelDNARevision, CreativeStylePlan, or NarrativePlan.
"""

from __future__ import annotations

import enum
import hashlib
import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ============================================================================
# 1. Evidence Hierarchy & Causality Semantics
# ============================================================================


class EvidenceTier(enum.IntEnum):
    """Explicit epistemic hierarchy for learning evidence (Section 4)."""

    TIER_1_CAUSAL_EXPERIMENT = 1  # Valid completed P25-C causal attribution result
    TIER_2_REPLICATED_CAUSAL = 2  # Repeated consistent causal experiment results
    TIER_3_CONTROLLED_OBSERVATION = (
        3  # Multi-production observational evidence with controlled context
    )
    TIER_4_DESCRIPTIVE_ASSOCIATION = 4  # Single-production descriptive association (P25-B)
    TIER_5_HEURISTIC = 5  # Heuristic or insufficient evidence basis


class CausalityStatus(enum.StrEnum):
    """Authoritative causal classification (Section 6)."""

    CAUSAL = "CAUSAL"  # Strictly reserved for Tier 1 and Tier 2 from valid P25-C experiments
    ASSOCIATIONAL = "ASSOCIATIONAL"  # Correlational patterns across multiple controlled cohorts
    DESCRIPTIVE = "DESCRIPTIVE"  # Observational metrics directly from P25-B performance
    HEURISTIC = "HEURISTIC"  # Rule-based prior or expert heuristic
    INSUFFICIENT = "INSUFFICIENT"  # Insufficient sample or delayed maturity


class LearningConfidence(enum.StrEnum):
    """Discrete, deterministic confidence classification (Section 16)."""

    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


class LearningHypothesisStatus(enum.StrEnum):
    """Deterministic lifecycle state for learning hypotheses (Section 8)."""

    PROPOSED = "PROPOSED"
    EVALUATING = "EVALUATING"
    SUPPORTED = "SUPPORTED"
    WEAKENED = "WEAKENED"
    CONTRADICTED = "CONTRADICTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    RETIRED = "RETIRED"


class RecommendationAction(enum.StrEnum):
    """Actionable recommendation categories (Section 18)."""

    CONSIDER_TITLE_STYLE = "CONSIDER_TITLE_STYLE"
    CONSIDER_THUMBNAIL_STYLE = "CONSIDER_THUMBNAIL_STYLE"
    CONSIDER_PACING_CHANGE = "CONSIDER_PACING_CHANGE"
    CONSIDER_VISUAL_DENSITY = "CONSIDER_VISUAL_DENSITY"
    CONSIDER_AUDIO_DENSITY = "CONSIDER_AUDIO_DENSITY"
    CONSIDER_FORMAT_STRATEGY = "CONSIDER_FORMAT_STRATEGY"
    RUN_FOLLOWUP_EXPERIMENT = "RUN_FOLLOWUP_EXPERIMENT"
    KEEP_CURRENT_POLICY = "KEEP_CURRENT_POLICY"


class AdaptationStatus(enum.StrEnum):
    """Lifecycle status of candidate adaptations (Section 20)."""

    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


# ============================================================================
# 2. Domain Data Models
# ============================================================================


class LearningEvidence(BaseModel):
    """Typed, immutable evidence unit referencing durable upstream sources (Section 5)."""

    evidence_id: UUID = Field(default_factory=uuid4)
    evidence_tier: EvidenceTier
    causal_status: CausalityStatus
    source_phase: str = Field(..., description="Upstream authority phase, e.g. P25-C or P25-B.")
    source_ids: list[str] = Field(
        ..., min_length=1, description="Exact durable IDs of upstream records."
    )
    channel_id: UUID
    target_scope_type: str = Field(default="MEDIA_ARTIFACT")
    target_scope_id: str = Field(default="")
    content_format: str = Field(
        default="DEFAULT", description="Format context, e.g. TUTORIAL, SHORT, ESSAY."
    )
    content_pillar: str = Field(default="GENERAL", description="Content pillar context.")
    creative_dimensions: list[str] = Field(
        default_factory=list, description="Affected creative dimensions."
    )
    metric: str = Field(..., description="Canonical metric evaluated.")
    effect_direction: str = Field(..., description="POSITIVE, NEGATIVE, or NEUTRAL.")
    effect_magnitude: float = Field(
        ..., description="Signed effect size or relative lift (e.g. +0.24 for +24%)."
    )
    sample_basis: dict[str, Any] = Field(
        default_factory=dict, description="Sample counts, impressions, or exposures."
    )
    quality_score: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Deterministic quality weighting."
    )
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)

    @field_validator("causal_status")
    @classmethod
    def validate_causality_tier_alignment(cls, v: CausalityStatus, info) -> CausalityStatus:
        """P25-B performance alone must NEVER be marked CAUSAL (Section 6, Section 28)."""
        tier = info.data.get("evidence_tier")
        source = info.data.get("source_phase")
        if v == CausalityStatus.CAUSAL:
            if tier not in (
                EvidenceTier.TIER_1_CAUSAL_EXPERIMENT,
                EvidenceTier.TIER_2_REPLICATED_CAUSAL,
            ):
                raise ValueError(
                    "Only Tier 1 or Tier 2 evidence from valid experiments may be marked CAUSAL."
                )
            if source == "P25-B":
                raise ValueError("P25-B performance data alone must NEVER be marked CAUSAL.")
        return v


class LearningHypothesis(BaseModel):
    """Bounded, falsifiable learning claim across creative and outcome dimensions (Section 7)."""

    hypothesis_id: UUID = Field(default_factory=uuid4)
    channel_id: UUID
    target_scope_type: str = "MEDIA_ARTIFACT"
    target_scope_id: str = ""
    creative_dimension: str = Field(
        ..., description="Single creative dimension, e.g. TITLE, THUMBNAIL."
    )
    content_pillar: str = "GENERAL"
    content_format: str = "DEFAULT"
    predicted_metric: str = Field(..., description="Canonical metric under test.")
    predicted_direction: str = Field(..., description="POSITIVE or NEGATIVE.")
    statement: str = Field(..., min_length=10, description="Specific, falsifiable learning claim.")
    status: LearningHypothesisStatus = LearningHypothesisStatus.PROPOSED
    evidence_requirements: dict[str, Any] = Field(
        default_factory=lambda: {"min_tier": 2, "min_sample_size": 2000, "min_replications": 1}
    )
    revision_number: int = Field(default=1, ge=1)
    supersedes_hypothesis_id: UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)

    def compute_dedupe_key(self) -> str:
        """Compute normalized deterministic identity (Section 9)."""
        raw = (
            f"{self.channel_id}:{self.creative_dimension}:{self.content_pillar}:"
            f"{self.content_format}:{self.predicted_metric}:{self.predicted_direction}:"
            f"{self.target_scope_type}:{self.target_scope_id}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LearningInsight(BaseModel):
    """Synthesized conclusion derived from evaluated evidence (Section 17)."""

    insight_id: UUID = Field(default_factory=uuid4)
    hypothesis_id: UUID
    evaluation_id: UUID | None = None
    hypothesis_revision_id: UUID | None = None
    policy_revision_id: UUID | None = None
    scope: str
    summary: str
    causal_status: CausalityStatus
    confidence: LearningConfidence
    supporting_evidence_ids: list[UUID] = Field(default_factory=list)
    contradicting_evidence_ids: list[UUID] = Field(default_factory=list)
    metric_impacts: dict[str, float] = Field(default_factory=dict)
    tradeoffs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    policy_version: str = "1.0.0"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


class CandidateAdaptation(BaseModel):
    """Proposed, unapplied modification to future creative policies (Section 20).

    STRICT INVARIANT: Starts in PROPOSED status. Never applied automatically.
    """

    adaptation_id: UUID = Field(default_factory=uuid4)
    target_authority: str = Field(
        ..., description="ChannelDNA, CreativeStylePlan, PackagingPlan, etc."
    )
    target_dimension: str
    current_value: Any
    proposed_value: Any
    supporting_evidence_ids: list[UUID] = Field(default_factory=list)
    confidence: LearningConfidence
    scope: str
    validation_requirement: str = Field(
        default="Requires explicit human producer approval before drafting ChannelDNA revision."
    )
    status: AdaptationStatus = AdaptationStatus.PROPOSED
    proposed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decision_at: datetime | None = None
    decision_reason: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


class LearningRecommendation(BaseModel):
    """Actionable recommendation answering what should be considered next (Section 18, 34)."""

    recommendation_id: UUID = Field(default_factory=uuid4)
    action: RecommendationAction
    target_authority: str
    proposed_change: str
    scope: str
    supporting_insight_id: UUID
    confidence: LearningConfidence
    risk_assessment: str
    expected_metrics: dict[str, str]
    tradeoffs: list[str] = Field(default_factory=list)
    candidate_adaptation: CandidateAdaptation | None = None
    ranking_score: float = Field(default=0.5, ge=0.0, le=1.0)
    explanation: dict[str, str] = Field(
        default_factory=lambda: {
            "what_supports_this": "",
            "what_contradicts_it": "",
            "how_strong_is_evidence": "",
            "where_does_it_apply": "",
            "what_are_the_risks": "",
            "what_should_happen_next": "",
        }
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


# ============================================================================
# 3. Learning Evaluation Engine & Deterministic Policies
# ============================================================================


class LearningPolicy(BaseModel):
    """Versioned configuration for evidence weighting, thresholds, and ranking (Section 42)."""

    policy_version: str = "1.0.0"
    recency_half_life_days: float = Field(default=90.0, gt=0)
    min_causal_replications_for_high: int = Field(default=2, ge=1)
    min_sample_size_per_evidence: int = Field(default=1000, ge=1)
    tradeoff_negative_threshold_pct: float = -5.0  # -5% drop triggers explicit tradeoff surfacing
    generalization_min_replications: int = Field(default=3, ge=1)
    causal_weight: float = Field(default=1.5, gt=0)
    support_ratio: float = Field(default=2.0, gt=0)
    followup_rank: float = Field(default=0.85, ge=0, le=1)
    high_rank: float = Field(default=0.9, ge=0, le=1)
    moderate_rank: float = Field(default=0.75, ge=0, le=1)
    lift_rank_weight: float = Field(default=0.2, ge=0)
    brand_safety_policy_version: str = "P24-producer-review-v1"
    algorithm_version: str = "p25d-evaluation-v1"

    model_config = ConfigDict(frozen=True)

    def compute_recency_weight(self, observed_at: datetime, as_of: datetime | None = None) -> float:
        """Compute exponential temporal decay weight [0.1, 1.0] (Section 14)."""
        reference_time = as_of or datetime.now(UTC)
        elapsed_days = max(0.0, (reference_time - observed_at).total_seconds() / 86400.0)
        decay = math.exp(-math.log(2.0) * (elapsed_days / self.recency_half_life_days))
        return max(0.10, min(1.0, decay))
