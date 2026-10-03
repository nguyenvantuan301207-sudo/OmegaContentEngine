"""Typed, renderer-neutral Music Director authority models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

MUSIC_DIRECTOR_VERSION = "p23b-v1"


class MusicIntentType(StrEnum):
    NONE = "NONE"
    INTRO = "INTRO"
    BACKGROUND = "BACKGROUND"
    BUILD = "BUILD"
    TENSION = "TENSION"
    DISCOVERY = "DISCOVERY"
    REVEAL = "REVEAL"
    PAYOFF = "PAYOFF"
    REFLECTION = "REFLECTION"
    RESOLUTION = "RESOLUTION"
    OUTRO = "OUTRO"


class MusicTransitionIntent(StrEnum):
    CONTINUE = "CONTINUE"
    FADE_IN = "FADE_IN"
    FADE_OUT = "FADE_OUT"
    CROSSFADE = "CROSSFADE"
    CUT = "CUT"
    REPLACE_AT_BOUNDARY = "REPLACE_AT_BOUNDARY"


class VocalPolicy(StrEnum):
    INSTRUMENTAL_ONLY = "INSTRUMENTAL_ONLY"
    VOCALS_ALLOWED = "VOCALS_ALLOWED"
    VOCALS_ONLY_WHEN_NO_NARRATION = "VOCALS_ONLY_WHEN_NO_NARRATION"


class TrackUsageState(StrEnum):
    OWNED = "OWNED"
    GENERATED = "GENERATED"
    LICENSED = "LICENSED"
    PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
    ATTRIBUTION_REQUIRED = "ATTRIBUTION_REQUIRED"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"


class MusicDurationPolicy(StrEnum):
    TRIM = "TRIM"
    LOOP_SAFE = "LOOP_SAFE"
    CONTINUE = "CONTINUE"
    NO_MUSIC = "NO_MUSIC"


class MusicContinuityFindingCode(StrEnum):
    EXCESSIVE_MUSIC_SWITCHING = "EXCESSIVE_MUSIC_SWITCHING"
    MUSIC_MOTIF_DRIFT = "MUSIC_MOTIF_DRIFT"
    UNJUSTIFIED_TRACK_CHANGE = "UNJUSTIFIED_TRACK_CHANGE"
    MUSIC_ARC_DISCONTINUITY = "MUSIC_ARC_DISCONTINUITY"
    EARLY_ENERGY_PEAK = "EARLY_ENERGY_PEAK"
    ENERGY_OSCILLATION = "ENERGY_OSCILLATION"


class MusicIntent(BaseModel):
    model_config = ConfigDict(frozen=True)
    intent: MusicIntentType
    mood: tuple[str, ...] = ()
    energy: float = Field(ge=0.0, le=1.0)
    intensity: float = Field(ge=0.0, le=1.0)
    tempo_min_bpm: int | None = Field(default=None, ge=30, le=240)
    tempo_max_bpm: int | None = Field(default=None, ge=30, le=240)
    instrumental_preferred: bool = True
    vocal_policy: VocalPolicy = VocalPolicy.INSTRUMENTAL_ONLY
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    narrative_role: str
    continuity_group: str
    transition_intent: MusicTransitionIntent

    @model_validator(mode="after")
    def validate_intent(self) -> MusicIntent:
        if self.end_ms <= self.start_ms:
            raise ValueError("music intent end_ms must exceed start_ms")
        if (
            self.tempo_min_bpm is not None
            and self.tempo_max_bpm is not None
            and self.tempo_min_bpm > self.tempo_max_bpm
        ):
            raise ValueError("tempo range is reversed")
        return self


class MusicTrackMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)
    asset_id: str = Field(min_length=1)
    source_artifact: str = Field(min_length=1)
    exists: bool = True
    duration_ms: int = Field(gt=0)
    genre: str | None = None
    moods: tuple[str, ...] = ()
    energy: float | None = Field(default=None, ge=0.0, le=1.0)
    bpm: int | None = Field(default=None, ge=30, le=240)
    instrumentation: tuple[str, ...] = ()
    has_vocals: bool | None = None
    loop_safe: bool | None = None
    catalog: str | None = None
    usage_state: TrackUsageState = TrackUsageState.UNKNOWN
    source_sha256: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class MusicCandidateScore(BaseModel):
    model_config = ConfigDict(frozen=True)
    asset_id: str
    eligible: bool
    total_score: float
    components: dict[str, float] = Field(default_factory=dict)
    rejected_reasons: tuple[str, ...] = ()


class MusicCue(BaseModel):
    model_config = ConfigDict(frozen=True)
    cue_id: str
    intent: MusicIntent
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    target_energy: float = Field(ge=0.0, le=1.0)
    mood: tuple[str, ...]
    continuity_group: str
    narrative_section_id: str
    narrative_section_order: int = Field(ge=1)
    transition_intent: MusicTransitionIntent
    selected_asset_id: str | None = None
    duration_policy: MusicDurationPolicy = MusicDurationPolicy.NO_MUSIC
    fade_in_ms: int = Field(default=0, ge=0)
    fade_out_ms: int = Field(default=0, ge=0)


class MusicArc(BaseModel):
    model_config = ConfigDict(frozen=True)
    version: str = MUSIC_DIRECTOR_VERSION
    narrative_plan_id: str
    pacing_profile: str
    channel_profile: str
    cues: tuple[MusicCue, ...]
    energy_curve: tuple[float, ...]
    findings: tuple[MusicContinuityFindingCode, ...] = ()


class MusicSelectionResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    cue: MusicCue
    selected_track: MusicTrackMetadata | None
    candidate_scores: tuple[MusicCandidateScore, ...]
    rationale: str
    director_version: str = MUSIC_DIRECTOR_VERSION
