"""Local TTS capability discovery and model readiness checking."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omega.application.local_tts.kokoro_engine import (
    EXPECTED_MODEL_SHA256,
    EXPECTED_VOICES_SHA256,
)
from omega.application.local_tts.voice_policy import (
    DEFAULT_DEVICE,
    DEFAULT_OVERALL_LANGUAGE,
    DEFAULT_OVERALL_VOICE,
    DEFAULT_PROFILE,
    MAX_SPEED,
    MIN_SPEED,
    SPEED_PRESETS,
    SUPPORTED_PROFILES,
    VOICE_REGISTRY,
)
from omega.application.media_storage import compute_sha256
from omega.config import get_settings

EXPECTED_MODEL_FILE = "kokoro-v1.0.fp16.onnx"
EXPECTED_MODEL_SHA = EXPECTED_MODEL_SHA256

EXPECTED_VOICES_FILE = "voices-v1.0.bin"
EXPECTED_VOICES_SHA = EXPECTED_VOICES_SHA256

_readiness_cache: dict[str, Any] = {}


def check_model_readiness(model_dir: Path | str | None = None) -> str:
    """Check readiness of Kokoro model weights.

    Returns:
    - 'READY': both model file and voices binary exist and match expected SHA-256
    - 'MISSING': one or both files are missing
    - 'CHECKSUM_MISMATCH': files exist but hash does not match pinned artifact

    Uses an in-memory mtime/size cache so ~190MB of weights are not re-hashed
    on every polling request.
    """
    global _readiness_cache

    cfg = get_settings()
    base_dir = Path(model_dir or getattr(cfg, "local_tts_model_dir", getattr(cfg, "kokoro_model_dir", "/app/models/tts/kokoro")))
    model_file = base_dir / EXPECTED_MODEL_FILE
    voices_file = base_dir / EXPECTED_VOICES_FILE

    if not model_file.exists() or not voices_file.exists():
        return "MISSING"

    try:
        m_stat = model_file.stat()
        v_stat = voices_file.stat()
        cache_key = f"{model_file}:{m_stat.st_mtime_ns}:{m_stat.st_size}:{voices_file}:{v_stat.st_mtime_ns}:{v_stat.st_size}"

        if _readiness_cache.get("key") == cache_key:
            return _readiness_cache["status"]

        m_hash = compute_sha256(model_file)
        v_hash = compute_sha256(voices_file)

        if m_hash.lower() != EXPECTED_MODEL_SHA.lower() or v_hash.lower() != EXPECTED_VOICES_SHA.lower():
            status = "CHECKSUM_MISMATCH"
        else:
            status = "READY"

        _readiness_cache = {"key": cache_key, "status": status}
        return status
    except Exception:
        return "MISSING"


def get_available_devices() -> list[str]:
    """Detect available execution provider devices from runtime environment.

    Does NOT advertise CUDA unless ONNX Runtime actually exposes CUDAExecutionProvider.
    """
    devices = ["cpu"]
    try:
        import onnxruntime as ort  # type: ignore

        available = ort.get_available_providers()
        if "CUDAExecutionProvider" in available:
            devices.append("cuda")
    except Exception:
        pass
    devices.append("auto")
    return devices


def get_tts_capability(settings_obj: Any = None) -> dict[str, Any]:
    """Return runtime capability facts for OMEGA Local TTS."""
    cfg = settings_obj or get_settings()
    model_dir = Path(getattr(cfg, "local_tts_model_dir", getattr(cfg, "kokoro_model_dir", "/app/models/tts/kokoro")))
    readiness = check_model_readiness(model_dir)
    available_devices = get_available_devices()
    configured_device = getattr(cfg, "local_tts_device", DEFAULT_DEVICE)
    configured_profile = getattr(cfg, "local_tts_profile", DEFAULT_PROFILE)

    voices_list = []
    for voice_id, meta in VOICE_REGISTRY.items():
        voices_list.append({
            "id": voice_id,
            "name": meta.get("label", voice_id),
            "locale": meta["language"],
            "gender": meta["gender"],
            "description": meta["description"],
            "is_default": meta.get("is_default", False),
        })

    return {
        "provider": "LOCAL_TTS",
        "engine": "kokoro-82m-onnx",
        "model": EXPECTED_MODEL_FILE,
        "readiness": readiness,
        "device": configured_device,
        "available_devices": available_devices,
        "profiles": sorted(list(SUPPORTED_PROFILES)),
        "default_profile": configured_profile,
        "languages": ["en-US", "en-GB"],
        "default_language": DEFAULT_OVERALL_LANGUAGE,
        "default_voice": DEFAULT_OVERALL_VOICE,
        "voices": voices_list,
        "speed_presets": SPEED_PRESETS,
        "speed_range": {"min": MIN_SPEED, "max": MAX_SPEED},
        "canonical_audio": {
            "format": "wav",
            "encoding": "pcm_s16le",
            "sample_rate": 44100,
            "channels": 1,
        },
    }
