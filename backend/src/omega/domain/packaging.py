"""Domain models, types, and validation schemas for P24-C Packaging Engine.

Defines:
- TitleCandidate & TitleStrategy
- ThumbnailConcept & ThumbnailArtifact
- VideoChapter & CTAMetadata
- PackagingPlan: The complete, derived packaging bundle for an Omega production
- PackagingProvenance & PackagingValidationFinding
- PackagingFindingCode & PackagingCoherenceFinding
- Grounding & Clickbait boundary checkers
"""

from __future__ import annotations

import enum
import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omega.domain.channel_dna import (
    AvoidPatterns,
    HardConstraints,
    PackagingPreferences,
)
from omega.domain.creative_style import (
    CreativeStylePlan,
    PackagingStyleHints,
)
from omega.domain.narrative_plan import NarrativeFormatProfile, NarrativePlan, NarrativeSectionRole


PACKAGING_ENGINE_VERSION = "p24c-v1"


# ── Typed Enums ─────────────────────────────────────────────────────────────


class TitleStrategy(enum.StrEnum):
    FACTUAL = "FACTUAL"
    EXPLAINER = "EXPLAINER"
    CURIOSITY = "CURIOSITY"
    QUESTION = "QUESTION"
    OUTCOME = "OUTCOME"
    CONTRAST = "CONTRAST"


class TextOverlayIntent(enum.StrEnum):
    NO_TEXT = "NO_TEXT"
    SHORT_TEXT = "SHORT_TEXT"
    TOPIC_KEYWORD = "TOPIC_KEYWORD"
    METRIC_HIGHLIGHT = "METRIC_HIGHLIGHT"


class ThumbnailSafeZone(enum.StrEnum):
    FULL = "FULL"
    CENTER_FOCUSED = "CENTER_FOCUSED"
    LEFT_WEIGHTED = "LEFT_WEIGHTED"  # Leaves right side clear for platform badge/timestamp
    TOP_WEIGHTED = "TOP_WEIGHTED"


class PackagingFindingCode(enum.StrEnum):
    # Title findings
    TITLE_UNSUPPORTED_CLAIM = "TITLE_UNSUPPORTED_CLAIM"
    TITLE_EXAGGERATION = "TITLE_EXAGGERATION"
    TITLE_MISLEADING_CURIOSITY = "TITLE_MISLEADING_CURIOSITY"
    TITLE_NUMERIC_MISMATCH = "TITLE_NUMERIC_MISMATCH"
    TITLE_CLICKBAIT_RISK = "TITLE_CLICKBAIT_RISK"
    TITLE_TOO_LONG = "TITLE_TOO_LONG"
    TITLE_TOO_SHORT = "TITLE_TOO_SHORT"
    TITLE_REPETITIVE = "TITLE_REPETITIVE"

    # Thumbnail findings
    THUMBNAIL_FABRICATED_SUBJECT = "THUMBNAIL_FABRICATED_SUBJECT"
    THUMBNAIL_TEXT_OVERLOADED = "THUMBNAIL_TEXT_OVERLOADED"
    THUMBNAIL_BADGE_COLLISION = "THUMBNAIL_BADGE_COLLISION"
    THUMBNAIL_LOW_CONTRAST = "THUMBNAIL_LOW_CONTRAST"
    THUMBNAIL_PHYSICAL_ERROR = "THUMBNAIL_PHYSICAL_ERROR"

    # Description & Chapters findings
    DESCRIPTION_UNSUPPORTED_CLAIM = "DESCRIPTION_UNSUPPORTED_CLAIM"
    CHAPTER_NON_MONOTONIC = "CHAPTER_NON_MONOTONIC"
    CHAPTER_NEGATIVE_TIME = "CHAPTER_NEGATIVE_TIME"
    CHAPTER_OVERFLOW = "CHAPTER_OVERFLOW"
    CHAPTER_DUPLICATE_NAME = "CHAPTER_DUPLICATE_NAME"

    # Metadata & Attribution findings
    METADATA_KEYWORD_STUFFING = "METADATA_KEYWORD_STUFFING"
    ATTRIBUTION_MISSING = "ATTRIBUTION_MISSING"

    # Coherence findings
    TITLE_THUMBNAIL_PROMISE_MISMATCH = "TITLE_THUMBNAIL_PROMISE_MISMATCH"
    TITLE_DESCRIPTION_MISMATCH = "TITLE_DESCRIPTION_MISMATCH"
    THUMBNAIL_DESCRIPTION_MISMATCH = "THUMBNAIL_DESCRIPTION_MISMATCH"
    PACKAGING_PAYOFF_MISMATCH = "PACKAGING_PAYOFF_MISMATCH"
    HARD_CONSTRAINT_VIOLATION = "HARD_CONSTRAINT_VIOLATION"


# ── Title & Thumbnail Domain Models ──────────────────────────────────────────


class TitleCandidate(BaseModel):
    """A generated title candidate with granular quality, grounding, and style scores."""
    candidate_id: str = Field(default_factory=lambda: f"title-{uuid.uuid4().hex[:8]}")
    text: str = Field(min_length=5, max_length=150)
    strategy: TitleStrategy
    character_count: int = Field(ge=5, le=150)
    claim_references: tuple[str, ...] = ()
    clarity_score: float = Field(default=0.85, ge=0.0, le=1.0)
    specificity_score: float = Field(default=0.80, ge=0.0, le=1.0)
    curiosity_level: Literal["LOW", "MODERATE", "HIGH"] = "MODERATE"
    clickbait_risk: Literal["NONE", "LOW", "MODERATE", "HIGH"] = "NONE"
    is_grounded: bool = True
    constraint_findings: tuple[str, ...] = ()
    rationale: str = ""

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def set_char_count(cls, data: Any) -> Any:
        if isinstance(data, dict) and "text" in data and "character_count" not in data:
            data["character_count"] = len(data["text"])
        return data


class ThumbnailConcept(BaseModel):
    """Structured concept layout and composition guide for thumbnail rendering."""
    concept_id: str = Field(default_factory=lambda: f"thumb-{uuid.uuid4().hex[:8]}")
    primary_subject: str = Field(min_length=1, max_length=150)
    secondary_subject: str | None = None
    visual_hierarchy: str = "PRIMARY_SUBJECT_HERO"
    composition: str = "RULE_OF_THIRDS_LEFT_HERO"
    text_overlay_intent: TextOverlayIntent = TextOverlayIntent.NO_TEXT
    text_content: str | None = None
    text_placement: str = "TOP_LEFT"  # Avoiding bottom-right timestamp safe-zone
    visual_asset_references: tuple[str, ...] = ()
    background_treatment: str = "DARK_CINEMATIC_GRADIENT"
    contrast_intent: str = "HIGH"
    emotional_intensity: str = "MEASURED"
    safe_zone: ThumbnailSafeZone = ThumbnailSafeZone.LEFT_WEIGHTED
    claim_references: tuple[str, ...] = ()
    is_grounded: bool = True
    rationale: str = ""

    model_config = ConfigDict(frozen=True)


class ThumbnailArtifact(BaseModel):
    """Physical rendered thumbnail file metadata."""
    artifact_id: str = Field(default_factory=lambda: f"art-{uuid.uuid4().hex[:8]}")
    concept_id: str
    file_path: Path
    width: int = 1280
    height: int = 720
    format: str = "PNG"
    file_size_bytes: int = Field(gt=0)
    content_sha256: str = Field(min_length=64, max_length=64)
    rendered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(frozen=True)


# ── Chapters, CTA, and Metadata Models ───────────────────────────────────────


class VideoChapter(BaseModel):
    """Monotonic video chapter marking section transitions."""
    title: str = Field(min_length=1, max_length=80)
    start_time_seconds: int = Field(ge=0)
    end_time_seconds: int | None = Field(default=None, ge=0)
    narrative_section_order: int | None = None
    narrative_section_role: NarrativeSectionRole | None = None

    model_config = ConfigDict(frozen=True)

    @property
    def formatted_timestamp(self) -> str:
        """Format seconds into HH:MM:SS or MM:SS."""
        hrs = self.start_time_seconds // 3600
        mins = (self.start_time_seconds % 3600) // 60
        secs = self.start_time_seconds % 60
        if hrs > 0:
            return f"{hrs:02d}:{mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"


class CTAMetadata(BaseModel):
    """Structured call-to-action details."""
    cta_policy: str = "SOFT_CTA"  # NO_CTA, SOFT_CTA, STANDARD_CTA
    cta_text: str = ""
    target_action: str = "DISCUSSION"  # DISCUSSION, SUBSCRIBE, RELATED_VIDEO

    model_config = ConfigDict(frozen=True)


class PackagingProvenance(BaseModel):
    """Non-secret generation provenance and audit lineage."""
    channel_dna_revision_id: UUID
    creative_style_plan_id: UUID
    narrative_plan_id: UUID | None = None
    script_version_id: UUID | None = None
    generation_method: str = "DETERMINISTIC_HYBRID"
    model_provider: str | None = None
    model_name: str | None = None
    renderer_version: str = "ffmpeg-9.0.2"
    selected_title_id: str
    selected_thumbnail_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(frozen=True)


class PackagingValidationFinding(BaseModel):
    """Diagnostic validation finding for the packaging bundle."""
    code: PackagingFindingCode
    severity: Literal["INFO", "WARNING", "ERROR", "BLOCKER"]
    field: str
    explanation: str
    recommended_remediation: str

    model_config = ConfigDict(frozen=True)


class PackagingValidationResult(BaseModel):
    """Result of validating a PackagingPlan."""
    is_valid: bool
    findings: tuple[PackagingValidationFinding, ...]
    error_count: int
    warning_count: int

    model_config = ConfigDict(frozen=True)


# ── The Canonical PackagingPlan ──────────────────────────────────────────────


class PackagingPlan(BaseModel):
    """Canonical packaging artifact produced by P24-C for an Omega production.

    Answers: What are the titles, thumbnail, description, chapters, and metadata
    that best package this video according to ChannelDNA and CreativeStylePlan?
    Strictly derived, recomputable, never publishes.
    """
    packaging_plan_id: UUID = Field(default_factory=uuid.uuid4)
    channel_dna_revision_id: UUID
    creative_style_plan_id: UUID
    content_generation_request_id: UUID | None = None
    narrative_plan_id: UUID | None = None
    script_version_id: UUID | None = None
    format_profile: NarrativeFormatProfile = NarrativeFormatProfile.MEDIUM
    language: str = "en"

    # Title Candidates & Selection
    title_candidates: tuple[TitleCandidate, ...]
    selected_title: TitleCandidate

    # Thumbnail Concepts & Selection
    thumbnail_concepts: tuple[ThumbnailConcept, ...]
    selected_thumbnail: ThumbnailConcept
    physical_thumbnail_artifact: ThumbnailArtifact | None = None

    # Metadata & Descriptions
    description: str = Field(min_length=10, max_length=5000)
    chapters: tuple[VideoChapter, ...] = ()
    tags: tuple[str, ...] = ()
    cta_metadata: CTAMetadata = Field(default_factory=CTAMetadata)
    attribution_block: str | None = None

    # Audit & Provenance
    provenance: PackagingProvenance
    validation_findings: tuple[PackagingValidationFinding, ...] = ()
    version: str = PACKAGING_ENGINE_VERSION

    model_config = ConfigDict(extra="ignore", frozen=True)

    def to_p24d_qa_package(self) -> dict[str, Any]:
        """Expose full bundle context for P24-D creative/brand acceptance QA."""
        return {
            "packaging_plan_id": str(self.packaging_plan_id),
            "channel_dna_revision_id": str(self.channel_dna_revision_id),
            "creative_style_plan_id": str(self.creative_style_plan_id),
            "selected_title": self.selected_title.model_dump(),
            "title_candidates": [c.model_dump() for c in self.title_candidates],
            "selected_thumbnail": self.selected_thumbnail.model_dump(),
            "thumbnail_concepts": [c.model_dump() for c in self.thumbnail_concepts],
            "physical_thumbnail_artifact": self.physical_thumbnail_artifact.model_dump() if self.physical_thumbnail_artifact else None,
            "description": self.description,
            "chapters": [c.model_dump() for c in self.chapters],
            "tags": list(self.tags),
            "attribution_block": self.attribution_block,
            "validation_findings": [f.model_dump() for f in self.validation_findings],
        }

    def to_p25_publish_payload(self) -> dict[str, Any]:
        """Project candidate metadata strictly formatted for P25 Publishing contracts."""
        return {
            "title": self.selected_title.text,
            "description": self.description,
            "tags": list(self.tags),
            "thumbnail_path": str(self.physical_thumbnail_artifact.file_path) if self.physical_thumbnail_artifact else None,
            "channel_dna_revision_id": str(self.channel_dna_revision_id),
            "chapters": [
                {"timestamp": c.formatted_timestamp, "title": c.title, "seconds": c.start_time_seconds}
                for c in self.chapters
            ],
        }
