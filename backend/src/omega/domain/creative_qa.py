"""Domain models, types, and schemas for P24-D Creative QA & Brand Acceptance.

Defines:
- CreativeQAStatus: PASS, REVISE, FAIL
- CreativeQASeverity: INFO, WARNING, ERROR, BLOCKER
- CreativeQASubsystem: 17 typed creative/brand subsystems
- CreativeQAFindingCode: Granular finding codes across channel identity, style, and packaging
- CreativeQAFinding: Structured diagnostic finding with provenance and source authority
- CreativeQARecommendation: Actionable non-destructive remediation recommendation
- CreativeQAProvenance: Provenance lineage tracking
- CreativeQAResult: The canonical cross-domain creative acceptance result
- CreativeAcceptancePackage: The bounded accepted physical creative package
- CreativeRenderGateError: Gate exception preventing unaccepted media from proceeding
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


CREATIVE_QA_ENGINE_VERSION = "p24d-v1"


# ── Typed Enums ─────────────────────────────────────────────────────────────


class CreativeQAStatus(enum.StrEnum):
    """Deterministic acceptance status for creative/brand QA."""
    PASS = "PASS"
    REVISE = "REVISE"
    FAIL = "FAIL"


class CreativeQASeverity(enum.StrEnum):
    """Severity levels governing the deterministic acceptance policy."""
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKER = "BLOCKER"

    @property
    def rank(self) -> int:
        return {"INFO": 0, "WARNING": 1, "ERROR": 2, "BLOCKER": 3}[self.value]

    def __ge__(self, other: CreativeQASeverity) -> bool:
        if not isinstance(other, CreativeQASeverity):
            return NotImplemented
        return self.rank >= other.rank

    def __gt__(self, other: CreativeQASeverity) -> bool:
        if not isinstance(other, CreativeQASeverity):
            return NotImplemented
        return self.rank > other.rank


class CreativeQASubsystem(enum.StrEnum):
    """Subsystems evaluated under Creative QA & Brand Acceptance."""
    CHANNEL_IDENTITY = "CHANNEL_IDENTITY"
    NARRATIVE_STYLE = "NARRATIVE_STYLE"
    VISUAL_STYLE = "VISUAL_STYLE"
    CAMERA = "CAMERA"
    GRAPHICS = "GRAPHICS"
    AUDIO_STYLE = "AUDIO_STYLE"
    MUSIC = "MUSIC"
    SFX = "SFX"
    TITLE = "TITLE"
    THUMBNAIL = "THUMBNAIL"
    DESCRIPTION = "DESCRIPTION"
    CHAPTERS = "CHAPTERS"
    METADATA = "METADATA"
    PACKAGING_COHERENCE = "PACKAGING_COHERENCE"
    CROSS_MODAL_COHERENCE = "CROSS_MODAL_COHERENCE"
    BRAND_CONSTRAINT = "BRAND_CONSTRAINT"
    PHYSICAL_ARTIFACT = "PHYSICAL_ARTIFACT"


class CreativeQAFindingCode(enum.StrEnum):
    """Granular defect and evaluation codes for creative and brand quality."""

    # ── Channel Identity & Hard Constraints ──
    HARD_CONSTRAINT_VIOLATION = "HARD_CONSTRAINT_VIOLATION"
    PROHIBITED_PHRASE_DETECTED = "PROHIBITED_PHRASE_DETECTED"
    MISLEADING_CLICKBAIT = "MISLEADING_CLICKBAIT"
    FABRICATED_CLAIM = "FABRICATED_CLAIM"
    UNSUPPORTED_MEDICAL_CLAIM = "UNSUPPORTED_MEDICAL_CLAIM"
    PROFANITY_DETECTED = "PROFANITY_DETECTED"
    CONTENT_PILLAR_MISMATCH = "CONTENT_PILLAR_MISMATCH"
    CHANNEL_REVISION_MISMATCH = "CHANNEL_REVISION_MISMATCH"
    AVOID_PATTERN_VIOLATION = "AVOID_PATTERN_VIOLATION"

    # ── Editorial Voice ──
    VOICE_FORMALITY_MISMATCH = "VOICE_FORMALITY_MISMATCH"
    VOICE_DEPTH_MISMATCH = "VOICE_DEPTH_MISMATCH"
    VOICE_ENERGY_MISMATCH = "VOICE_ENERGY_MISMATCH"
    VOICE_EXPRESSIVENESS_MISMATCH = "VOICE_EXPRESSIVENESS_MISMATCH"
    VOICE_TECHNICALITY_MISMATCH = "VOICE_TECHNICALITY_MISMATCH"
    VOICE_CHARACTER_MISMATCH = "VOICE_CHARACTER_MISMATCH"

    # ── Narrative Style ──
    HOOK_INTENSITY_MISMATCH = "HOOK_INTENSITY_MISMATCH"
    CONTEXT_DEPTH_MISMATCH = "CONTEXT_DEPTH_MISMATCH"
    EXPLANATION_DENSITY_MISMATCH = "EXPLANATION_DENSITY_MISMATCH"
    PAYOFF_EMPHASIS_MISMATCH = "PAYOFF_EMPHASIS_MISMATCH"
    CTA_INTENSITY_MISMATCH = "CTA_INTENSITY_MISMATCH"
    PACING_STYLE_MISMATCH = "PACING_STYLE_MISMATCH"

    # ── Visual Style & Graphics ──
    VISUAL_DENSITY_MISMATCH = "VISUAL_DENSITY_MISMATCH"
    EVIDENCE_EMPHASIS_MISMATCH = "EVIDENCE_EMPHASIS_MISMATCH"
    DOCUMENT_TREATMENT_MISMATCH = "DOCUMENT_TREATMENT_MISMATCH"
    GRAPHIC_STYLE_MISMATCH = "GRAPHIC_STYLE_MISMATCH"
    ANNOTATION_DENSITY_EXCESSIVE = "ANNOTATION_DENSITY_EXCESSIVE"

    # ── Camera / Motion ──
    CAMERA_TOO_ACTIVE = "CAMERA_TOO_ACTIVE"
    CAMERA_TOO_STATIC = "CAMERA_TOO_STATIC"
    UNJUSTIFIED_STYLE_SHIFT = "UNJUSTIFIED_STYLE_SHIFT"
    TRANSITION_OVERUSE = "TRANSITION_OVERUSE"

    # ── Audio Style ──
    MUSIC_ENERGY_TOO_HIGH = "MUSIC_ENERGY_TOO_HIGH"
    MUSIC_ENERGY_TOO_LOW = "MUSIC_ENERGY_TOO_LOW"
    VOCAL_POLICY_VIOLATION = "VOCAL_POLICY_VIOLATION"
    SFX_DENSITY_EXCESSIVE = "SFX_DENSITY_EXCESSIVE"
    SFX_PROMINENCE_MISMATCH = "SFX_PROMINENCE_MISMATCH"
    SILENCE_PREFERENCE_VIOLATION = "SILENCE_PREFERENCE_VIOLATION"

    # ── Title ──
    TITLE_BRAND_MISMATCH = "TITLE_BRAND_MISMATCH"
    TITLE_STYLE_MISMATCH = "TITLE_STYLE_MISMATCH"
    TITLE_UNGROUNDED = "TITLE_UNGROUNDED"
    TITLE_PREVIOUSLY_REJECTED = "TITLE_PREVIOUSLY_REJECTED"

    # ── Thumbnail ──
    THUMBNAIL_BRAND_MISMATCH = "THUMBNAIL_BRAND_MISMATCH"
    THUMBNAIL_TEXT_OVERLOADED = "THUMBNAIL_TEXT_OVERLOADED"
    THUMBNAIL_SAFE_AREA_COLLISION = "THUMBNAIL_SAFE_AREA_COLLISION"
    THUMBNAIL_UNSUPPORTED_CLAIM = "THUMBNAIL_UNSUPPORTED_CLAIM"
    THUMBNAIL_MOBILE_READABILITY = "THUMBNAIL_MOBILE_READABILITY"

    # ── Description & Metadata ──
    DESCRIPTION_VOICE_MISMATCH = "DESCRIPTION_VOICE_MISMATCH"
    DESCRIPTION_UNGROUNDED = "DESCRIPTION_UNGROUNDED"
    DESCRIPTION_CTA_MISMATCH = "DESCRIPTION_CTA_MISMATCH"
    KEYWORD_STUFFING = "KEYWORD_STUFFING"
    ATTRIBUTION_MISSING = "ATTRIBUTION_MISSING"
    CHAPTER_STYLE_MISMATCH = "CHAPTER_STYLE_MISMATCH"

    # ── Packaging Coherence & Promise/Payoff ──
    TITLE_THUMBNAIL_PROMISE_MISMATCH = "TITLE_THUMBNAIL_PROMISE_MISMATCH"
    TITLE_DESCRIPTION_MISMATCH = "TITLE_DESCRIPTION_MISMATCH"
    THUMBNAIL_DESCRIPTION_MISMATCH = "THUMBNAIL_DESCRIPTION_MISMATCH"
    PACKAGING_CONTENT_MISMATCH = "PACKAGING_CONTENT_MISMATCH"
    OVERPROMISE = "OVERPROMISE"
    PAYOFF_NOT_DELIVERED = "PAYOFF_NOT_DELIVERED"
    MISLEADING_OUTCOME_FRAMING = "MISLEADING_OUTCOME_FRAMING"

    # ── Cross-Modal Coherence & Drift ──
    CROSS_MODAL_ENERGY_CONFLICT = "CROSS_MODAL_ENERGY_CONFLICT"
    CALM_NARRATION_HYPERACTIVE_PRODUCTION = "CALM_NARRATION_HYPERACTIVE_PRODUCTION"
    ENERGETIC_HOOK_INERT_EXECUTION = "ENERGETIC_HOOK_INERT_EXECUTION"
    NARRATIVE_STYLE_DRIFT = "NARRATIVE_STYLE_DRIFT"
    VISUAL_STYLE_DRIFT = "VISUAL_STYLE_DRIFT"
    CAMERA_STYLE_DRIFT = "CAMERA_STYLE_DRIFT"
    GRAPHIC_STYLE_DRIFT = "GRAPHIC_STYLE_DRIFT"
    AUDIO_STYLE_DRIFT = "AUDIO_STYLE_DRIFT"
    PACKAGING_STYLE_DRIFT = "PACKAGING_STYLE_DRIFT"

    # ── Brand Consistency & Variation ──
    BRAND_IDENTITY_INCONSISTENCY = "BRAND_IDENTITY_INCONSISTENCY"
    FORMULAIC_REPETITION = "FORMULAIC_REPETITION"

    # ── Physical Artifacts ──
    MISSING_PHYSICAL_THUMBNAIL = "MISSING_PHYSICAL_THUMBNAIL"
    INVALID_PHYSICAL_THUMBNAIL = "INVALID_PHYSICAL_THUMBNAIL"
    VIDEO_ARTIFACT_LINEAGE_MISMATCH = "VIDEO_ARTIFACT_LINEAGE_MISMATCH"


class CreativeQARecommendationAction(enum.StrEnum):
    """Structured, actionable, non-destructive remediation directives."""
    REDUCE_CAMERA_INTENSITY = "REDUCE_CAMERA_INTENSITY"
    INCREASE_CAMERA_INTENSITY = "INCREASE_CAMERA_INTENSITY"
    REPLACE_TITLE = "REPLACE_TITLE"
    REDUCE_THUMBNAIL_TEXT = "REDUCE_THUMBNAIL_TEXT"
    REMOVE_UNSUPPORTED_CLAIM = "REMOVE_UNSUPPORTED_CLAIM"
    REDUCE_SFX_DENSITY = "REDUCE_SFX_DENSITY"
    RESTORE_CTA_POLICY = "RESTORE_CTA_POLICY"
    ADJUST_PACKAGING_TONE = "ADJUST_PACKAGING_TONE"
    ADJUST_MUSIC_ENERGY = "ADJUST_MUSIC_ENERGY"
    REVISE_VOICE_TONE = "REVISE_VOICE_TONE"
    REALIGN_PROMISE_PAYOFF = "REALIGN_PROMISE_PAYOFF"
    RESOLVE_STYLE_DRIFT = "RESOLVE_STYLE_DRIFT"
    ADJUST_VISUAL_DENSITY = "ADJUST_VISUAL_DENSITY"


# ── Domain Models ───────────────────────────────────────────────────────────


class CreativeQAFinding(BaseModel):
    """Structured diagnostic finding with source authority and lineage."""
    finding_code: CreativeQAFindingCode
    severity: CreativeQASeverity
    subsystem: CreativeQASubsystem
    affected_artifact: str = ""
    explanation: str
    source_authority: str
    recommended_remediation: str
    lineage: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


class CreativeQARecommendation(BaseModel):
    """Actionable remediation guidance produced alongside findings."""
    action: CreativeQARecommendationAction
    subsystem: CreativeQASubsystem
    description: str
    affected_field: str = ""

    model_config = ConfigDict(frozen=True)


class CreativeQAProvenance(BaseModel):
    """Provenance audit lineage tracking authority chain."""
    channel_dna_revision_id: UUID
    creative_style_plan_id: UUID
    packaging_plan_id: UUID | None = None
    narrative_plan_id: UUID | None = None
    video_artifact_id: UUID | None = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    engine_version: str = CREATIVE_QA_ENGINE_VERSION

    model_config = ConfigDict(frozen=True)


class CreativeQAResult(BaseModel):
    """Canonical cross-domain creative and brand acceptance result."""
    result_id: UUID = Field(default_factory=uuid.uuid4)
    status: CreativeQAStatus
    highest_severity: CreativeQASeverity
    findings: tuple[CreativeQAFinding, ...]
    recommendations: tuple[CreativeQARecommendation, ...]
    provenance: CreativeQAProvenance
    blocker_count: int
    error_count: int
    warning_count: int
    info_count: int
    is_accepted: bool
    version: str = CREATIVE_QA_ENGINE_VERSION

    model_config = ConfigDict(extra="ignore", frozen=True)


class CreativeAcceptancePackage(BaseModel):
    """Bounded, accepted physical creative package ready for downstream publishing."""
    title: str
    thumbnail_path: str
    description: str
    chapters: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    video_artifact_id: str | None = None
    video_path: str | None = None
    channel_dna_revision_id: UUID
    creative_style_plan_id: UUID
    packaging_plan_id: UUID
    qa_result: CreativeQAResult
    accepted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(extra="ignore", frozen=True)


# ── Gate Exception ──────────────────────────────────────────────────────────


class CreativeRenderGateError(RuntimeError):
    """Raised when final creative gate blocks (status is REVISE or FAIL)."""

    def __init__(self, message: str, qa_result: CreativeQAResult | None = None) -> None:
        super().__init__(message)
        self.qa_result = qa_result
