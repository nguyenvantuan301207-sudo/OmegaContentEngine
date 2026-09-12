"""Production Kokoro-82M ONNX local TTS engine implementation."""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as rt

from omega.application.local_tts.engine import (
    ChecksumMismatchError,
    ModelNotFoundError,
    SynthesisFailedError,
    SynthesisResult,
    UnsupportedDeviceError,
)
from omega.application.local_tts.text_normalizer import normalize_narration_text
from omega.application.local_tts.voice_policy import (
    DEFAULT_OVERALL_LANGUAGE,
    DEFAULT_OVERALL_VOICE,
    DEFAULT_PROFILE,
    DEFAULT_SPEED,
    normalize_language,
    validate_profile,
    validate_speed,
    validate_voice,
)

# Authoritative model checksums (release model-files-v1.1)
EXPECTED_MODEL_SHA256 = "f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96"
EXPECTED_VOICES_SHA256 = "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"
EXPECTED_MODEL_SIZE = 163527961
EXPECTED_VOICES_SIZE = 28214398


def compute_file_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file in 64KB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class KokoroLocalTTSEngine:
    """Kokoro-82M ONNX local speech synthesis engine with persistent session reuse."""

    def __init__(
        self,
        model_dir: Path | str | None = None,
        default_voice: str = DEFAULT_OVERALL_VOICE,
        default_language: str = DEFAULT_OVERALL_LANGUAGE,
        default_speed: float = DEFAULT_SPEED,
        default_profile: str = DEFAULT_PROFILE,
        device: str = "auto",
        verify_checksums: bool = True,
    ) -> None:
        raw_dir = model_dir or os.getenv("LOCAL_TTS_MODEL_DIR", "/app/models/tts/kokoro")
        self.model_dir = Path(raw_dir)
        self.model_path = self.model_dir / "kokoro-v1.0.fp16.onnx"
        self.voices_path = self.model_dir / "voices-v1.0.bin"

        self.default_voice = default_voice
        self.default_language = default_language
        self.default_speed = default_speed
        self.default_profile = validate_profile(default_profile)
        self.device = (device or os.getenv("LOCAL_TTS_DEVICE", "auto")).lower()
        self.verify_checksums = verify_checksums

        # Device selection policy (CPU-first / auto / cuda)
        available_providers = rt.get_available_providers()
        has_cuda = "CUDAExecutionProvider" in available_providers

        if self.device == "cuda":
            if not has_cuda:
                raise UnsupportedDeviceError(
                    "CUDA acceleration requested for Kokoro TTS, but "
                    f"CUDAExecutionProvider is not available. Available providers: {available_providers}"
                )
            self.effective_device = "cuda"
            os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"
        elif self.device == "cpu":
            self.effective_device = "cpu"
            os.environ["ONNX_PROVIDER"] = "CPUExecutionProvider"
        elif self.device == "auto":
            if has_cuda:
                self.effective_device = "cuda"
                os.environ["ONNX_PROVIDER"] = "CUDAExecutionProvider"
            else:
                self.effective_device = "cpu"
                os.environ["ONNX_PROVIDER"] = "CPUExecutionProvider"
        else:
            raise UnsupportedDeviceError(
                f"Unknown device '{self.device}'. Supported devices: 'auto', 'cpu', 'cuda'"
            )

        # Ensure model files exist on filesystem (NO runtime network calls)
        self._validate_model_files()

        # Model session initialization counter & instance
        self._load_count = 0
        self._kokoro: Any = None
        self._init_kokoro_session()

    @property
    def load_count(self) -> int:
        """Number of times the underlying Kokoro ONNX model session was initialized."""
        return self._load_count

    def _validate_model_files(self) -> None:
        """Validate presence and integrity of local model and voice pack."""
        if not self.model_path.exists():
            raise ModelNotFoundError(
                f"Kokoro model file not found at: {self.model_path}. "
                "Models must be provisioned prior to runtime synthesis via download_kokoro_models.py."
            )
        if not self.voices_path.exists():
            raise ModelNotFoundError(
                f"Kokoro voice pack not found at: {self.voices_path}. "
                "Voice pack must be provisioned prior to runtime synthesis via download_kokoro_models.py."
            )

        if self.verify_checksums:
            # Verify model weights
            actual_model_sha = compute_file_sha256(self.model_path)
            if actual_model_sha.lower() != EXPECTED_MODEL_SHA256.lower():
                raise ChecksumMismatchError(
                    f"Kokoro model SHA-256 mismatch at {self.model_path}. "
                    f"Expected: {EXPECTED_MODEL_SHA256}, Actual: {actual_model_sha}"
                )

            # Verify voice pack
            actual_voices_sha = compute_file_sha256(self.voices_path)
            if actual_voices_sha.lower() != EXPECTED_VOICES_SHA256.lower():
                raise ChecksumMismatchError(
                    f"Kokoro voice pack SHA-256 mismatch at {self.voices_path}. "
                    f"Expected: {EXPECTED_VOICES_SHA256}, Actual: {actual_voices_sha}"
                )

    def _init_kokoro_session(self) -> None:
        """Initialize Kokoro ONNX session once and reuse across synthesis requests."""
        from kokoro_onnx import Kokoro

        self._kokoro = Kokoro(str(self.model_path), str(self.voices_path))
        self._load_count += 1

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
        """Synthesize text asynchronously via worker thread to avoid event loop blocking."""
        # Non-blocking async execution using asyncio.to_thread
        return await asyncio.to_thread(
            self._sync_synthesize,
            text=text,
            output_path=output_path,
            voice=voice,
            language=language,
            speed=speed,
            profile=profile,
        )

    def _sync_synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        voice: str | None = None,
        language: str | None = None,
        speed: float | None = None,
        profile: str | None = None,
    ) -> SynthesisResult:
        """Synchronous CPU/GPU synthesis and canonical audio conversion."""
        # Policy validations
        canonical_lang, kokoro_lang = normalize_language(language or self.default_language)
        validated_voice = validate_voice(voice or self.default_voice, canonical_lang)
        validated_speed = validate_speed(speed if speed is not None else self.default_speed)
        effective_profile = validate_profile(profile or self.default_profile)

        # Minimal, non-destructive text normalization
        clean_text = normalize_narration_text(text)
        if not clean_text:
            raise SynthesisFailedError("Cannot synthesize empty narration text")

        # Destination path setup
        output_path.parent.mkdir(parents=True, exist_ok=True)

        t_start = time.perf_counter()
        try:
            # Reusing the existing model session
            samples, native_sr = self._kokoro.create(
                clean_text,
                voice=validated_voice,
                speed=validated_speed,
                lang=kokoro_lang,
            )
        except Exception as e:
            raise SynthesisFailedError(f"Kokoro ONNX inference failed: {e}") from e

        t_inference = time.perf_counter() - t_start

        if samples is None or len(samples) == 0:
            raise SynthesisFailedError("Kokoro inference returned empty audio samples")

        # Step 1: Write native 24,000 Hz 16-bit mono WAV to temp file
        with tempfile.NamedTemporaryFile(suffix="_native.wav", delete=False) as tmp_native:
            tmp_native_path = Path(tmp_native.name)

        try:
            int16_samples = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
            with wave.open(str(tmp_native_path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(native_sr)
                w.writeframes(int16_samples.tobytes())

            # Step 2: Convert to canonical OMEGA audio contract via FFmpeg
            # Contract: WAV, pcm_s16le, 44100 Hz, mono
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(tmp_native_path),
                "-ar",
                "44100",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
                err_msg = proc.stderr.decode("utf-8", errors="replace")
                raise SynthesisFailedError(f"FFmpeg audio normalization failed: {err_msg}")

            # Step 3: Validate canonical WAV properties and derive duration
            with wave.open(str(output_path), "rb") as cw:
                canonical_sr = cw.getframerate()
                channels = cw.getnchannels()
                frames = cw.getnframes()
                sampwidth = cw.getsampwidth()

            if canonical_sr != 44100 or channels != 1 or sampwidth != 2:
                raise SynthesisFailedError(
                    f"Canonical WAV violates contract: rate={canonical_sr}, channels={channels}, width={sampwidth}"
                )

            duration_ms = int(frames / canonical_sr * 1000)
            content_sha256 = compute_file_sha256(output_path)
            total_duration_ms = int(t_inference * 1000)

            return SynthesisResult(
                audio_path=output_path,
                duration_ms=duration_ms,
                native_sample_rate=native_sr,
                canonical_sample_rate=canonical_sr,
                channels=channels,
                engine="kokoro-onnx",
                model="kokoro-v1.0.fp16",
                voice=validated_voice,
                language=canonical_lang,
                speed=validated_speed,
                device=self.effective_device,
                generation_duration_ms=total_duration_ms,
                content_hash=content_sha256,
                profile=effective_profile,
            )
        finally:
            if tmp_native_path.exists():
                tmp_native_path.unlink()
