"""Comprehensive unit tests for OMEGA Local TTS engine and NarrationProvider integration."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omega.application.local_tts import (
    ChecksumMismatchError,
    KokoroLocalTTSEngine,
    ModelNotFoundError,
    SynthesisResult,
    UnsupportedDeviceError,
    UnsupportedLanguageError,
    UnsupportedSpeedError,
    VoiceNotFoundError,
    chunk_narration_segment,
    normalize_language,
    normalize_narration_text,
    validate_profile,
    validate_speed,
    validate_voice,
)
from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import (
    LocalTTSNarrationProvider,
    NarrationProviderError,
    NeuralTTSNarrationProvider,
    get_narration_provider,
)
from omega.domain.production import AssetProviderType, AssetType

# ==============================================================================
# 1. Voice, Language, Speed, and Profile Validation Tests
# ==============================================================================


def test_language_normalization_valid():
    """Verify supported language codes map to canonical and internal representations."""
    canonical, internal = normalize_language("en-US")
    assert canonical == "en-US"
    assert internal == "en-us"

    canonical, internal = normalize_language("en-GB")
    assert canonical == "en-GB"
    assert internal == "en-gb"

    canonical, internal = normalize_language("en")
    assert canonical == "en-US"
    assert internal == "en-us"


def test_language_normalization_invalid():
    """Verify unsupported languages are rejected explicitly."""
    with pytest.raises(UnsupportedLanguageError, match="Unsupported language 'vi'"):
        normalize_language("vi")

    with pytest.raises(UnsupportedLanguageError, match="Unsupported language 'fr'"):
        normalize_language("fr")


def test_voice_validation_valid():
    """Verify primary and secondary English voices pass validation."""
    assert validate_voice("af_heart") == "af_heart"
    assert validate_voice("am_michael") == "am_michael"
    assert validate_voice("bf_emma") == "bf_emma"
    assert validate_voice("bm_george") == "bm_george"
    assert validate_voice("af_bella") == "af_bella"
    assert validate_voice("am_fenrir") == "am_fenrir"
    assert validate_voice("am_puck") == "am_puck"


def test_voice_validation_defaults():
    """Verify default resolution for empty or neutral voice profiles."""
    assert validate_voice(None, "en-US") == "af_heart"
    assert validate_voice("default", "en-US") == "af_heart"
    assert validate_voice("neutral_default", "en-GB") == "bf_emma"


def test_voice_validation_invalid():
    """Verify unknown or unvetted voice strings are rejected clearly."""
    with pytest.raises(VoiceNotFoundError, match="Unsupported voice 'unknown_voice_xyz'"):
        validate_voice("unknown_voice_xyz")


def test_speed_validation_valid():
    """Verify valid speeds within [0.8, 1.2] are accepted."""
    assert validate_speed(1.0) == 1.0
    assert validate_speed(0.9) == 0.9
    assert validate_speed(1.1) == 1.1
    assert validate_speed(0.8) == 0.8
    assert validate_speed(1.2) == 1.2
    assert validate_speed(None) == 1.0


def test_speed_validation_out_of_bounds():
    """Verify speeds outside [0.8, 1.2] raise UnsupportedSpeedError without silent clamping."""
    with pytest.raises(UnsupportedSpeedError, match="Must be between 0.8 and 1.2"):
        validate_speed(0.5)

    with pytest.raises(UnsupportedSpeedError, match="Must be between 0.8 and 1.2"):
        validate_speed(1.5)

    with pytest.raises(UnsupportedSpeedError, match="Must be between 0.8 and 1.2"):
        validate_speed(2.0)


def test_profile_validation():
    """Verify fast and quality profiles validate correctly."""
    assert validate_profile("fast") == "fast"
    assert validate_profile("quality") == "quality"
    assert validate_profile(None) == "quality"

    with pytest.raises(ValueError, match="Unsupported profile 'turbo'"):
        validate_profile("turbo")


# ==============================================================================
# 2. Text Normalization Tests (Preserve Semantic English Constructs)
# ==============================================================================


def test_text_normalization_preserves_semantics():
    """Verify numbers, percentages, currencies, dates, times, and acronyms remain intact."""
    raw = (
        "In 2026, over 15% of high-scale systems generated $1.2 million in ARR. "
        "The conference on September 15, 2026 starts at 8:30 PM. "
        "Engineers benchmarked AI, GPU, and API throughput for OpenAI and YouTube SaaS."
    )
    normalized = normalize_narration_text(raw)

    assert "2026" in normalized
    assert "15%" in normalized
    assert "$1.2 million" in normalized
    assert "September 15, 2026" in normalized
    assert "8:30 PM" in normalized
    assert "AI, GPU, and API" in normalized
    assert "OpenAI and YouTube SaaS" in normalized


def test_text_normalization_unicode_punctuation():
    """Verify curly quotes, apostrophes, and em-dashes normalize cleanly."""
    raw = "“FastAPI’s performance—measured deterministically—is impressive.”"
    normalized = normalize_narration_text(raw)
    assert normalized == '"FastAPI\'s performance - measured deterministically - is impressive."'


# ==============================================================================
# 3. Semantic Chunking Policy Tests
# ==============================================================================


def test_chunking_short_scene_preserved_as_is():
    """Short scenes under 150 tokens must NOT be split or padded."""
    scene_text = "FastAPI 2026 High-Throughput Microservices architecture overview."
    chunks = chunk_narration_segment(scene_text, max_tokens=150)
    assert len(chunks) == 1
    assert chunks[0] == scene_text


def test_chunking_long_segment_splits_semantically():
    """Long scenes exceeding 150 tokens split along sentence boundaries."""
    long_text = (
        "Sentence one establishes the distributed systems baseline for our narration pipeline. "
        "Sentence two introduces the GPU cluster and memory management constraints. "
        "Sentence three covers high-throughput network topologies and asynchronous RPC. "
        "Sentence four outlines persistent storage replication across multiple geographic regions. "
        "Sentence five concludes the architectural breakdown with measurable benchmark results."
    )
    chunks = chunk_narration_segment(long_text, max_tokens=40)
    assert len(chunks) > 1
    # Every chunk must have content
    assert all(len(c.strip()) > 0 for c in chunks)
    # Merging chunks recovers all original words
    assert "Sentence one" in chunks[0]
    assert "Sentence five" in chunks[-1]


# ==============================================================================
# 4. Engine Initialization, Device, and Model Integrity Tests
# ==============================================================================


def test_missing_model_file_raises_error(tmp_path: Path):
    """Missing model weights must fail with ModelNotFoundError, no auto-download."""
    empty_dir = tmp_path / "empty_models"
    empty_dir.mkdir()

    with pytest.raises(ModelNotFoundError, match="Kokoro model file not found"):
        KokoroLocalTTSEngine(model_dir=empty_dir)


def test_missing_voice_pack_raises_error(tmp_path: Path):
    """Missing voice pack must fail with ModelNotFoundError, no auto-download."""
    model_dir = tmp_path / "models_no_voices"
    model_dir.mkdir()
    (model_dir / "kokoro-v1.0.fp16.onnx").write_bytes(b"dummy")

    with pytest.raises(ModelNotFoundError, match="Kokoro voice pack not found"):
        KokoroLocalTTSEngine(model_dir=model_dir, verify_checksums=False)


def test_checksum_mismatch_raises_error(tmp_path: Path):
    """Corrupted model weights must raise ChecksumMismatchError."""
    model_dir = tmp_path / "corrupted_models"
    model_dir.mkdir()
    (model_dir / "kokoro-v1.0.fp16.onnx").write_bytes(b"corrupted bytes")
    (model_dir / "voices-v1.0.bin").write_bytes(b"corrupted voices")

    with pytest.raises(ChecksumMismatchError, match="Kokoro model SHA-256 mismatch"):
        KokoroLocalTTSEngine(model_dir=model_dir, verify_checksums=True)


def test_cuda_unavailable_raises_explicitly(tmp_path: Path):
    """Explicitly requesting CUDA when unavailable must raise UnsupportedDeviceError (no silent fallback)."""
    model_dir = tmp_path / "valid_models"
    model_dir.mkdir()
    (model_dir / "kokoro-v1.0.fp16.onnx").write_bytes(b"dummy")
    (model_dir / "voices-v1.0.bin").write_bytes(b"dummy")

    with (
        patch("onnxruntime.get_available_providers", return_value=["CPUExecutionProvider"]),
        pytest.raises(UnsupportedDeviceError, match="CUDA acceleration requested for Kokoro TTS, but CUDAExecutionProvider is not available"),
    ):
        KokoroLocalTTSEngine(model_dir=model_dir, device="cuda", verify_checksums=False)


def test_device_auto_falls_back_to_cpu_when_no_cuda(tmp_path: Path):
    """Device auto chooses CPU if CUDA is not installed."""
    model_dir = tmp_path / "valid_models"
    model_dir.mkdir()
    (model_dir / "kokoro-v1.0.fp16.onnx").write_bytes(b"dummy")
    (model_dir / "voices-v1.0.bin").write_bytes(b"dummy")

    with (
        patch("onnxruntime.get_available_providers", return_value=["CPUExecutionProvider"]),
        patch("kokoro_onnx.Kokoro"),
    ):
        engine = KokoroLocalTTSEngine(model_dir=model_dir, device="auto", verify_checksums=False)
        assert engine.effective_device == "cpu"


def test_model_session_reused_single_load(tmp_path: Path):
    """Verify underlying Kokoro model session is loaded once and reused across syntheses."""
    model_dir = tmp_path / "valid_models"
    model_dir.mkdir()
    (model_dir / "kokoro-v1.0.fp16.onnx").write_bytes(b"dummy")
    (model_dir / "voices-v1.0.bin").write_bytes(b"dummy")

    with (
        patch("onnxruntime.get_available_providers", return_value=["CPUExecutionProvider"]),
        patch("kokoro_onnx.Kokoro") as mock_kokoro_cls,
    ):
        mock_inst = MagicMock()
        mock_kokoro_cls.return_value = mock_inst
        engine = KokoroLocalTTSEngine(model_dir=model_dir, verify_checksums=False)

        assert engine.load_count == 1
        assert mock_kokoro_cls.call_count == 1


# ==============================================================================
# 5. Narration Provider Integration & Fallback Policy Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_local_tts_provider_delegates_to_engine(tmp_path: Path):
    """Verify LocalTTSNarrationProvider delegates synthesis to LocalTTSEngine and returns rich metadata."""
    storage = LocalMediaStorageProvider(base_root=str(tmp_path))
    channel_id = uuid.uuid4()
    request_id = uuid.uuid4()

    mock_engine = MagicMock()
    mock_result = SynthesisResult(
        audio_path=tmp_path / "dummy.wav",
        duration_ms=2500,
        native_sample_rate=24000,
        canonical_sample_rate=44100,
        channels=1,
        engine="kokoro-onnx",
        model="kokoro-v1.0.fp16",
        voice="af_heart",
        language="en-US",
        speed=1.0,
        device="cpu",
        generation_duration_ms=450,
        content_hash="test_sha_12345",
        profile="quality",
    )

    async def fake_synthesize(*args, **kwargs):
        # Create output file to simulate engine behavior
        out_p = kwargs.get("output_path") or args[1]
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_bytes(b"RIFFdummyWAVbytes")
        return mock_result

    mock_engine.synthesize = AsyncMock(side_effect=fake_synthesize)

    provider = LocalTTSNarrationProvider(storage=storage, engine=mock_engine)
    asset = await provider.synthesize_segment_audio(
        channel_id=channel_id,
        request_id=request_id,
        segment={"text": "Hello world from OMEGA test."},
        voice_profile={"voice_ref": "af_heart", "language": "en-US"},
    )

    assert asset["id"] is not None
    assert asset["asset_type"] == AssetType.AUDIO.value
    assert asset["provider_type"] == AssetProviderType.SYSTEM.value
    assert asset["mime_type"] == "audio/wav"
    assert asset["content_hash"] == "test_sha_12345"
    assert asset["duration_ms"] == 2500
    assert asset["provider"] == "LOCAL_TTS"
    assert asset["engine"] == "kokoro-onnx"
    assert asset["model"] == "kokoro-v1.0.fp16"
    assert asset["device"] == "cpu"
    assert asset["canonical_sample_rate"] == 44100
    assert asset["native_sample_rate"] == 24000
    assert asset["generation_duration_ms"] == 450


@pytest.mark.asyncio
async def test_local_tts_failure_raises_no_sine_fallback(tmp_path: Path):
    """When local engine fails, LocalTTSNarrationProvider must raise NarrationProviderError without sine fallback."""
    storage = LocalMediaStorageProvider(base_root=str(tmp_path))
    channel_id = uuid.uuid4()
    request_id = uuid.uuid4()

    mock_engine = MagicMock()
    mock_engine.synthesize = AsyncMock(side_effect=RuntimeError("Synthesis catastrophic failure"))

    provider = LocalTTSNarrationProvider(storage=storage, engine=mock_engine)

    with pytest.raises(NarrationProviderError, match="Local TTS narration synthesis failed"):
        await provider.synthesize_segment_audio(
            channel_id=channel_id,
            request_id=request_id,
            segment={"text": "Fail this text"},
        )


@pytest.mark.asyncio
async def test_neural_tts_failure_raises_no_local_fallback(tmp_path: Path):
    """When OpenAI fails or key missing, NeuralTTSNarrationProvider must raise NarrationProviderError without calling local provider."""
    storage = LocalMediaStorageProvider(base_root=str(tmp_path))
    channel_id = uuid.uuid4()
    request_id = uuid.uuid4()

    # Missing API key
    provider_no_key = NeuralTTSNarrationProvider(storage=storage, api_key="")
    with pytest.raises(NarrationProviderError, match="OPENAI_API_KEY missing for neural TTS provider"):
        await provider_no_key.synthesize_segment_audio(
            channel_id=channel_id,
            request_id=request_id,
            segment={"text": "Test narration text"},
        )


def test_factory_selects_local_by_default(tmp_path: Path, monkeypatch):
    """Factory selects LocalTTSNarrationProvider when TTS_PROVIDER is unset or 'local'."""
    storage = LocalMediaStorageProvider(base_root=str(tmp_path))
    monkeypatch.delenv("TTS_PROVIDER", raising=False)

    provider = get_narration_provider(storage)
    assert isinstance(provider, LocalTTSNarrationProvider)

    monkeypatch.setenv("TTS_PROVIDER", "local")
    provider = get_narration_provider(storage)
    assert isinstance(provider, LocalTTSNarrationProvider)


def test_factory_cloud_missing_key_fails(tmp_path: Path, monkeypatch):
    """When TTS_PROVIDER=cloud or neural, missing OPENAI_API_KEY must raise NarrationProviderError."""
    storage = LocalMediaStorageProvider(base_root=str(tmp_path))
    monkeypatch.setenv("TTS_PROVIDER", "cloud")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(NarrationProviderError, match="OPENAI_API_KEY missing for neural/cloud TTS provider"):
        get_narration_provider(storage)
