"""Verify 3 sequential real Kokoro syntheses with persistent model session reuse and ffprobe validation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import time
from pathlib import Path

from omega.application.local_tts.kokoro_engine import KokoroLocalTTSEngine


def probe_audio_ffprobe(file_path: Path) -> dict[str, str | int | float]:
    """Inspect audio file properties using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels,duration,bits_per_raw_sample",
        "-of", "json",
        str(file_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(proc.stdout)
    stream = data["streams"][0]
    return {
        "codec_name": stream.get("codec_name", ""),
        "sample_rate": int(stream.get("sample_rate", 0)),
        "channels": int(stream.get("channels", 0)),
        "duration_sec": float(stream.get("duration", 0.0)),
    }


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 checksum."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


async def main() -> None:
    output_dir = Path("/tmp/omega_real_kokoro_verification")
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=== OMEGA P14-A1 REAL KOKORO SYNTHESIS VERIFICATION ===")
    t_init_0 = time.perf_counter()
    engine = KokoroLocalTTSEngine(
        model_dir="/app/models/tts/kokoro",
        device="auto",
        default_profile="quality",
        verify_checksums=True,
    )
    init_duration = time.perf_counter() - t_init_0
    print(f"Engine instantiated in {init_duration:.4f}s (effective device: {engine.effective_device})")
    print(f"Model load count: {engine.load_count}")

    test_sentences = [
        "The distributed microservices architecture handles real-time visual rendering with deterministic pipeline execution.",
        "In 2026, over 15% of high-scale systems generated $1.2 million in annual recurring revenue.",
        "Engineers benchmarked GPU clusters and AI speech synthesis models across multiple geographic regions.",
    ]

    latencies: list[float] = []
    results = []

    for idx, text in enumerate(test_sentences):
        out_path = output_dir / f"synthesis_{idx + 1}.wav"
        t0 = time.perf_counter()
        res = await engine.synthesize(
            text=text,
            output_path=out_path,
            voice="af_heart",
            language="en-US",
            speed=1.0,
            profile="quality",
        )
        duration = time.perf_counter() - t0
        latencies.append(duration)
        results.append(res)
        print(f"Synthesis #{idx + 1}: {duration:.4f}s | Audio duration: {res.duration_ms / 1000.0:.2f}s | SHA: {res.content_hash[:16]}...")

    print("\n--- Model Session Reuse Verification ---")
    print(f"Total syntheses performed: {len(test_sentences)}")
    print(f"Final engine load count:   {engine.load_count}")
    assert engine.load_count == 1, f"Expected load_count == 1, got {engine.load_count}"

    print("\n--- Latency Breakdown ---")
    print(f"First synthesis latency: {latencies[0]:.4f}s")
    print(f"Warm synthesis #1:       {latencies[1]:.4f}s")
    print(f"Warm synthesis #2:       {latencies[2]:.4f}s")

    print("\n--- FFprobe Canonical Audio Inspection ---")
    last_file = results[-1].audio_path
    probe = probe_audio_ffprobe(last_file)
    print(f"File:         {last_file}")
    print(f"Codec:        {probe['codec_name']}")
    print(f"Sample Rate:  {probe['sample_rate']} Hz")
    print(f"Channels:     {probe['channels']}")
    print(f"Duration:     {probe['duration_sec']:.3f} s")
    print(f"SHA-256:      {results[-1].content_hash}")

    # Assertions
    assert probe["codec_name"] == "pcm_s16le", f"Expected pcm_s16le, got {probe['codec_name']}"
    assert probe["sample_rate"] == 44100, f"Expected 44100, got {probe['sample_rate']}"
    assert probe["channels"] == 1, f"Expected 1 (mono), got {probe['channels']}"
    assert probe["duration_sec"] > 0, "Expected duration > 0"
    assert len(results[-1].content_hash) == 64, "Expected valid 64-char SHA-256"

    print("\n[SUCCESS] All real synthesis checks passed with full contract conformity.")


if __name__ == "__main__":
    asyncio.run(main())
