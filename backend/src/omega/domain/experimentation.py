"""Domain models, enums, and policies for P25-C Experimentation & Causal Attribution.

Answers:
"Under a valid controlled comparison, what difference can be attributed to the tested variant?"

Does NOT answer:
"What should Omega learn or change?" (P25-D learning loop)

Pure domain layer: zero infrastructure dependencies, zero vendor SDK calls, zero mutation of external accounts.
"""

from __future__ import annotations

import enum
import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omega.domain.creative_qa import CreativeQAStatus


# ── Canonical Enums ────────────────────────────────────────────────────────────


class ExperimentType(enum.StrEnum):
    """Categorical classification of the variable under test."""

    TITLE = "TITLE"
    THUMBNAIL = "THUMBNAIL"
    TITLE_AND_THUMBNAIL = "TITLE_AND_THUMBNAIL"
    DESCRIPTION = "DESCRIPTION"
    PACKAGING_BUNDLE = "PACKAGING_BUNDLE"
    CONTENT = "CONTENT"


class ChangeDimension(enum.StrEnum):
    """The specific single dimension manipulated by a variant."""

    TITLE = "TITLE"
    THUMBNAIL = "THUMBNAIL"
    TITLE_AND_THUMBNAIL = "TITLE_AND_THUMBNAIL"
    DESCRIPTION = "DESCRIPTION"
    PACKAGING_BUNDLE = "PACKAGING_BUNDLE"
    CONTENT = "CONTENT"


class ExperimentStatus(enum.StrEnum):
    """Strict lifecycle state machine for controlled experiments."""

    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    INVALIDATED = "INVALIDATED"


class VariantRole(enum.StrEnum):
    """Authoritative role of a variant within an experiment."""

    CONTROL = "CONTROL"
    TREATMENT = "TREATMENT"


class ExperimentUnit(enum.StrEnum):
    """Explicitly defined unit of randomization and exposure."""

    IMPRESSION = "IMPRESSION"
    VIEW = "VIEW"
    TIME_BUCKET = "TIME_BUCKET"
    PUBLICATION = "PUBLICATION"


class DataMaturityState(enum.StrEnum):
    """Evaluation data maturity status derived from P25-B freshness."""

    MATURE = "MATURE"
    WAITING_FOR_MATURITY = "WAITING_FOR_MATURITY"
    DELAYED = "DELAYED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INVALID = "INVALID"


class AttributionClassification(enum.StrEnum):
    """Authoritative causal outcome classification without forced winners."""

    INCONCLUSIVE = "INCONCLUSIVE"
    CONTROL_BETTER = "CONTROL_BETTER"
    TREATMENT_BETTER = "TREATMENT_BETTER"
    NO_MEANINGFUL_DIFFERENCE = "NO_MEANINGFUL_DIFFERENCE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INVALID_EXPERIMENT = "INVALID_EXPERIMENT"


# ── Assignment Policy ─────────────────────────────────────────────────────────


class AssignmentPolicy(BaseModel):
    """Deterministic, reproducible assignment policy for subject allocation."""

    randomization_seed: str = Field(
        default_factory=lambda: uuid4().hex,
        description="Persisted seed ensuring deterministic reproducible assignment.",
    )
    control_allocation_ratio: float = Field(
        default=0.50,
        ge=0.01,
        le=0.99,
        description="Proportion of subjects allocated to control [0.01, 0.99].",
    )

    model_config = ConfigDict(frozen=True)

    def assign_subject(self, subject_id: str) -> VariantRole:
        """Deterministically allocate a subject to CONTROL or TREATMENT.

        Zero reliance on random.random(); fully reproducible via cryptographic hash.
        """
        raw = f"{self.randomization_seed}:{subject_id}"
        hash_val = int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8], 16)
        normalized_bucket = (hash_val % 10000) / 10000.0
        if normalized_bucket < self.control_allocation_ratio:
            return VariantRole.CONTROL
        return VariantRole.TREATMENT


# ── Variant Model ─────────────────────────────────────────────────────────────


class ExperimentVariant(BaseModel):
    """Immutable representation of a controlled creative or packaging variant."""

    variant_id: UUID = Field(default_factory=uuid4)
    experiment_id: UUID
    role: VariantRole
    change_dimension: ChangeDimension
    media_artifact_id: UUID = Field(
        ..., description="Underlying video artifact ID. Must match control for packaging tests."
    )
    packaging_plan_id: UUID | None = None
    title: str | None = None
    thumbnail_concept_id: UUID | None = None
    thumbnail_ref: str | None = None
    is_accepted_p24: bool = Field(
        default=False,
        description="Whether this variant satisfies all applicable P24 acceptance gates.",
    )
    creative_qa_status: CreativeQAStatus = Field(
        default=CreativeQAStatus.FAIL,
        description="P24-D CreativeQA status. Must be PASS to participate in experiments.",
    )
    provenance: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)

    def is_eligible_for_experiment(self) -> bool:
        """Every variant must independently satisfy P24 acceptance and CreativeQA PASS."""
        return self.is_accepted_p24 and self.creative_qa_status == CreativeQAStatus.PASS


# ── Experiment Definition ─────────────────────────────────────────────────────


class ExperimentDefinition(BaseModel):
    """Authoritative definition of a controlled experiment."""

    experiment_id: UUID = Field(default_factory=uuid4)
    channel_id: UUID
    experiment_type: ExperimentType
    hypothesis: str = Field(..., min_length=5, description="Falsifiable causal hypothesis statement.")
    primary_metric: str = Field(
        ...,
        description="Single locked primary metric. Reuses P25-B canonical metric names (e.g. impressions_ctr).",
    )
    secondary_metrics: list[str] = Field(default_factory=list)
    control_variant_id: UUID
    treatment_variant_ids: list[UUID] = Field(..., min_length=1)
    assignment_policy: AssignmentPolicy = Field(default_factory=AssignmentPolicy)
    experiment_unit: ExperimentUnit = ExperimentUnit.IMPRESSION
    minimum_sample_size: int = Field(default=1000, ge=10)
    status: ExperimentStatus = ExperimentStatus.DRAFT
    start_at: datetime | None = None
    end_at: datetime | None = None
    analysis_window_hours: int = Field(default=24, ge=1)
    revision_number: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)

    @field_validator("primary_metric")
    @classmethod
    def validate_primary_metric(cls, v: str) -> str:
        allowed_metrics = {
            "views",
            "impressions",
            "impressions_ctr",
            "watch_time_seconds",
            "average_view_duration_seconds",
            "average_percentage_viewed",
            "likes",
            "comments",
            "shares",
            "subscribers_gained",
        }
        if v not in allowed_metrics:
            raise ValueError(f"Primary metric '{v}' must be one of {sorted(allowed_metrics)}")
        return v

    def can_transition_to(self, target_status: ExperimentStatus) -> bool:
        """Validates allowable lifecycle state transitions."""
        valid_transitions: dict[ExperimentStatus, set[ExperimentStatus]] = {
            ExperimentStatus.DRAFT: {ExperimentStatus.READY, ExperimentStatus.CANCELLED},
            ExperimentStatus.READY: {ExperimentStatus.RUNNING, ExperimentStatus.CANCELLED, ExperimentStatus.INVALIDATED},
            ExperimentStatus.RUNNING: {ExperimentStatus.PAUSED, ExperimentStatus.COMPLETED, ExperimentStatus.INVALIDATED, ExperimentStatus.CANCELLED},
            ExperimentStatus.PAUSED: {ExperimentStatus.RUNNING, ExperimentStatus.CANCELLED, ExperimentStatus.INVALIDATED},
            ExperimentStatus.COMPLETED: set(),  # Terminal immutable state
            ExperimentStatus.CANCELLED: set(),  # Terminal immutable state
            ExperimentStatus.INVALIDATED: set(),  # Terminal immutable state
        }
        return target_status in valid_transitions.get(self.status, set())


# ── Exposure Model ────────────────────────────────────────────────────────────


class ExperimentExposure(BaseModel):
    """Immutable record of an actual exposure of a subject/delivery to a variant."""

    exposure_id: UUID = Field(default_factory=uuid4)
    experiment_id: UUID
    variant_id: UUID
    subject_id: str = Field(..., description="Subject identity or aggregate window bucket identifier.")
    exposed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    aggregate_sample_count: int = Field(default=1, ge=1)
    provenance: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


# ── Statistical Inference Results ─────────────────────────────────────────────


class StatisticalInferenceResult(BaseModel):
    """Formal inferential statistics results when mathematically supported."""

    is_statistically_significant: bool
    p_value: float | None = Field(default=None, ge=0.0, le=1.0)
    test_statistic: float | None = None
    confidence_interval: tuple[float, float] | None = None
    confidence_level: float = 0.95
    method: str = "TWO_PROPORTION_Z_TEST"
    sample_basis: dict[str, int] = Field(default_factory=dict)
    notes: str | None = None

    model_config = ConfigDict(frozen=True)


# ── Attribution Result ────────────────────────────────────────────────────────


class AttributionResult(BaseModel):
    """Immutable causal attribution outcome for an experiment comparison."""

    result_id: UUID = Field(default_factory=uuid4)
    experiment_id: UUID
    control_variant_id: UUID
    treatment_variant_id: UUID
    primary_metric: str
    analysis_window: str
    control_value: float | None = None
    treatment_value: float | None = None
    absolute_difference: float | None = None
    relative_lift: float | None = None
    sample_basis: dict[str, int] = Field(default_factory=dict)
    statistical_inference: StatisticalInferenceResult | None = None
    data_maturity: DataMaturityState
    classification: AttributionClassification
    findings: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)

    @classmethod
    def build_inconclusive(
        cls,
        experiment_id: UUID,
        control_variant_id: UUID,
        treatment_variant_id: UUID,
        metric: str,
        window: str,
        reason: str,
        maturity: DataMaturityState = DataMaturityState.MATURE,
    ) -> AttributionResult:
        """Deterministic helper for inconclusive or insufficient data findings."""
        return cls(
            experiment_id=experiment_id,
            control_variant_id=control_variant_id,
            treatment_variant_id=treatment_variant_id,
            primary_metric=metric,
            analysis_window=window,
            data_maturity=maturity,
            classification=AttributionClassification.INCONCLUSIVE,
            findings=[reason],
        )

    @classmethod
    def build_invalid(
        cls,
        experiment_id: UUID,
        control_variant_id: UUID,
        treatment_variant_id: UUID,
        metric: str,
        window: str,
        reasons: list[str],
    ) -> AttributionResult:
        """Deterministic helper for invalidated experiments (confounding, artifact mismatch)."""
        return cls(
            experiment_id=experiment_id,
            control_variant_id=control_variant_id,
            treatment_variant_id=treatment_variant_id,
            primary_metric=metric,
            analysis_window=window,
            data_maturity=DataMaturityState.INVALID,
            classification=AttributionClassification.INVALID_EXPERIMENT,
            findings=reasons,
        )
