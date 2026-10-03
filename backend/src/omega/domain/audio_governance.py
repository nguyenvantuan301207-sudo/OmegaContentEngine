"""Audio Library, License Governance, and Final Audio QA Domain Models.

Defines:
- AudioLibraryEntry, AudioUsageStatus / TrackUsageState, AudioAttribution
- AudioLicensePolicy and eligibility evaluation
- AudioQASeverity, AudioQASubsystem, AudioQAFindingCode, AudioQAFinding
- AudioQAStatus, AudioQAResult, AudioAttributionManifest, AudioQAProvenance
- Pure domain logic with zero external I/O or database mutations.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omega.domain.audio_mix import AudioStem, AudioStemRole
from omega.domain.music_direction import TrackUsageState

# Re-export AudioUsageStatus as TrackUsageState for consistency
AudioUsageStatus = TrackUsageState


class AudioQASeverity(StrEnum):
    """Severity classification for Audio QA findings."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKER = "BLOCKER"

    @property
    def rank(self) -> int:
        ranks = {
            AudioQASeverity.INFO: 1,
            AudioQASeverity.WARNING: 2,
            AudioQASeverity.ERROR: 3,
            AudioQASeverity.BLOCKER: 4,
        }
        return ranks.get(self, 0)

    def __ge__(self, other: AudioQASeverity) -> bool:
        if not isinstance(other, AudioQASeverity):
            return NotImplemented
        return self.rank >= other.rank

    def __gt__(self, other: AudioQASeverity) -> bool:
        if not isinstance(other, AudioQASeverity):
            return NotImplemented
        return self.rank > other.rank

    def __le__(self, other: AudioQASeverity) -> bool:
        if not isinstance(other, AudioQASeverity):
            return NotImplemented
        return self.rank <= other.rank

    def __lt__(self, other: AudioQASeverity) -> bool:
        if not isinstance(other, AudioQASeverity):
            return NotImplemented
        return self.rank < other.rank


class AudioQASubsystem(StrEnum):
    """Subsystems emitting Audio QA findings."""

    LIBRARY = "LIBRARY"
    LICENSE = "LICENSE"
    PROVENANCE = "PROVENANCE"
    FORMAT = "FORMAT"
    LOUDNESS = "LOUDNESS"
    PEAK = "PEAK"
    SILENCE = "SILENCE"
    TIMING = "TIMING"
    MIX_BALANCE = "MIX_BALANCE"
    NARRATION = "NARRATION"
    MUSIC = "MUSIC"
    SFX = "SFX"
    MASTER = "MASTER"
    MUX = "MUX"


class AudioQAFindingCode(StrEnum):
    """Authoritative typed taxonomy for Audio QA finding codes."""

    # Integrity & Library
    ASSET_HASH_MISMATCH = "ASSET_HASH_MISMATCH"
    MISSING_REQUIRED_HASH = "MISSING_REQUIRED_HASH"
    MISSING_AUDIO_ASSET = "MISSING_AUDIO_ASSET"
    ZERO_BYTE_AUDIO = "ZERO_BYTE_AUDIO"
    CORRUPT_AUDIO = "CORRUPT_AUDIO"

    # License & Attribution
    BLOCKED_AUDIO_LICENSE = "BLOCKED_AUDIO_LICENSE"
    UNKNOWN_AUDIO_LICENSE = "UNKNOWN_AUDIO_LICENSE"
    EXPIRED_AUDIO_LICENSE = "EXPIRED_AUDIO_LICENSE"
    RESTRICTED_AUDIO_LICENSE = "RESTRICTED_AUDIO_LICENSE"
    MISSING_REQUIRED_ATTRIBUTION = "MISSING_REQUIRED_ATTRIBUTION"

    # Narration & Protection
    MISSING_NARRATION_STEM = "MISSING_NARRATION_STEM"
    MISSING_NARRATION_AUDIO = "MISSING_NARRATION_AUDIO"
    NARRATION_MUTED = "NARRATION_MUTED"
    NARRATION_LINEAGE_BROKEN = "NARRATION_LINEAGE_BROKEN"
    NARRATION_MASKED = "NARRATION_MASKED"

    # Music & SFX
    MUSIC_INSUFFICIENTLY_DUCKED = "MUSIC_INSUFFICIENTLY_DUCKED"
    MUSIC_SELECTION_MISMATCH = "MUSIC_SELECTION_MISMATCH"
    SFX_COLLISION_UNATTENUATED = "SFX_COLLISION_UNATTENUATED"
    SFX_SELECTION_MISMATCH = "SFX_SELECTION_MISMATCH"
    SFX_DENSITY_EXCEEDED = "SFX_DENSITY_EXCEEDED"

    # Silence & Balance
    UNEXPECTED_SILENCE = "UNEXPECTED_SILENCE"
    ALL_SILENT_MASTER = "ALL_SILENT_MASTER"
    AUDIO_STREAM_MISSING = "AUDIO_STREAM_MISSING"
    STEM_DOMINATING_MASTER = "STEM_DOMINATING_MASTER"

    # Mastering & Loudness
    LOUDNESS_TOO_LOW = "LOUDNESS_TOO_LOW"
    LOUDNESS_TOO_HIGH = "LOUDNESS_TOO_HIGH"
    TRUE_PEAK_EXCEEDED = "TRUE_PEAK_EXCEEDED"
    AUDIO_CLIPPING_DETECTED = "AUDIO_CLIPPING_DETECTED"

    # Format & Sync
    WRONG_SAMPLE_RATE = "WRONG_SAMPLE_RATE"
    WRONG_CHANNEL_LAYOUT = "WRONG_CHANNEL_LAYOUT"
    UNEXPECTED_CODEC = "UNEXPECTED_CODEC"
    AUDIO_TOO_SHORT = "AUDIO_TOO_SHORT"
    AUDIO_TOO_LONG = "AUDIO_TOO_LONG"
    AV_DURATION_MISMATCH = "AV_DURATION_MISMATCH"
    TRUNCATED_AUDIO_TAIL = "TRUNCATED_AUDIO_TAIL"


class AudioQAStatus(StrEnum):
    """Final Audio QA verdict."""

    PASS = "PASS"
    REVISE = "REVISE"
    FAIL = "FAIL"


class AudioAttribution(BaseModel):
    """Structured attribution requirements for an audio asset."""

    model_config = ConfigDict(frozen=True)

    creator: str | None = None
    title: str | None = None
    provider: str
    source_reference: str | None = None
    license_name: str | None = None
    attribution_text: str = Field(min_length=1)
    source_url: str | None = None


class AudioLibraryEntry(BaseModel):
    """Governed projection of an audio asset in the Omega Audio Library."""

    model_config = ConfigDict(frozen=True)

    asset_id: str = Field(min_length=1)
    role: AudioStemRole
    source_uri: str = Field(min_length=1)
    source_provider: str = Field(default="in-house")
    source_catalog: str | None = None
    source_sha256: str | None = None
    duration_ms: int = Field(ge=0)
    sample_rate_hz: int = Field(default=48_000, gt=0)
    channels: int = Field(default=2, ge=1, le=8)
    codec_container: str = Field(default="wav")
    usage_state: TrackUsageState = TrackUsageState.UNKNOWN
    license_id: str | None = None
    attribution: AudioAttribution | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    lineage: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_hash_format(self) -> AudioLibraryEntry:
        if self.source_sha256 is not None and len(self.source_sha256) != 64:
            raise ValueError("source_sha256 must be a 64-character hex digest")
        return self


class AudioAttributionEntry(BaseModel):
    """Single item in the final derived attribution manifest."""

    model_config = ConfigDict(frozen=True)

    asset_id: str
    role: AudioStemRole
    provider: str
    creator: str | None = None
    source_reference: str | None = None
    license_name: str | None = None
    attribution_text: str


class AudioAttributionManifest(BaseModel):
    """Derived attribution manifest generated for accepted production artifacts."""

    model_config = ConfigDict(frozen=True)

    manifest_id: UUID = Field(default_factory=uuid4)
    entries: list[AudioAttributionEntry] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def entry_count(self) -> int:
        return len(self.entries)


class AudioQAProvenance(BaseModel):
    """Immutable audit metadata linking Audio QA to execution lineage."""

    model_config = ConfigDict(frozen=True)

    provenance_id: UUID = Field(default_factory=uuid4)
    audio_mix_plan_hash: str | None = None
    master_artifact_hash: str | None = None
    music_selection_id: str | None = None
    sfx_cue_count: int = 0
    qa_engine_version: str = "p23d-v1"
    finding_count: int = 0
    final_status: AudioQAStatus = AudioQAStatus.PASS
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AudioQAFinding(BaseModel):
    """Typed finding produced during Audio QA evaluation."""

    model_config = ConfigDict(frozen=True)

    finding_id: UUID = Field(default_factory=uuid4)
    finding_code: AudioQAFindingCode
    severity: AudioQASeverity
    subsystem: AudioQASubsystem
    stem_id: str | None = None
    asset_id: str | None = None
    timeline_range_ms: tuple[int, int] | None = None
    explanation: str
    recommended_remediation: str | None = None
    contributing_authorities: tuple[str, ...] = ("P23-D",)


class AudioQAResult(BaseModel):
    """Final outcome and audit trail of Audio QA evaluation."""

    model_config = ConfigDict(frozen=True)

    result_id: UUID = Field(default_factory=uuid4)
    status: AudioQAStatus
    findings: list[AudioQAFinding] = Field(default_factory=list)
    measured_loudness_lufs: float | None = None
    measured_true_peak_dbtp: float | None = None
    measured_duration_sec: float | None = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provenance: AudioQAProvenance | None = None
    attribution_manifest: AudioAttributionManifest | None = None

    @property
    def is_accepted(self) -> bool:
        return self.status == AudioQAStatus.PASS

    @property
    def has_blockers(self) -> bool:
        return any(f.severity == AudioQASeverity.BLOCKER for f in self.findings)

    @property
    def has_errors(self) -> bool:
        return any(f.severity == AudioQASeverity.ERROR for f in self.findings)

    @property
    def blocker_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == AudioQASeverity.BLOCKER)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == AudioQASeverity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == AudioQASeverity.WARNING)


class AudioRenderGateError(RuntimeError):
    """Raised when an unaccepted Audio QA result fails the acceptance gate."""

    def __init__(self, message: str, qa_result: AudioQAResult | None = None) -> None:
        super().__init__(message)
        self.qa_result = qa_result


class AudioLicensePolicy:
    """Deterministic usage and licensing eligibility evaluator."""

    ALLOWED_STATES = frozenset({
        TrackUsageState.OWNED,
        TrackUsageState.GENERATED,
        TrackUsageState.LICENSED,
        TrackUsageState.PUBLIC_DOMAIN,
        TrackUsageState.ATTRIBUTION_REQUIRED,
    })

    @classmethod
    def evaluate_entry(cls, entry: AudioLibraryEntry) -> tuple[bool, str | None, AudioQAFindingCode | None]:
        """Evaluates whether an audio library entry is legally and operationally permitted."""
        # 1. Narration has specific provider-based evaluation
        if entry.role == AudioStemRole.NARRATION:
            if entry.usage_state == TrackUsageState.BLOCKED:
                return False, f"Narration asset {entry.asset_id} is explicitly BLOCKED.", AudioQAFindingCode.BLOCKED_AUDIO_LICENSE
            return True, None, None

        # 2. Expiration check
        if entry.expires_at is not None and entry.expires_at < datetime.now(UTC):
            return False, f"Asset {entry.asset_id} license expired at {entry.expires_at.isoformat()}.", AudioQAFindingCode.EXPIRED_AUDIO_LICENSE

        # 3. Known blocked states
        if entry.usage_state == TrackUsageState.BLOCKED:
            return False, f"Asset {entry.asset_id} is marked BLOCKED.", AudioQAFindingCode.BLOCKED_AUDIO_LICENSE

        # 4. Unknown usage state fails closed
        if entry.usage_state == TrackUsageState.UNKNOWN:
            return False, f"Asset {entry.asset_id} has UNKNOWN usage rights (fail-closed policy).", AudioQAFindingCode.UNKNOWN_AUDIO_LICENSE

        # 5. Permitted states
        if entry.usage_state not in cls.ALLOWED_STATES:
            return False, f"Asset {entry.asset_id} has unpermitted usage state {entry.usage_state}.", AudioQAFindingCode.BLOCKED_AUDIO_LICENSE

        # 6. Attribution requirement completeness
        if entry.usage_state == TrackUsageState.ATTRIBUTION_REQUIRED:
            if not entry.attribution or not entry.attribution.attribution_text.strip():
                return False, f"Asset {entry.asset_id} requires attribution but lacks structured attribution text.", AudioQAFindingCode.MISSING_REQUIRED_ATTRIBUTION

        return True, None, None
