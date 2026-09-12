"""Local TTS engine package for OMEGA."""

from __future__ import annotations

from omega.application.local_tts.chunker import chunk_narration_segment
from omega.application.local_tts.engine import (
    ChecksumMismatchError,
    LocalTTSEngine,
    LocalTTSError,
    ModelNotFoundError,
    SynthesisFailedError,
    SynthesisResult,
    UnsupportedDeviceError,
    UnsupportedLanguageError,
    UnsupportedSpeedError,
    VoiceNotFoundError,
)
from omega.application.local_tts.kokoro_engine import KokoroLocalTTSEngine
from omega.application.local_tts.text_normalizer import normalize_narration_text
from omega.application.local_tts.voice_policy import (
    DEFAULT_OVERALL_LANGUAGE,
    DEFAULT_OVERALL_VOICE,
    DEFAULT_PROFILE,
    DEFAULT_SPEED,
    DEFAULT_VOICES,
    VOICE_REGISTRY,
    normalize_language,
    validate_profile,
    validate_speed,
    validate_voice,
)

__all__ = [
    "ChecksumMismatchError",
    "DEFAULT_OVERALL_LANGUAGE",
    "DEFAULT_OVERALL_VOICE",
    "DEFAULT_PROFILE",
    "DEFAULT_SPEED",
    "DEFAULT_VOICES",
    "KokoroLocalTTSEngine",
    "LocalTTSEngine",
    "LocalTTSError",
    "ModelNotFoundError",
    "SynthesisFailedError",
    "SynthesisResult",
    "UnsupportedDeviceError",
    "UnsupportedLanguageError",
    "UnsupportedSpeedError",
    "VOICE_REGISTRY",
    "VoiceNotFoundError",
    "chunk_narration_segment",
    "normalize_language",
    "normalize_narration_text",
    "validate_profile",
    "validate_speed",
    "validate_voice",
]
