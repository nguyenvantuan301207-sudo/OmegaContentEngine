"""Renderer-neutral Audio Mixing v2 authority models."""

from __future__ import annotations

import math
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

AUDIO_MIX_PLAN_VERSION = "p23a-v1"


class AudioStemRole(StrEnum):
    NARRATION = "NARRATION"
    MUSIC = "MUSIC"
    SFX = "SFX"
    AMBIENCE = "AMBIENCE"


class AudioTimelineFindingCode(StrEnum):
    AUDIO_GAP = "AUDIO_GAP"
    AUDIO_OVERFLOW = "AUDIO_OVERFLOW"
    STEM_OUT_OF_RANGE = "STEM_OUT_OF_RANGE"
    NARRATION_TIMING_MISMATCH = "NARRATION_TIMING_MISMATCH"
    MISSING_AUDIO_FILE = "MISSING_AUDIO_FILE"
    EMPTY_NARRATION = "EMPTY_NARRATION"
    ALL_SILENT_MIX = "ALL_SILENT_MIX"


class GainEnvelopePoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    offset_ms: int = Field(ge=0)
    gain_db: float

    @model_validator(mode="after")
    def validate_gain(self) -> GainEnvelopePoint:
        if not math.isfinite(self.gain_db) or not -96.0 <= self.gain_db <= 24.0:
            raise ValueError("gain_db must be finite and between -96 and 24 dB")
        return self


class AudioStem(BaseModel):
    """A supplied audio source and its technical placement; never a creative choice."""

    model_config = ConfigDict(frozen=True)
    stem_id: str = Field(min_length=1, max_length=160)
    role: AudioStemRole
    source_artifact: str = Field(min_length=1)
    source_identity: str = Field(min_length=1)
    source_sha256: str | None = None
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    trim_start_ms: int = Field(default=0, ge=0)
    gain_db: float = 0.0
    fade_in_ms: int = Field(default=0, ge=0)
    fade_out_ms: int = Field(default=0, ge=0)
    priority: int = Field(default=50, ge=0, le=100)
    optional: bool = False
    lineage: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_bounds(self) -> AudioStem:
        duration = self.end_ms - self.start_ms
        if duration <= 0:
            raise ValueError("STEM_OUT_OF_RANGE: end_ms must be greater than start_ms")
        if self.fade_in_ms + self.fade_out_ms > duration:
            raise ValueError("STEM_OUT_OF_RANGE: fades exceed stem duration")
        if not math.isfinite(self.gain_db) or not -96.0 <= self.gain_db <= 12.0:
            raise ValueError("gain_db must be finite and between -96 and 12 dB")
        if self.source_sha256 is not None and len(self.source_sha256) != 64:
            raise ValueError("source_sha256 must be a 64-character digest")
        return self


class LoudnessPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    integrated_lufs: float = -16.0
    true_peak_dbtp: float = -1.5
    loudness_range_lu: float = 7.0
    tolerance_lu: float = 1.0
    limiter_release_ms: int = Field(default=50, ge=10, le=1000)


class DuckingPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    enabled: bool = True
    attenuation_db: float = Field(default=-10.0, ge=-24.0, le=-3.0)
    attack_ms: int = Field(default=80, ge=5, le=500)
    release_ms: int = Field(default=350, ge=20, le=2000)
    ambience_enabled: bool = True


class AudioMixPlan(BaseModel):
    """Derived/recomputable plan. It deliberately contains no FFmpeg syntax."""

    model_config = ConfigDict(frozen=True)
    version: str = AUDIO_MIX_PLAN_VERSION
    timeline_duration_ms: int = Field(gt=0)
    sample_rate_hz: int = 48_000
    channel_layout: str = "stereo"
    stems: tuple[AudioStem, ...]
    gain_envelopes: dict[str, tuple[GainEnvelopePoint, ...]] = Field(default_factory=dict)
    narration_intervals_ms: tuple[tuple[int, int], ...] = ()
    loudness: LoudnessPolicy = Field(default_factory=LoudnessPolicy)
    ducking: DuckingPolicy = Field(default_factory=DuckingPolicy)
    duration_tolerance_ms: int = Field(default=80, ge=0, le=500)

    @model_validator(mode="after")
    def validate_plan(self) -> AudioMixPlan:
        if self.sample_rate_hz != 48_000 or self.channel_layout != "stereo":
            raise ValueError("canonical audio format is 48000 Hz stereo")
        ids = [stem.stem_id for stem in self.stems]
        if len(ids) != len(set(ids)):
            raise ValueError("stem_id values must be unique")
        narration = [stem for stem in self.stems if stem.role == AudioStemRole.NARRATION]
        if not narration:
            raise ValueError("EMPTY_NARRATION: a narration stem is required")
        for stem in self.stems:
            if stem.end_ms > self.timeline_duration_ms:
                raise ValueError("AUDIO_OVERFLOW: stem exceeds the production timeline")
            points = self.gain_envelopes.get(stem.stem_id, ())
            if any(point.offset_ms > stem.end_ms - stem.start_ms for point in points):
                raise ValueError("STEM_OUT_OF_RANGE: envelope exceeds stem bounds")
            if tuple(sorted(points, key=lambda point: point.offset_ms)) != tuple(points):
                raise ValueError("gain envelope points must be ordered")
        for start, end in self.narration_intervals_ms:
            if start < 0 or end <= start or end > self.timeline_duration_ms:
                raise ValueError("NARRATION_TIMING_MISMATCH: invalid narration interval")
        return self
