"""Authoritative Narration Policy Resolution for OMEGA Local TTS.

Provides a single authoritative resolver:
resolve_narration_policy(request_policy, channel_policy, settings_defaults)

Strict field-wise precedence:
request explicit fields > channel narration metadata > settings defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omega.application.local_tts.voice_policy import (
    DEFAULT_DEVICE,
    DEFAULT_OVERALL_LANGUAGE,
    DEFAULT_OVERALL_VOICE,
    DEFAULT_PROFILE,
    DEFAULT_SPEED,
    normalize_language,
    validate_device,
    validate_profile,
    validate_speed,
    validate_voice,
)
from omega.domain.production import VoiceProfileSnapshot


@dataclass(frozen=True)
class ResolvedNarrationPolicy:
    """Immutable, fully-validated narration policy resolved at execution time."""

    provider: str
    profile: str
    language: str
    voice: str
    speed: float
    device: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "profile": self.profile,
            "language": self.language,
            "voice": self.voice,
            "speed": self.speed,
            "device": self.device,
        }


def is_legacy_placeholder(data: dict[str, Any] | VoiceProfileSnapshot | None) -> bool:
    """Detect whether a voice profile configuration matches historical/default placeholder values.

    Historical placeholder requests typically have:
    - provider: "PLACEHOLDER" (or None)
    - voice_ref: "neutral_default" (or "default", None)
    - language: "en"
    - speaking_rate: 1.0

    Such combinations indicate an UNSPECIFIED narration request that must fall through
    to channel narration metadata and settings defaults.
    """
    if data is None:
        return False

    if isinstance(data, VoiceProfileSnapshot):
        provider = data.provider
        voice_ref = data.voice_ref
    elif isinstance(data, dict):
        provider = data.get("provider")
        voice_ref = data.get("voice_ref") or data.get("voice")
    else:
        return False

    is_placeholder_provider = provider in ("PLACEHOLDER", None, "")
    is_placeholder_voice = voice_ref in ("neutral_default", "default", None, "")
    return is_placeholder_provider and is_placeholder_voice


def extract_explicit_request_policy(
    request_policy: dict[str, Any] | VoiceProfileSnapshot | None = None,
    request_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract explicitly-specified narration policy fields from a request.

    Canonical legacy mapping:
    - voice_ref -> voice
    - speaking_rate -> speed
    - language -> language

    Structured metadata source:
    - request.metadata["narration"] provides profile, device, or fallback overrides.

    If the request represents a legacy placeholder, its default placeholder fields
    are treated as UNSPECIFIED so they fall through to channel and settings defaults.
    """
    explicit: dict[str, Any] = {}

    # Check for structured narration in metadata first (profile, device, etc.)
    meta_narration = {}
    if request_metadata and isinstance(request_metadata, dict):
        meta_narration = request_metadata.get("narration") or {}
        if isinstance(meta_narration, dict):
            if meta_narration.get("profile"):
                explicit["profile"] = str(meta_narration["profile"]).strip()
            if meta_narration.get("device"):
                explicit["device"] = str(meta_narration["device"]).strip()
            if meta_narration.get("voice") or meta_narration.get("voice_ref"):
                explicit["voice"] = str(meta_narration.get("voice") or meta_narration.get("voice_ref")).strip()
            if meta_narration.get("speed") or meta_narration.get("speaking_rate"):
                explicit["speed"] = meta_narration.get("speed") or meta_narration.get("speaking_rate")
            if meta_narration.get("language"):
                explicit["language"] = str(meta_narration["language"]).strip()

    if request_policy is None:
        return explicit

    # If it's a legacy placeholder, do NOT let default placeholder values override channel/settings
    if is_legacy_placeholder(request_policy):
        return explicit

    # Extract from VoiceProfileSnapshot or request dict
    if isinstance(request_policy, VoiceProfileSnapshot):
        # Canonical mapping: voice_ref -> voice
        if request_policy.voice_ref and request_policy.voice_ref not in ("neutral_default", "default"):
            explicit["voice"] = request_policy.voice_ref.strip()

        # Canonical mapping: speaking_rate -> speed
        # If explicitly different from 1.0 or present in model_fields_set
        if "speaking_rate" in request_policy.model_fields_set or request_policy.speaking_rate != 1.0:
            explicit["speed"] = request_policy.speaking_rate

        # Canonical mapping: language -> language
        if "language" in request_policy.model_fields_set:
            explicit["language"] = request_policy.language.strip()
        elif request_policy.language and request_policy.language != "en":
            # If not the default "en", it was explicitly set
            explicit["language"] = request_policy.language.strip()
    elif isinstance(request_policy, dict):
        # Dict may contain modern keys (voice, speed, profile, device) or legacy keys
        voice_val = request_policy.get("voice") or request_policy.get("voice_ref")
        if voice_val and str(voice_val).strip() not in ("neutral_default", "default"):
            explicit["voice"] = str(voice_val).strip()

        speed_val = request_policy.get("speed") or request_policy.get("speaking_rate")
        if speed_val is not None:
            explicit["speed"] = speed_val

        lang_val = request_policy.get("language")
        if lang_val:
            explicit["language"] = str(lang_val).strip()

        profile_val = request_policy.get("profile")
        if profile_val:
            explicit["profile"] = str(profile_val).strip()

        device_val = request_policy.get("device")
        if device_val:
            explicit["device"] = str(device_val).strip()

    return explicit


# Canonical adapter between VoiceProfileSnapshot and NarrationPolicy explicit fields
adapt_voice_profile_snapshot = extract_explicit_request_policy


def extract_channel_narration_policy(
    channel_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract narration policy preferences stored in channel.metadata_['narration'] or direct dict."""
    if not channel_metadata or not isinstance(channel_metadata, dict):
        return {}

    # Support both channel.metadata_ wrapping {"narration": {...}} and direct narration dict
    if "narration" in channel_metadata and isinstance(channel_metadata["narration"], dict):
        narration = channel_metadata["narration"]
    else:
        narration = channel_metadata

    channel_policy: dict[str, Any] = {}
    voice = narration.get("voice") or narration.get("voice_ref")
    if voice and str(voice).strip() not in ("neutral_default", "default"):
        channel_policy["voice"] = str(voice).strip()

    speed = narration.get("speed") or narration.get("speaking_rate")
    if speed is not None:
        channel_policy["speed"] = speed

    lang = narration.get("language")
    if lang:
        channel_policy["language"] = str(lang).strip()

    profile = narration.get("profile")
    if profile:
        channel_policy["profile"] = str(profile).strip()

    device = narration.get("device")
    if device:
        channel_policy["device"] = str(device).strip()

    return channel_policy


def get_settings_narration_defaults(settings_obj: Any = None) -> dict[str, Any]:
    """Extract server/settings defaults."""
    profile = getattr(settings_obj, "local_tts_profile", DEFAULT_PROFILE) if settings_obj else DEFAULT_PROFILE
    device = getattr(settings_obj, "local_tts_device", DEFAULT_DEVICE) if settings_obj else DEFAULT_DEVICE
    return {
        "profile": profile,
        "language": DEFAULT_OVERALL_LANGUAGE,
        "voice": DEFAULT_OVERALL_VOICE,
        "speed": DEFAULT_SPEED,
        "device": device,
    }


def resolve_narration_policy(
    request_policy: dict[str, Any] | VoiceProfileSnapshot | ResolvedNarrationPolicy | None = None,
    channel_policy: dict[str, Any] | None = None,
    settings_defaults: dict[str, Any] | None = None,
    request_metadata: dict[str, Any] | None = None,
    actual_provider: str = "LOCAL_TTS",
) -> ResolvedNarrationPolicy:
    """Authoritative, single-pass resolution of narration policy.

    Resolution Precedence (field-wise):
    request explicit field > channel narration metadata > settings defaults

    The resolved policy is strictly validated before returning.
    """
    if isinstance(request_policy, ResolvedNarrationPolicy):
        return request_policy

    req_fields = extract_explicit_request_policy(request_policy, request_metadata)
    chan_fields = extract_channel_narration_policy(channel_policy)
    defaults = settings_defaults or get_settings_narration_defaults()

    # Field-wise merge
    raw_profile = req_fields.get("profile") or chan_fields.get("profile") or defaults.get("profile")
    raw_language = req_fields.get("language") or chan_fields.get("language") or defaults.get("language")
    raw_voice = req_fields.get("voice") or chan_fields.get("voice") or defaults.get("voice")
    raw_speed = req_fields.get("speed") if req_fields.get("speed") is not None else chan_fields.get("speed")
    if raw_speed is None:
        raw_speed = defaults.get("speed")
    raw_device = req_fields.get("device") or chan_fields.get("device") or defaults.get("device")

    # Complete policy validation
    profile = validate_profile(raw_profile)
    canonical_lang, _ = normalize_language(raw_language)
    voice = validate_voice(raw_voice, language=canonical_lang)
    speed = validate_speed(raw_speed)
    device = validate_device(raw_device)

    return ResolvedNarrationPolicy(
        provider=actual_provider,
        profile=profile,
        language=canonical_lang,
        voice=voice,
        speed=speed,
        device=device,
    )
