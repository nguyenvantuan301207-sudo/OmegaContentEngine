"""Research domain models, enums, validators, and Pydantic schemas.

Defines ResearchRequest, ResearchSource, ResearchClaim, ClaimEvidence,
ResearchConflict, and versioned ResearchBrief schemas.
Zero infrastructure dependencies in domain layer.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResearchRequestStatus(enum.StrEnum):
    """Lifecycle states for a ResearchRequest."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ResearchOutcome(enum.StrEnum):
    """Quality and completeness outcome of a research run."""

    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class ResearchSourceType(enum.StrEnum):
    """Supported source types for research collection."""

    MANUAL = "MANUAL"
    IMPORT = "IMPORT"
    SYSTEM_SEED = "SYSTEM_SEED"

    # Reserved for future phases
    WEB_SEARCH = "WEB_SEARCH"
    RSS = "RSS"
    NEWS = "NEWS"


SUPPORTED_RESEARCH_SOURCES: set[ResearchSourceType] = {
    ResearchSourceType.MANUAL,
    ResearchSourceType.IMPORT,
    ResearchSourceType.SYSTEM_SEED,
}


class PrimarySourceStatus(enum.StrEnum):
    """Verification status of primary source declaration."""

    UNKNOWN = "UNKNOWN"
    CLAIMED = "CLAIMED"
    CONFIRMED = "CONFIRMED"


class ClaimType(enum.StrEnum):
    """Classification of research claim."""

    FACT = "FACT"
    STATISTIC = "STATISTIC"
    DATE = "DATE"
    QUOTE = "QUOTE"
    CAUSAL = "CAUSAL"
    DEFINITION = "DEFINITION"
    INTERPRETATION = "INTERPRETATION"


class EvidenceDirection(enum.StrEnum):
    """Support relationship between an evidence excerpt and a claim."""

    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    CONTEXT_ONLY = "CONTEXT_ONLY"


class ConfidenceBand(enum.StrEnum):
    """Discrete confidence rating bands."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class ConflictSeverity(enum.StrEnum):
    """Severity of a detected contradiction."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ConflictStatus(enum.StrEnum):
    """Resolution status of a research contradiction."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


# ── Schemas for Evidence ──


class ClaimEvidenceBase(BaseModel):
    """Base schema for claim evidence."""

    source_id: UUID
    support_direction: EvidenceDirection = EvidenceDirection.SUPPORTS
    excerpt: str = Field(..., min_length=2, max_length=1000)
    source_location: str | None = Field(default=None, max_length=200)
    strength_score: float = Field(default=80.0, ge=0.0, le=100.0)


class ClaimEvidenceCreate(ClaimEvidenceBase):
    """Schema for creating evidence for a claim."""

    pass


class ClaimEvidenceResponse(ClaimEvidenceBase):
    """Schema for returning claim evidence."""

    id: UUID
    claim_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Schemas for Claims ──


class ResearchClaimBase(BaseModel):
    """Base schema for a research claim."""

    claim_text: str = Field(..., min_length=3, max_length=1000)
    claim_type: ClaimType = ClaimType.FACT
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchClaimCreate(ResearchClaimBase):
    """Schema for creating a research claim with initial evidence."""

    evidence: list[ClaimEvidenceCreate] = Field(default_factory=list)


class ResearchClaimResponse(ResearchClaimBase):
    """Schema for returning a research claim with confidence metrics."""

    id: UUID
    research_request_id: UUID
    channel_id: UUID
    normalized_claim: str
    confidence_score: float
    confidence_band: ConfidenceBand
    supporting_sources_count: int
    contradicting_sources_count: int
    independent_sources_count: int
    is_verified: bool
    reasons: list[str] = Field(default_factory=list)
    evidence: list[ClaimEvidenceResponse] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ── Schemas for Sources ──


class ResearchSourceCreate(BaseModel):
    """Schema for ingesting a research source."""

    source_type: ResearchSourceType = ResearchSourceType.MANUAL
    title: str = Field(..., min_length=2, max_length=300)
    publisher: str = Field(..., min_length=1, max_length=200)
    author: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=1000)
    content_excerpt: str = Field(..., min_length=5, max_length=5000)
    primary_source_status: PrimarySourceStatus = PrimarySourceStatus.UNKNOWN
    published_at: datetime | None = None
    language: str = Field(default="en", min_length=2, max_length=20)
    region: str = Field(default="US", min_length=2, max_length=10)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_type")
    @classmethod
    def check_source_type(cls, v: ResearchSourceType) -> ResearchSourceType:
        if v not in SUPPORTED_RESEARCH_SOURCES:
            raise ValueError(
                f"Source '{v.value}' is reserved and not supported in OMEGA-005. Supported sources: {', '.join(s.value for s in SUPPORTED_RESEARCH_SOURCES)}"
            )
        return v


class ResearchSourceBatchCreate(BaseModel):
    """Schema for batch ingesting multiple sources."""

    sources: list[ResearchSourceCreate] = Field(..., min_length=1, max_length=50)


class ResearchSourceResponse(BaseModel):
    """Response schema for a normalized research source."""

    id: UUID
    research_request_id: UUID
    channel_id: UUID
    source_type: ResearchSourceType
    title: str
    publisher: str
    author: str | None = None
    url: str | None = None
    content_excerpt: str
    content_hash: str
    primary_source_status: PrimarySourceStatus
    quality_score: float
    relevance_score: float
    freshness_score: float
    quality_reasons: list[str] = Field(default_factory=list)
    independence_cluster_id: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime
    language: str
    region: str
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ── Schemas for Research Conflicts ──


class ResearchConflictResponse(BaseModel):
    """Response schema for a research contradiction/conflict entity."""

    id: UUID
    research_request_id: UUID
    claim_id: UUID | None = None
    conflict_type: str
    severity: ConflictSeverity
    status: ConflictStatus
    description: str
    involved_evidence_ids: list[str] = Field(default_factory=list)
    involved_source_ids: list[str] = Field(default_factory=list)
    resolution_note: str | None = None
    detected_at: datetime
    resolved_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# ── Schemas for Research Requests ──


class ResearchRequestCreate(BaseModel):
    """Schema for initiating a new research request."""

    topic_candidate_id: UUID
    mission_execution_id: UUID | None = None
    research_question: str | None = Field(default=None, max_length=500)
    scope: str | None = Field(default=None, max_length=2000)
    language: str = Field(default="en", min_length=2, max_length=20)
    region: str = Field(default="US", min_length=2, max_length=10)
    max_sources: int = Field(default=10, ge=1, le=50)
    minimum_source_quality: float = Field(default=50.0, ge=0.0, le=100.0)
    minimum_claim_confidence: float = Field(default=60.0, ge=0.0, le=100.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchRunPayload(BaseModel):
    """Payload for executing research pipeline."""

    idempotency_key: str | None = Field(default=None, max_length=100)


class ResearchRequestResponse(BaseModel):
    """Response schema for a research request."""

    id: UUID
    channel_id: UUID
    topic_candidate_id: UUID
    mission_execution_id: UUID | None = None
    mode: str
    status: ResearchRequestStatus
    outcome: ResearchOutcome | None = None
    research_question: str | None = None
    scope: str | None = None
    language: str
    region: str
    max_sources: int
    minimum_source_quality: float
    minimum_claim_confidence: float
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_")
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ── Schemas for Research Brief ──


class CitationRef(BaseModel):
    """Stable citation reference linking a verified claim to source and evidence."""

    source_id: UUID
    evidence_id: UUID
    publisher: str
    excerpt: str
    source_location: str | None = None


class VerifiedClaimBrief(BaseModel):
    """Verified claim structure inside a ResearchBrief."""

    claim_id: UUID
    text: str
    type: ClaimType
    confidence_score: float
    confidence_band: ConfidenceBand
    citations: list[CitationRef] = Field(default_factory=list)


class UncertainClaimBrief(BaseModel):
    """Uncertain or unverified claim inside a ResearchBrief."""

    claim_id: UUID
    text: str
    type: ClaimType
    confidence_score: float
    confidence_band: ConfidenceBand
    uncertainty_reason: str


class ConflictBrief(BaseModel):
    """Contradiction summary inside a ResearchBrief."""

    conflict_id: UUID
    claim_id: UUID | None = None
    description: str
    severity: ConflictSeverity
    involved_source_ids: list[str] = Field(default_factory=list)


class ResearchBriefResponse(BaseModel):
    """Versioned immutable ResearchBrief response."""

    id: UUID
    research_request_id: UUID
    topic_candidate_id: UUID
    channel_id: UUID
    version: int
    supersedes_brief_id: UUID | None = None
    is_current: bool
    outcome: ResearchOutcome
    overall_confidence: float
    title: str
    summary: str
    verified_claims: list[VerifiedClaimBrief] = Field(default_factory=list)
    uncertain_claims: list[UncertainClaimBrief] = Field(default_factory=list)
    contradictions: list[ConflictBrief] = Field(default_factory=list)
    key_facts: list[str] = Field(default_factory=list)
    statistics: list[dict[str, Any]] = Field(default_factory=list)
    dates: list[dict[str, Any]] = Field(default_factory=list)
    quotes: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    sources_summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_")
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ResearchBriefSummaryResponse(BaseModel):
    """Summary representation for listing brief revisions."""

    id: UUID
    research_request_id: UUID
    version: int
    supersedes_brief_id: UUID | None = None
    is_current: bool
    outcome: ResearchOutcome
    overall_confidence: float
    verified_claims_count: int
    contradictions_count: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
