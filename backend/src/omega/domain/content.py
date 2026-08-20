"""Content Engine domain models, enums, schemas, and QA rules.

Defines ContentGenerationRequest, ContentIntent, ContentHook, ContentOutline,
ScriptVersion, ScriptSection, ScriptStatement, ContentCitation, and ContentQAResult.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContentGenerationMode(enum.StrEnum):
    """Execution context mode for content generation."""

    INTERACTIVE = "INTERACTIVE"
    MISSION_EXECUTION = "MISSION_EXECUTION"


class ContentRequestStatus(enum.StrEnum):
    """Lifecycle status of a ContentGenerationRequest."""

    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ContentOutcome(enum.StrEnum):
    """Usability outcome of content generation."""

    GENERATED = "GENERATED"
    BLOCKED = "BLOCKED"


class ContentType(enum.StrEnum):
    """Target content format."""

    YOUTUBE_LONGFORM = "YOUTUBE_LONGFORM"
    YOUTUBE_SHORT = "YOUTUBE_SHORT"


class HookType(enum.StrEnum):
    """Hook narrative archetype."""

    QUESTION = "QUESTION"
    CONTRARIAN = "CONTRARIAN"
    CURIOSITY = "CURIOSITY"
    RESULT_FIRST = "RESULT_FIRST"
    STORY = "STORY"
    STATISTIC = "STATISTIC"
    PROBLEM = "PROBLEM"


class ContentStatementType(enum.StrEnum):
    """Classification of individual narration statements in a script."""

    FACTUAL = "FACTUAL"
    ATTRIBUTED = "ATTRIBUTED"
    INTERPRETIVE = "INTERPRETIVE"
    CREATIVE = "CREATIVE"
    TRANSITION = "TRANSITION"
    CTA = "CTA"


class ClaimUsagePolicy(enum.StrEnum):
    """Verification policy for utilizing research claims in script content."""

    VERIFIED_FACT = "VERIFIED_FACT"
    QUALIFIED_ALLOWED = "QUALIFIED_ALLOWED"
    BLOCKED_UNVERIFIED = "BLOCKED_UNVERIFIED"
    BLOCKED_CONFLICT = "BLOCKED_CONFLICT"
    BLOCKED_NO_EVIDENCE = "BLOCKED_NO_EVIDENCE"


class RetentionBeatType(enum.StrEnum):
    """Retention beat structural archetype."""

    HOOK = "HOOK"
    OPEN_LOOP = "OPEN_LOOP"
    PAYOFF = "PAYOFF"
    PATTERN_INTERRUPT = "PATTERN_INTERRUPT"
    QUESTION = "QUESTION"
    REVEAL = "REVEAL"
    RECAP = "RECAP"
    CTA = "CTA"


class ScriptQAStatus(enum.StrEnum):
    """QA verification status for a script version."""

    PENDING = "PENDING"
    PASSED = "PASSED"
    PASSED_WITH_WARNINGS = "PASSED_WITH_WARNINGS"
    BLOCKED = "BLOCKED"


class QASeverity(enum.StrEnum):
    """Severity of a Content QA finding."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKING = "BLOCKING"


class QARuleCode(enum.StrEnum):
    """Authoritative rule codes for local Content QA."""

    FACTUAL_PROVENANCE_MISSING = "FACTUAL_PROVENANCE_MISSING"
    BLOCKED_CLAIM_USED = "BLOCKED_CLAIM_USED"
    UNSUPPORTED_STATISTIC = "UNSUPPORTED_STATISTIC"
    UNSUPPORTED_QUOTE = "UNSUPPORTED_QUOTE"
    OPEN_HIGH_RESEARCH_CONFLICT = "OPEN_HIGH_RESEARCH_CONFLICT"
    DURATION_OUT_OF_BOUNDS = "DURATION_OUT_OF_BOUNDS"
    FORBIDDEN_TOPIC_OR_TERM = "FORBIDDEN_TOPIC_OR_TERM"
    AVOIDED_VOCABULARY = "AVOIDED_VOCABULARY"
    EMPTY_REQUIRED_HOOK_OR_STRUCTURE = "EMPTY_REQUIRED_HOOK_OR_STRUCTURE"
    DUPLICATE_OR_REPEATED_SECTION = "DUPLICATE_OR_REPEATED_SECTION"


# ── Schemas ──


class RetentionBeatSchema(BaseModel):
    """Structure for an in-section retention beat."""

    timestamp_estimate_seconds: int = Field(ge=0, description="Estimated second mark in timeline")
    beat_type: RetentionBeatType = Field(description="Retention beat archetype")
    purpose: str = Field(min_length=1, max_length=255, description="Editorial purpose")
    text_hint: str | None = Field(
        default=None, max_length=255, description="Script reference or cue"
    )


class ContentCitationResponse(BaseModel):
    """Provenance citation linking a script statement to research artifacts."""

    id: UUID
    script_statement_id: UUID
    research_brief_id: UUID
    claim_id: UUID
    evidence_id: UUID
    source_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScriptStatementResponse(BaseModel):
    """Individual classified statement in a script section."""

    id: UUID
    script_section_id: UUID
    statement_order: int
    statement_text: str
    statement_type: ContentStatementType
    qualification_note: str | None = None
    citations: list[ContentCitationResponse] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScriptSectionResponse(BaseModel):
    """Structured narrative section of a script."""

    id: UUID
    script_version_id: UUID
    section_order: int
    heading: str
    narration_text: str
    estimated_duration_seconds: int
    transition_text: str | None = None
    retention_beat: dict[str, Any] | None = None
    statements: list[ScriptStatementResponse] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScriptVersionSummaryResponse(BaseModel):
    """Summary of a script revision."""

    id: UUID
    content_request_id: UUID
    version: int
    is_current: bool
    supersedes_script_id: UUID | None = None
    title: str
    estimated_word_count: int
    estimated_duration_seconds: int
    qa_status: ScriptQAStatus
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScriptVersionResponse(BaseModel):
    """Full script revision with sections, statements, and citations."""

    id: UUID
    content_request_id: UUID
    version: int
    is_current: bool
    supersedes_script_id: UUID | None = None
    title: str
    hook_id: UUID | None = None
    hook_text: str
    closing_text: str
    cta_text: str
    estimated_word_count: int
    estimated_duration_seconds: int
    qa_status: ScriptQAStatus
    style_snapshot: dict[str, Any] = Field(default_factory=dict)
    sections: list[ScriptSectionResponse] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HookCitationResponse(BaseModel):
    """Provenance citation linking a factual hook to research artifacts."""

    id: UUID
    hook_id: UUID
    research_brief_id: UUID
    claim_id: UUID
    evidence_id: UUID
    source_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContentHookResponse(BaseModel):
    """Hook variant candidate."""

    id: UUID
    content_request_id: UUID
    hook_variant_index: int
    text: str
    hook_type: HookType
    score: float
    reason_codes: list[str] = Field(default_factory=list)
    selected: bool
    citations: list[HookCitationResponse] = Field(default_factory=list)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HookSelectPayload(BaseModel):
    """Payload to select a single hook variant."""

    selected: bool = True


class ContentIntentResponse(BaseModel):
    """Editorial intent driving content generation."""

    id: UUID
    content_request_id: UUID
    primary_goal: str
    audience_intent: str
    viewer_promise: str
    central_question: str
    core_takeaway: str
    tone: str
    pace: str
    complexity: str
    desired_emotion: str
    call_to_action_type: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OutlineSectionSchema(BaseModel):
    """Individual section in a content outline."""

    section_id: str
    title: str
    objective: str
    key_points: list[str] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)
    estimated_duration_seconds: int
    transition: str
    retention_goal: str


class ContentOutlineResponse(BaseModel):
    """Structured outline for a content generation request."""

    id: UUID
    content_request_id: UUID
    opening_description: str
    sections: list[OutlineSectionSchema] = Field(default_factory=list)
    closing_description: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class QAFindingSchema(BaseModel):
    """Individual finding from local Content QA."""

    rule_code: QARuleCode
    severity: QASeverity
    message: str
    section_index: int | None = None
    statement_order: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ContentQAResultResponse(BaseModel):
    """Local QA evaluation result."""

    id: UUID
    script_version_id: UUID
    status: ScriptQAStatus
    findings: list[QAFindingSchema] = Field(default_factory=list)
    executed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContentGenerationRequestCreate(BaseModel):
    """Payload to initiate a content generation request."""

    topic_candidate_id: UUID
    research_brief_id: UUID
    mission_execution_id: UUID | None = None
    content_type: ContentType = ContentType.YOUTUBE_LONGFORM
    target_duration_seconds: int = Field(default=480, ge=30, le=3600)
    target_word_count: int | None = Field(default=None, ge=50, le=10000)
    language: str = Field(default="en", max_length=20)
    region: str = Field(default="US", max_length=10)
    creative_direction: str | None = Field(default=None, max_length=2000)

    @field_validator("creative_direction")
    @classmethod
    def clean_creative_direction(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            return None
        return v.strip() if v else None


class ContentRunPayload(BaseModel):
    """Payload to execute content generation pipeline."""

    idempotency_key: str | None = Field(default=None, max_length=100)


class ContentGenerationRequestResponse(BaseModel):
    """Content generation request representation."""

    id: UUID
    channel_id: UUID
    topic_candidate_id: UUID
    research_brief_id: UUID
    channel_dna_revision_id: UUID
    mission_execution_id: UUID | None = None
    mode: ContentGenerationMode
    status: ContentRequestStatus
    outcome: ContentOutcome | None = None
    content_type: ContentType
    target_duration_seconds: int
    target_word_count: int | None = None
    language: str
    region: str
    creative_direction: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
