"""Unit tests for authoritative narration policy resolution, legacy placeholder semantics,
canonical field mapping, and runtime capability discovery.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from omega.application.local_tts.capability import (
    check_model_readiness,
    get_available_devices,
    get_tts_capability,
)
from omega.application.local_tts.engine import (
    UnsupportedLanguageError,
    UnsupportedSpeedError,
    VoiceNotFoundError,
)
from omega.application.local_tts.policy import (
    ResolvedNarrationPolicy,
    adapt_voice_profile_snapshot,
    extract_channel_narration_policy,
    extract_explicit_request_policy,
    is_legacy_placeholder,
    resolve_narration_policy,
)
from omega.application.local_tts.voice_policy import (
    DEFAULT_OVERALL_LANGUAGE,
    DEFAULT_OVERALL_VOICE,
    DEFAULT_PROFILE,
    DEFAULT_SPEED,
    validate_voice,
)
from omega.domain.production import VoiceProfileSnapshot

# ==============================================================================
# 1. Authoritative Resolution & Field-Wise Precedence
# ==============================================================================


def test_field_wise_precedence_request_overrides_channel_and_settings():
    """Request explicit voice overrides channel voice, absent language falls through to channel."""
    request_policy = {"voice": "am_michael"}
    channel_policy = {"voice": "af_bella", "language": "en-US", "speed": 1.1}
    settings_defaults = {
        "profile": "quality",
        "language": "en-US",
        "voice": "af_heart",
        "speed": 1.0,
        "device": "auto",
    }

    resolved = resolve_narration_policy(
        request_policy=request_policy,
        channel_policy=channel_policy,
        settings_defaults=settings_defaults,
    )

    assert isinstance(resolved, ResolvedNarrationPolicy)
    # Request voice wins
    assert resolved.voice == "am_michael"
    # Channel language wins (since request omitted it)
    assert resolved.language == "en-US"
    # Channel speed wins (since request omitted it)
    assert resolved.speed == 1.1
    # Settings profile wins (since request and channel omitted it)
    assert resolved.profile == "quality"
    assert resolved.device == "auto"
    assert resolved.provider == "LOCAL_TTS"


def test_channel_overrides_settings_defaults():
    """When request is unspecified, channel preferences override server defaults."""
    channel_policy = {
        "voice": "am_fenrir",
        "language": "en-US",
        "speed": 0.9,
        "profile": "fast",
    }

    resolved = resolve_narration_policy(
        request_policy=None,
        channel_policy=channel_policy,
    )

    assert resolved.voice == "am_fenrir"
    assert resolved.language == "en-US"
    assert resolved.speed == 0.9
    assert resolved.profile == "fast"
    assert resolved.device == "auto"


def test_pure_settings_defaults():
    """When both request and channel are absent, server defaults are applied."""
    resolved = resolve_narration_policy(
        request_policy=None,
        channel_policy=None,
    )

    assert resolved.voice == DEFAULT_OVERALL_VOICE  # af_heart
    assert resolved.language == DEFAULT_OVERALL_LANGUAGE  # en-US
    assert resolved.speed == DEFAULT_SPEED  # 1.0
    assert resolved.profile == DEFAULT_PROFILE  # quality
    assert resolved.device == "auto"


# ==============================================================================
# 2. Legacy Placeholder Semantics & Backward Compatibility
# ==============================================================================


def test_legacy_placeholder_detection():
    """Verify legacy placeholder tuples are detected as unspecified."""
    # Pydantic default instantiation
    snapshot = VoiceProfileSnapshot()
    assert is_legacy_placeholder(snapshot) is True

    # Dict representation in historical DB
    historical_dict = {
        "provider": "PLACEHOLDER",
        "voice_ref": "neutral_default",
        "language": "en",
        "speaking_rate": 1.0,
    }
    assert is_legacy_placeholder(historical_dict) is True

    # Modern explicit voice is NOT a placeholder
    explicit_snap = VoiceProfileSnapshot(voice_ref="am_michael")
    assert is_legacy_placeholder(explicit_snap) is False

    explicit_dict = {"voice": "am_michael", "language": "en-US"}
    assert is_legacy_placeholder(explicit_dict) is False


def test_legacy_placeholder_falls_through_to_channel():
    """Historical request with legacy placeholder falls through to channel preferences."""
    historical_snap = VoiceProfileSnapshot()  # PLACEHOLDER, neutral_default, en, 1.0
    channel_policy = {
        "voice": "bf_emma",
        "language": "en-GB",
        "speed": 1.1,
    }

    resolved = resolve_narration_policy(
        request_policy=historical_snap,
        channel_policy=channel_policy,
    )

    # Must NOT use neutral_default or fallback to af_heart
    assert resolved.voice == "bf_emma"
    assert resolved.language == "en-GB"
    assert resolved.speed == 1.1


def test_modern_explicit_language_en_rejected():
    """Explicit modern requests specifying 'en' without locale must be rejected."""
    # When explicitly provided (not via legacy placeholder fallthrough)
    with pytest.raises(UnsupportedLanguageError, match="Unsupported language 'en'"):
        resolve_narration_policy(
            request_policy={"language": "en", "voice": "af_heart"}
        )


def test_modern_explicit_en_us_and_en_gb_succeed():
    """Modern explicit requests with en-US or en-GB succeed."""
    res_us = resolve_narration_policy(request_policy={"language": "en-US", "voice": "am_michael"})
    assert res_us.language == "en-US"
    assert res_us.voice == "am_michael"

    res_gb = resolve_narration_policy(request_policy={"language": "en-GB", "voice": "bm_george"})
    assert res_gb.language == "en-GB"
    assert res_gb.voice == "bm_george"


# ==============================================================================
# 3. Canonical Legacy Field Mapping
# ==============================================================================


def test_canonical_legacy_field_mapping_voice_profile_snapshot():
    """VoiceProfileSnapshot canonical mapping: voice_ref -> voice, speaking_rate -> speed."""
    snapshot = VoiceProfileSnapshot(
        voice_ref="am_michael",
        speaking_rate=1.1,
        language="en-US",
    )
    explicit = extract_explicit_request_policy(snapshot)
    adapted = adapt_voice_profile_snapshot(snapshot)

    assert explicit["voice"] == "am_michael"
    assert explicit["speed"] == 1.1
    assert explicit["language"] == "en-US"
    assert adapted == explicit

    # Channel policy extraction
    chan_policy = extract_channel_narration_policy({"narration": {"voice": "bm_george", "speed": 1.0}})
    assert chan_policy["voice"] == "bm_george"
    assert chan_policy["speed"] == 1.0


def test_canonical_structured_metadata_for_profile_and_device():
    """Fields not in VoiceProfileSnapshot (profile, device) are drawn from request metadata."""
    snapshot = VoiceProfileSnapshot(voice_ref="am_michael")
    req_meta = {
        "narration": {
            "profile": "fast",
            "device": "cpu",
        }
    }

    resolved = resolve_narration_policy(
        request_policy=snapshot,
        request_metadata=req_meta,
    )

    assert resolved.voice == "am_michael"
    assert resolved.profile == "fast"
    assert resolved.device == "cpu"


# ==============================================================================
# 4. Strict Language-Voice Compatibility & Validation
# ==============================================================================


def test_language_voice_compatibility_success():
    """Compatible voice and language combinations pass validation."""
    # US voices with en-US
    for v in ["af_heart", "af_bella", "am_michael", "am_fenrir", "am_puck"]:
        assert validate_voice(v, language="en-US") == v

    # UK voices with en-GB
    for v in ["bf_emma", "bm_george"]:
        assert validate_voice(v, language="en-GB") == v


def test_language_voice_incompatibility_raises_error():
    """Incompatible voice and language combinations are rejected strictly."""
    # UK voice with en-US
    with pytest.raises(VoiceNotFoundError, match="incompatible with language 'en-US'"):
        validate_voice("bf_emma", language="en-US")

    # US voice with en-GB
    with pytest.raises(VoiceNotFoundError, match="incompatible with language 'en-GB'"):
        validate_voice("am_michael", language="en-GB")


def test_speed_validation_bounds():
    """Speed outside [0.8, 1.2] is rejected explicitly without clamping."""
    with pytest.raises(UnsupportedSpeedError):
        resolve_narration_policy(request_policy={"speed": 0.7, "voice": "af_heart"})

    with pytest.raises(UnsupportedSpeedError):
        resolve_narration_policy(request_policy={"speed": 1.3, "voice": "af_heart"})


# ==============================================================================
# 5. Capability Discovery & Model Readiness
# ==============================================================================


def test_check_model_readiness_missing(tmp_path: Path):
    """Missing model files return 'MISSING'."""
    status = check_model_readiness(tmp_path)
    assert status == "MISSING"


def test_check_model_readiness_checksum_mismatch(tmp_path: Path):
    """Corrupted or wrong model files return 'CHECKSUM_MISMATCH'."""
    (tmp_path / "kokoro-v1.0.fp16.onnx").write_bytes(b"corrupted_model")
    (tmp_path / "voices-v1.0.bin").write_bytes(b"corrupted_voices")

    status = check_model_readiness(tmp_path)
    assert status == "CHECKSUM_MISMATCH"


def test_get_available_devices_no_cuda():
    """Does NOT advertise CUDA if CUDAExecutionProvider is absent."""
    with patch("onnxruntime.get_available_providers", return_value=["CPUExecutionProvider"]):
        devices = get_available_devices()
        assert "cuda" not in devices
        assert "cpu" in devices
        assert "auto" in devices


def test_get_tts_capability_structure():
    """Capability endpoint returns complete truthful structure."""
    cap = get_tts_capability()
    assert cap["provider"] == "LOCAL_TTS"
    assert cap["engine"] == "kokoro-82m-onnx"
    assert cap["canonical_audio"]["format"] == "wav"
    assert cap["canonical_audio"]["sample_rate"] == 44100
    assert cap["canonical_audio"]["channels"] == 1
    assert len(cap["voices"]) >= 7
