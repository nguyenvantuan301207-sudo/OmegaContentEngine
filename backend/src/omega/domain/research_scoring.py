"""Research scoring profile, outcome rules, and operational reason codes.

Defines typed ClaimConfidenceProfile, ResearchOutcomeProfile, and explainable
constants for the Research Engine.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClaimConfidenceProfile(BaseModel):
    """Authoritative configuration for claim confidence scoring and verification."""

    independent_support_weight: float = Field(default=0.40, ge=0.0, le=1.0)
    source_quality_weight: float = Field(default=0.30, ge=0.0, le=1.0)
    evidence_strength_weight: float = Field(default=0.30, ge=0.0, le=1.0)

    # Primary source bonuses (only awarded based on PrimarySourceStatus)
    confirmed_primary_bonus: float = Field(default=15.0, ge=0.0, le=30.0)
    claimed_primary_bonus: float = Field(default=5.0, ge=0.0, le=10.0)

    # Contradiction penalties
    contradiction_penalty_high: float = Field(default=40.0, ge=0.0, le=80.0)
    contradiction_penalty_medium: float = Field(default=20.0, ge=0.0, le=40.0)

    # Thresholds for bands and verification
    minimum_verified_confidence: float = Field(default=70.0, ge=50.0, le=90.0)
    min_sources_for_high_band: int = Field(default=2, ge=2)
    min_sources_for_very_high_band: int = Field(default=3, ge=2)

    @model_validator(mode="after")
    def validate_weights(self) -> ClaimConfidenceProfile:
        total = (
            self.independent_support_weight
            + self.source_quality_weight
            + self.evidence_strength_weight
        )
        if abs(total - 1.0) > 1e-5:
            raise ValueError(f"Confidence weights must sum to 1.0 (got {total:.4f})")
        return self

    model_config = ConfigDict(extra="forbid")


# Default singleton instance
DEFAULT_CLAIM_CONFIDENCE_PROFILE = ClaimConfidenceProfile()


class ResearchOutcomeProfile(BaseModel):
    """Deterministic rules for SUFFICIENT / PARTIAL / INSUFFICIENT research outcomes."""

    min_sources_sufficient: int = Field(default=2, ge=1)
    min_independent_sources_sufficient: int = Field(default=2, ge=1)
    min_verified_claims_sufficient: int = Field(default=2, ge=1)
    max_open_high_conflicts_sufficient: int = Field(default=0, ge=0)

    min_sources_partial: int = Field(default=1, ge=1)
    min_claims_partial: int = Field(default=1, ge=1)

    model_config = ConfigDict(extra="forbid")


# Default singleton instance
DEFAULT_OUTCOME_PROFILE = ResearchOutcomeProfile()


# ── Operational Reason Codes ──

REASON_CONFIRMED_PRIMARY_SOURCE = "CONFIRMED_PRIMARY_SOURCE"
REASON_CLAIMED_PRIMARY_SOURCE = "CLAIMED_PRIMARY_SOURCE"
REASON_HIGH_AUTHORITY_PUBLISHER = "HIGH_AUTHORITY_PUBLISHER"
REASON_MULTI_SOURCE_INDEPENDENT_CONSENSUS = "MULTI_SOURCE_INDEPENDENT_CONSENSUS"
REASON_DIRECT_EXCERPT_EVIDENCE = "DIRECT_EXCERPT_EVIDENCE"
REASON_ZERO_SUPPORTING_EVIDENCE = "ZERO_SUPPORTING_EVIDENCE"
REASON_OPEN_HIGH_CONTRADICTION = "OPEN_HIGH_CONTRADICTION"
REASON_OPEN_MEDIUM_CONTRADICTION = "OPEN_MEDIUM_CONTRADICTION"
REASON_SYNDICATED_SOURCE_GROUPED = "SYNDICATED_SOURCE_GROUPED"
REASON_UNVERIFIED_STATISTIC = "UNVERIFIED_STATISTIC"
REASON_QUALIFIED_INTERPRETATION = "QUALIFIED_INTERPRETATION"
