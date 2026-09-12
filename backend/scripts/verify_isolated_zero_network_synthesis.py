"""Verify post-recreate synthesis with network guards and ffprobe inspection."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from omega.application.local_tts.kokoro_engine import KokoroLocalTTSEngine


def probe_audio_ffprobe(file_path: Path) -> dict[str, str | int | float]:
    """Inspect audio file properties using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,sample_rate,channels,duration",
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


async def main() -> None:
    output_dir = Path("/tmp/omega_post_recreate_verification")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / "post_recreate_synthesis.wav"

    print("=== OMEGA P14-A1.1 POST-RECREATE ZERO-NETWORK SYNTHESIS ===")

    # Network access interception
    network_attempts = 0

    original_urlopen = urllib.request.urlopen
    original_connect = socket.socket.connect

    def blocked_urlopen(*args, **kwargs):
        nonlocal network_attempts
        network_attempts += 1
        raise RuntimeError("UNEXPECTED NETWORK CALL DETECTED via urllib.request.urlopen")

    def blocked_connect(*args, **kwargs):
        nonlocal network_attempts
        network_attempts += 1
        raise RuntimeError("UNEXPECTED NETWORK CALL DETECTED via socket.connect")

    urllib.request.urlopen = blocked_urlopen
    socket.socket.connect = blocked_connect

    try:
        t0 = time.perf_counter()
        engine = KokoroLocalTTSEngine(
            model_dir="/app/models/tts/kokoro",
            device="auto",
            default_profile="quality",
            verify_checksums=True,
        )
        init_duration = time.perf_counter() - t0
        print(f"Engine instantiated in {init_duration:.4f}s (load_count: {engine.load_count})")

        t_synth = time.perf_counter()
        res = await engine.synthesize(
            text="Autonomous content production pipeline verification after container recreation.",
            output_path=out_file,
            voice="af_heart",
            language="en-US",
            speed=1.0,
            profile="quality",
        )
        synth_duration = time.perf_counter() - t_synth
        print(f"Synthesis completed in {synth_duration:.4f}s")
        print(f"Audio duration: {res.duration_ms / 1000.0:.3f}s")
        print(f"Content SHA-256: {res.content_hash}")

        # ffprobe inspection
        probe = probe_audio_ffprobe(out_file)
        print("\n--- FFprobe Inspection ---")
        print(f"Codec:        {probe['codec_name']}")
        print(f"Sample Rate:  {probe['sample_rate']} Hz")
        print(f"Channels:     {probe['channels']}")
        print(f"Duration:     {probe['duration_sec']:.3f} s")

        assert probe["codec_name"] == "pcm_s16le", f"Expected pcm_s16le, got {probe['codec_name']}"
        assert probe["sample_rate"] == 44100, f"Expected 44100, got {probe['sample_rate']}"
        assert probe["channels"] == 1, f"Expected 1, got {probe['channels']}"
        assert probe["duration_sec"] > 0, "Expected duration > 0"
        assert len(res.content_hash) == 64, "Expected valid SHA-256"

        print(f"\nRuntime network requests attempted: {network_attempts}")
        assert network_attempts == 0, f"Expected 0 network attempts, got {network_attempts}"
        print("[SUCCESS] Zero-network post-recreation synthesis verified.")

    finally:
        urllib.request.urlopen = original_urlopen
        socket.socket.connect = original_connect


if __name__ == "__main__":
    asyncio.run(main())
