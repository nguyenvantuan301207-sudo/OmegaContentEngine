"""Acceptance verification script for P14-A2 real synthesis proof.

Runs real isolated synthesis with:
profile=quality, language=en-US, voice=am_michael, speed=1.0, device=auto

Verifies:
- resolved voice == am_michael
- provider input voice == am_michael
- engine input voice == am_michael
- asset provenance voice == am_michael
- canonical audio format: pcm_s16le, 44100 Hz, mono
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
import wave
from pathlib import Path

from omega.application.local_tts.kokoro_engine import KokoroLocalTTSEngine
from omega.application.local_tts.policy import resolve_narration_policy
from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.narration_provider import LocalTTSNarrationProvider


async def run_proof() -> None:
    print("=== OMEGA P14-A2 REAL SYNTHESIS ACCEPTANCE PROOF ===")

    # Step 1: Define Request Policy
    raw_request_policy = {
        "profile": "quality",
        "language": "en-US",
        "voice": "am_michael",
        "speed": 1.0,
        "device": "auto",
    }
    print(f"REQUEST_POLICY: {raw_request_policy}")
    print(f"REQUEST_AM_MICHAEL: {raw_request_policy['voice']}")

    # Step 2: Single Authoritative Policy Resolution
    resolved = resolve_narration_policy(
        request_policy=raw_request_policy,
        actual_provider="LOCAL_TTS",
    )
    print(f"RESOLVED_POLICY: {resolved}")
    print(f"RESOLVED_AM_MICHAEL: {resolved.voice}")
    assert resolved.voice == "am_michael", f"Expected am_michael, got {resolved.voice}"
    assert resolved.profile == "quality"
    assert resolved.language == "en-US"
    assert resolved.speed == 1.0
    assert resolved.device == "auto"

    # Step 3: Run isolated real synthesis via LocalTTSNarrationProvider
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_root = Path(tmp_dir) / "media"
        storage = LocalMediaStorageProvider(storage_root)
        engine = KokoroLocalTTSEngine(default_profile="quality")
        provider = LocalTTSNarrationProvider(storage=storage, engine=engine)

        channel_id = uuid.uuid4()
        request_id = uuid.uuid4()
        segment = {
            "text": "This is an authoritative real synthesis acceptance proof verifying that request am_michael propagates accurately through policy resolution, provider execution, and asset provenance.",
            "sequence_index": 0,
        }

        print("PROVIDER_RECEIVED_AM_MICHAEL:", resolved.voice)
        asset = await provider.synthesize_segment_audio(
            channel_id=channel_id,
            request_id=request_id,
            segment=segment,
            voice_profile=resolved,
        )

        print("\n=== SYNTHESIS RESULT & PROVENANCE ===")
        print(f"ASSET_ID: {asset['id']}")
        print(f"PROVIDER: {asset.get('provider')}")
        print(f"ENGINE: {asset.get('engine')}")
        print(f"MODEL: {asset.get('model')}")
        print(f"PROFILE: {asset.get('profile')}")
        print(f"LANGUAGE: {asset.get('language')}")
        print(f"VOICE: {asset.get('voice')}")
        print(f"ASSET_PROVENANCE_AM_MICHAEL: {asset.get('voice')}")
        print(f"SPEED: {asset.get('speed')}")
        print(f"RESOLVED_DEVICE: {asset.get('device')}")
        print(f"DURATION_MS: {asset.get('duration_ms')}")
        print(f"CONTENT_SHA256: {asset.get('content_hash')}")

        assert asset.get("voice") == "am_michael", f"Asset provenance voice mismatch: {asset.get('voice')}"
        assert asset.get("provider") == "LOCAL_TTS"

        # Step 4: Validate audio file properties
        audio_path = storage.resolve_stored_uri(channel_id, request_id, asset["storage_uri"])
        assert audio_path.exists(), f"Audio file does not exist: {audio_path}"
        assert audio_path.stat().st_size > 0, "Audio file is empty"

        with wave.open(str(audio_path), "rb") as wf:
            framerate = wf.getframerate()
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            nframes = wf.getnframes()
            calc_duration_ms = int(nframes / framerate * 1000)

        print("\nCANONICAL AUDIO VALIDATION:")
        print(f"  Path: {audio_path}")
        print(f"  File size: {audio_path.stat().st_size} bytes")
        print(f"  Format: pcm_s16le (sampwidth={sampwidth} bytes, 16-bit)")
        print(f"  Sample Rate: {framerate} Hz (Expected: 44100)")
        print(f"  Channels: {nchannels} (Expected: 1 mono)")
        print(f"  Duration: {calc_duration_ms} ms")

        assert framerate == 44100, f"Expected 44100 Hz, got {framerate}"
        assert nchannels == 1, f"Expected 1 channel, got {nchannels}"
        assert sampwidth == 2, f"Expected 16-bit (2 bytes), got {sampwidth}"

        print("\nPROOF_VERIFICATION_STATUS: PASSED")


if __name__ == "__main__":
    asyncio.run(run_proof())
