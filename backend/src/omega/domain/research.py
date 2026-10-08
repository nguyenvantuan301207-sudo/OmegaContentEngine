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

from omega.domain.causal_direction import CausalAssertion


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
    ResearchSourceType.WEB_SEARCH,
}


class ResearchAcquisitionMode(enum.StrEnum):
    """Operating mode for research acquisition."""

    MANUAL = "MANUAL"
    AUTOMATIC_SEARCH = "AUTOMATIC_SEARCH"


class ResearchCoverageStopReason(enum.StrEnum):
    """Terminal status reasons for coverage-driven research execution."""

    COVERAGE_FULFILLED = "COVERAGE_FULFILLED"
    NUMERIC_COVERAGE_NOT_FULFILLED = "NUMERIC_COVERAGE_NOT_FULFILLED"
    SEARCH_BUDGET_EXHAUSTED = "SEARCH_BUDGET_EXHAUSTED"
    NO_NEW_RELEVANT_SOURCES = "NO_NEW_RELEVANT_SOURCES"
    DISCOVERY_PROVIDER_UNAVAILABLE = "DISCOVERY_PROVIDER_UNAVAILABLE"
    MANUAL_ONLY_AWAITING_INPUT = "MANUAL_ONLY_AWAITING_INPUT"


class ResearchQueryIntent(enum.StrEnum):
    """Intent classification for research search queries."""

    OVERVIEW = "OVERVIEW"
    TYPES = "TYPES"
    CAUSES = "CAUSES"
    MECHANISMS = "MECHANISMS"
    TECHNICAL_REFERENCE = "TECHNICAL_REFERENCE"
    ADDITIONAL_COVERAGE = "ADDITIONAL_COVERAGE"
    CORROBORATION = "CORROBORATION"


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


# ── Schemas for Discovery & Coverage Expansion ──


class DiscoveryCandidate(BaseModel):
    """Candidate source retrieved by a discovery provider.

    IMPORTANT: Discovery != evidence. Snippets are not verified claims.
    Candidates represent prospective references only, not canonical evidence authority.
    """

    canonical_url: str = Field(..., min_length=5, max_length=1000)
    title: str = Field(..., min_length=2, max_length=300)
    publisher: str = Field(..., min_length=1, max_length=200)
    snippet: str | None = Field(default=None, max_length=1000)
    author: str | None = Field(default=None, max_length=200)
    published_at: datetime | None = None
    primary_source_status: PrimarySourceStatus = PrimarySourceStatus.UNKNOWN
    language: str = Field(default="en", max_length=20)
    region: str = Field(default="US", max_length=10)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedResearchDocument(BaseModel):
    """Normalized content extracted from a discovered research document.

    Serves as canonical evidence authority for ResearchSource creation.
    """

    canonical_url: str = Field(..., min_length=5, max_length=1000)
    title: str | None = Field(default=None, max_length=300)
    publisher: str | None = Field(default=None, max_length=200)
    extracted_content: str = Field(..., min_length=5, max_length=10000)
    content_provenance: dict[str, Any] = Field(default_factory=dict)
    language: str = Field(default="en", min_length=2, max_length=20)
    region: str = Field(default="US", min_length=2, max_length=10)
    published_at: datetime | None = None
    author: str | None = Field(default=None, max_length=200)
    primary_source_status: PrimarySourceStatus = PrimarySourceStatus.UNKNOWN


class ResearchQuery(BaseModel):
    """Deterministic query specification generated by query planner."""

    query_text: str = Field(..., min_length=2, max_length=500)
    intent: ResearchQueryIntent
    reason: str = Field(default="", max_length=500)
    round_number: int = 1
    target_claim_id: UUID | None = None
    target_source_ids: list[UUID] = Field(default_factory=list)
    causal_assertion: CausalAssertion | None = None
    candidate_family: str = ""
    prior_independent_support: int = 0


class CorroborationTarget(BaseModel):
    """Deterministic in-memory planning representation for candidate proposition corroboration."""

    representative_claim_text: str = Field(..., min_length=5, max_length=1000)
    claim_type: ClaimType = ClaimType.FACT
    independent_support_count: int = Field(default=1, ge=0)
    supporting_domains: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=100.0)
    topic_relevance: float = Field(default=0.0, ge=0.0)
    priority: int = Field(default=1, ge=1)
    semantic_role: str = Field(default="GENERIC_CONTEXT")
    candidate_family: str = Field(default="")
    claim_id: UUID | None = None
    source_ids: list[UUID] = Field(default_factory=list)
    causal_assertion: CausalAssertion | None = None
    source_grounded: bool = False


class ResearchCoveragePlan(BaseModel):
    """Deterministic planning representation for coverage-driven research."""

    topic_title: str
    numeric_contract: dict[str, Any] | None = None
    promised_entity_type: str | None = None
    promised_count: int | None = None
    currently_supported_entities: list[str] = Field(default_factory=list)
    remaining_coverage_count: int = 0
    query_budget: int = 3
    source_budget: int = 5
    round_budget: int = 3
    current_round: int = 0


class ResearchCoverageRoundTruth(BaseModel):
    """Observability snapshot for a single coverage expansion round."""

    round_number: int
    queries: list[dict[str, Any]] = Field(default_factory=list)
    candidates_discovered: int = 0
    sources_accepted: int = 0
    sources_rejected: int = 0
    rejection_reasons: list[str] = Field(default_factory=list)
    supported_count_before: int = 0
    supported_count_after: int = 0
    supported_families_after: list[str] = Field(default_factory=list)
    remaining_count: int = 0
    stop_reason: str | None = None


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
    acquisition_mode: ResearchAcquisitionMode = ResearchAcquisitionMode.MANUAL
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
