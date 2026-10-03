"""Typed, renderer-neutral SFX Director authority models for P23-C."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omega.domain.music_direction import TrackUsageState

SFX_DIRECTOR_VERSION = "p23c-v1"
SFXUsageState = TrackUsageState


class SFXIntentType(StrEnum):
    NONE = "NONE"
    UI_CLICK = "UI_CLICK"
    WHOOSH = "WHOOSH"
    IMPACT = "IMPACT"
    RISER = "RISER"
    REVEAL = "REVEAL"
    TRANSITION = "TRANSITION"
    ACCENT = "ACCENT"
    NOTIFICATION = "NOTIFICATION"
    MECHANICAL = "MECHANICAL"
    ENVIRONMENTAL = "ENVIRONMENTAL"
    AMBIENCE = "AMBIENCE"
    STINGER = "STINGER"


class SFXProminence(StrEnum):
    SUBTLE = "SUBTLE"
    BALANCED = "BALANCED"
    PROMINENT = "PROMINENT"
    ACCENT = "ACCENT"


class SFXDurationPolicy(StrEnum):
    EXACT = "EXACT"
    TRIM = "TRIM"
    PAD = "PAD"
    NONE = "NONE"


class SFXFindingCode(StrEnum):
    SFX_OVERUSE = "SFX_OVERUSE"
    SFX_CLUSTERING = "SFX_CLUSTERING"
    EXCESSIVE_TRANSITION_SFX = "EXCESSIVE_TRANSITION_SFX"
    REPETITIVE_ACCENTS = "REPETITIVE_ACCENTS"
    SFX_ON_EVERY_BEAT = "SFX_ON_EVERY_BEAT"
    SEMANTIC_COLLISION = "SEMANTIC_COLLISION"
    NARRATION_COLLISION = "NARRATION_COLLISION"
    MUSIC_PEAK_COLLISION = "MUSIC_PEAK_COLLISION"
    ACCIDENTAL_REPETITION = "ACCIDENTAL_REPETITION"
    DURATION_OVERFLOW = "DURATION_OVERFLOW"


class SFXAssetMetadata(BaseModel):
    """Metadata representing an available SFX audio asset."""

    model_config = ConfigDict(frozen=True)

    asset_id: str = Field(min_length=1)
    source_artifact: str = Field(min_length=1)
    intent: SFXIntentType
    duration_ms: int = Field(gt=0)
    energy: float = Field(default=0.5, ge=0.0, le=1.0)
    prominence: SFXProminence = SFXProminence.BALANCED
    loop_safe: bool = False
    source_catalog: str = "local"
    usage_state: TrackUsageState = TrackUsageState.UNKNOWN
    source_sha256: str | None = None
    tags: tuple[str, ...] = ()
    format: str = "wav"
    exists: bool = True

    @model_validator(mode="after")
    def validate_sha(self) -> SFXAssetMetadata:
        if self.source_sha256 is not None and len(self.source_sha256) != 64:
            raise ValueError("source_sha256 must be a 64-character digest")
        return self


class SFXIntent(BaseModel):
    """Editorial intent for why an SFX cue exists."""

    model_config = ConfigDict(frozen=True)

    intent: SFXIntentType
    editorial_purpose: str = Field(min_length=1)
    energy: float = Field(default=0.5, ge=0.0, le=1.0)
    prominence: SFXProminence = SFXProminence.BALANCED
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    narrative_role: str | None = None
    visual_role: str | None = None
    camera_intent: str | None = None
    transition_intent: str | None = None
    motif_id: str | None = None
    collision_priority: int = Field(default=50, ge=0, le=100)


class SFXCue(BaseModel):
    """Editorial SFX Cue expressing when, what, and how prominent a sound should be."""

    model_config = ConfigDict(frozen=True)

    cue_id: str = Field(min_length=1)
    intent: SFXIntent
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    energy: float = Field(default=0.5, ge=0.0, le=1.0)
    prominence: SFXProminence = SFXProminence.BALANCED
    narrative_section_id: UUID | None = None
    visual_beat_id: UUID | None = None
    camera_transition_lineage: dict[str, str] = Field(default_factory=dict)
    selected_asset_id: str | None = None
    selected_asset_metadata: SFXAssetMetadata | None = None
    continuity_motif_id: str | None = None
    collision_priority: int = Field(default=50, ge=0, le=100)
    fade_in_ms: int = Field(default=0, ge=0)
    fade_out_ms: int = Field(default=0, ge=0)
    gain_db: float = Field(default=0.0, ge=-96.0, le=12.0)
    suppressed: bool = False
    suppression_reason: str | None = None


class SFXCandidateScore(BaseModel):
    """Scoring breakdown for an evaluated candidate SFX asset."""

    model_config = ConfigDict(frozen=True)

    asset_id: str
    eligible: bool
    ineligibility_reasons: tuple[str, ...] = ()
    total_score: float = 0.0
    intent_score: float = 0.0
    energy_score: float = 0.0
    duration_score: float = 0.0
    context_score: float = 0.0
    dna_score: float = 0.0
    continuity_score: float = 0.0
    repetition_penalty: float = 0.0
    rationale: str = ""


class SFXSelectionResult(BaseModel):
    """Result of selecting an SFX asset for an SFXCue."""

    model_config = ConfigDict(frozen=True)

    cue: SFXCue
    selected_asset: SFXAssetMetadata | None = None
    candidates: tuple[SFXCandidateScore, ...] = ()
    rationale: str = ""


class SFXPlan(BaseModel):
    """Complete editorial SFX plan containing cues, selections, and quality findings."""

    model_config = ConfigDict(frozen=True)

    version: str = SFX_DIRECTOR_VERSION
    cues: tuple[SFXCue, ...] = ()
    selections: tuple[SFXSelectionResult, ...] = ()
    findings: tuple[SFXFindingCode, ...] = ()
    density_events_per_minute: float = 0.0
    provenance: dict[str, Any] = Field(default_factory=dict)
