"""Local TTS Engine abstraction, contracts, and error hierarchy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class LocalTTSError(Exception):
    """Base exception for all local TTS engine errors."""


class ModelNotFoundError(LocalTTSError):
    """Raised when model weights or voice assets are missing from filesystem."""


class ChecksumMismatchError(LocalTTSError):
    """Raised when model weights or voice assets fail cryptographic SHA-256 integrity verification."""


class VoiceNotFoundError(LocalTTSError):
    """Raised when a requested voice is unknown or unsupported by policy."""


class UnsupportedLanguageError(LocalTTSError):
    """Raised when a requested language is unknown or unsupported."""


class UnsupportedSpeedError(LocalTTSError):
    """Raised when speaking rate is outside explicit supported bounds."""


class UnsupportedDeviceError(LocalTTSError):
    """Raised when a requested acceleration device (e.g. CUDA) is unavailable."""


class SynthesisFailedError(LocalTTSError):
    """Raised when synthesis execution fails."""


@dataclass(frozen=True)
class SynthesisResult:
    """Normalized metadata contract returned by local speech synthesis engines."""

    audio_path: Path
    duration_ms: int
    native_sample_rate: int
    canonical_sample_rate: int
    channels: int
    engine: str
    model: str
    voice: str
    language: str
    speed: float
    device: str
    generation_duration_ms: int
    content_hash: str
    profile: str = "quality"


class LocalTTSEngine(Protocol):
    """Protocol contract for local text-to-speech synthesis engines."""

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        voice: str | None = None,
        language: str | None = None,
        speed: float | None = None,
        profile: str | None = None,
    ) -> SynthesisResult:
        """Synthesize narration text to canonical WAV audio and return normalized metadata."""
        ...
