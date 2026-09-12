"""Voice, language, profile, and speed policies for local TTS engines."""

from __future__ import annotations

from typing import Any

from omega.application.local_tts.engine import (
    UnsupportedLanguageError,
    UnsupportedSpeedError,
    VoiceNotFoundError,
)

# Supported language codes and their internal Kokoro model mappings
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en-US": "en-us",
    "en-us": "en-us",
    "en_US": "en-us",
    "en_us": "en-us",
    "en-GB": "en-gb",
    "en-gb": "en-gb",
    "en_GB": "en-gb",
    "en_gb": "en-gb",
}

# Explicit voice metadata registry (no runtime guesswork)
VOICE_REGISTRY: dict[str, dict[str, Any]] = {
    "af_heart": {
        "language": "en-US",
        "gender": "female",
        "label": "Heart (US Female)",
        "description": "US Female (Warm / Expressive Flagship Narration)",
        "is_default": True,
    },
    "af_bella": {
        "language": "en-US",
        "gender": "female",
        "label": "Bella (US Female)",
        "description": "US Female (Crisp / Direct)",
        "is_default": False,
    },
    "am_michael": {
        "language": "en-US",
        "gender": "male",
        "label": "Michael (US Male)",
        "description": "US Male (Expository / Documentary)",
        "is_default": True,
    },
    "am_fenrir": {
        "language": "en-US",
        "gender": "male",
        "label": "Fenrir (US Male)",
        "description": "US Male (Deep Baritone / Cinematic)",
        "is_default": False,
    },
    "am_puck": {
        "language": "en-US",
        "gender": "male",
        "label": "Puck (US Male)",
        "description": "US Male (Energetic / Conversational)",
        "is_default": False,
    },
    "bf_emma": {
        "language": "en-GB",
        "gender": "female",
        "label": "Emma (UK Female)",
        "description": "UK Female (BBC / Authoritative)",
        "is_default": True,
    },
    "bm_george": {
        "language": "en-GB",
        "gender": "male",
        "label": "George (UK Male)",
        "description": "UK Male (Classical British Expository)",
        "is_default": True,
    },
}

# Policy-defined default voice mappings
DEFAULT_VOICES: dict[tuple[str, str], str] = {
    ("en-US", "female"): "af_heart",
    ("en-US", "male"): "am_michael",
    ("en-GB", "female"): "bf_emma",
    ("en-GB", "male"): "bm_george",
}

DEFAULT_OVERALL_VOICE = "af_heart"
DEFAULT_OVERALL_LANGUAGE = "en-US"
DEFAULT_SPEED = 1.0

# Explicit speed bounds: presets 0.9, 1.0, 1.1; supported range [0.8, 1.2]
MIN_SPEED = 0.8
MAX_SPEED = 1.2
SPEED_PRESETS = [0.9, 1.0, 1.1]

# Profiles
SUPPORTED_PROFILES = {"fast", "quality"}
DEFAULT_PROFILE = "quality"

# Devices
SUPPORTED_DEVICES = {"auto", "cpu", "cuda"}
DEFAULT_DEVICE = "auto"


def normalize_language(lang: str | None) -> tuple[str, str]:
    """Normalize language code to canonical OMEGA (e.g. 'en-US') and Kokoro internal (e.g. 'en-us')."""
    if not lang:
        return ("en-US", "en-us")
    normalized_key = lang.strip()
    if normalized_key not in SUPPORTED_LANGUAGES:
        valid_options = ", ".join(sorted(set(SUPPORTED_LANGUAGES.keys())))
        raise UnsupportedLanguageError(
            f"Unsupported language '{lang}'. Supported languages: {valid_options}"
        )
    kokoro_lang = SUPPORTED_LANGUAGES[normalized_key]
    canonical_lang = "en-GB" if kokoro_lang == "en-gb" else "en-US"
    return (canonical_lang, kokoro_lang)


def validate_voice(voice: str | None, language: str | None = None) -> str:
    """Validate voice against explicit registry and language compatibility."""
    if not voice or voice in ("default", "neutral_default"):
        canonical_lang, _ = normalize_language(language)
        return DEFAULT_VOICES.get((canonical_lang, "female"), DEFAULT_OVERALL_VOICE)

    cleaned = voice.strip()
    if cleaned not in VOICE_REGISTRY:
        valid_voices = ", ".join(sorted(VOICE_REGISTRY.keys()))
        raise VoiceNotFoundError(
            f"Unsupported voice '{cleaned}'. Supported voices: {valid_voices}"
        )

    # Cross-check: strict language compatibility
    if language:
        canonical_lang, _ = normalize_language(language)
        expected_lang = VOICE_REGISTRY[cleaned]["language"]
        if canonical_lang != expected_lang:
            raise VoiceNotFoundError(
                f"Voice '{cleaned}' ({expected_lang}) is incompatible with language '{canonical_lang}'"
            )

    return cleaned


def validate_speed(speed: float | int | None) -> float:
    """Validate speaking rate against explicit supported bounds [0.8, 1.2].

    Rejects out-of-bound values explicitly rather than silently clamping.
    """
    if speed is None:
        return DEFAULT_SPEED
    try:
        val = float(speed)
    except (ValueError, TypeError) as e:
        raise UnsupportedSpeedError(f"Invalid speed value '{speed}': must be a float") from e

    if val < MIN_SPEED or val > MAX_SPEED:
        raise UnsupportedSpeedError(
            f"Unsupported speed {val:.2f}. Must be between {MIN_SPEED} and {MAX_SPEED}"
        )
    return round(val, 2)


def validate_profile(profile: str | None) -> str:
    """Validate profile name ('fast' or 'quality')."""
    if not profile:
        return DEFAULT_PROFILE
    cleaned = profile.strip().lower()
    if cleaned not in SUPPORTED_PROFILES:
        raise ValueError(
            f"Unsupported profile '{profile}'. Supported: {', '.join(sorted(SUPPORTED_PROFILES))}"
        )
    return cleaned


def validate_device(device: str | None) -> str:
    """Validate synthesis device ('auto', 'cpu', or 'cuda')."""
    if not device:
        return DEFAULT_DEVICE
    cleaned = device.strip().lower()
    if cleaned not in SUPPORTED_DEVICES:
        raise ValueError(
            f"Unsupported device '{device}'. Supported: {', '.join(sorted(SUPPORTED_DEVICES))}"
        )
    return cleaned
