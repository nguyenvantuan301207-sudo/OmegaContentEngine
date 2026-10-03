"""Bounded physical P23-A canary using only deterministic synthetic stems."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from omega.application.ffmpeg_renderer import FFmpegRenderer


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60)


def probe(path: Path) -> dict:
    result = run(
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    )
    return json.loads(result.stdout)


def mean_volume(path: Path, start: float, duration: float) -> float:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-ss", str(start), "-t", str(duration),
            "-i", str(path), "-af", "bandpass=f=220:w=80,volumedetect", "-f", "null", "-",
        ],
        check=True, capture_output=True, text=True, timeout=60,
    )
    match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", result.stderr)
    assert match
    return float(match.group(1))


@pytest.mark.asyncio
async def test_p23a_real_audio_mix_and_video_mux_canary(tmp_path: Path):
    narration_video = tmp_path / "narration.mp4"
    music = tmp_path / "music.wav"
    sfx = tmp_path / "sfx.wav"
    mixed = tmp_path / "mixed.mp4"
    final = tmp_path / "final.mp4"

    run(
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:s=320x180:r=24:d=4",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=4",
        "-filter_complex",
        "[1:a]volume='if(between(t,0.5,1.5)+between(t,2.5,3.3),0.8,0)':eval=frame[voice]",
        "-map", "0:v", "-map", "[voice]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-t", "4", str(narration_video),
    )
    run(
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "sine=frequency=220:sample_rate=44100:duration=4", "-ac", "1", str(music),
    )
    run(
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "sine=frequency=1200:sample_rate=32000:duration=0.15", "-ac", "1", str(sfx),
    )

    renderer = FFmpegRenderer()
    await renderer.mix_master_audio(
        video_path=narration_video,
        output_path=mixed,
        target_duration_ms=4_000,
        background_music_path=music,
        background_music_gain_db=-8.0,
        background_music_fade_in_ms=100,
        background_music_fade_out_ms=100,
        sfx_inputs=[
            FFmpegRenderer.SFXMixInput(
                audio_path=sfx, start_ms=2_000, duration_ms=150, gain_db=-10.0
            )
        ],
    )
    await renderer.normalize_master_audio(video_path=mixed, output_path=final)

    assert mixed.stat().st_size > 0
    assert final.stat().st_size > 0
    metadata = probe(final)
    streams = metadata["streams"]
    video_stream = next(stream for stream in streams if stream["codec_type"] == "video")
    audio_stream = next(stream for stream in streams if stream["codec_type"] == "audio")
    assert video_stream["codec_name"] == "h264"
    assert audio_stream["codec_name"] == "aac"
    assert audio_stream["sample_rate"] == "48000"
    assert audio_stream["channels"] == 2
    assert abs(float(metadata["format"]["duration"]) - 4.0) <= 0.08

    # The 220 Hz background recovers measurably in a narration gap.
    active_background = mean_volume(final, 0.8, 0.4)
    gap_background = mean_volume(final, 1.8, 0.4)
    assert gap_background >= active_background + 2.0

    levels = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(final), "-af", "volumedetect", "-f", "null", "-"],
        check=True, capture_output=True, text=True, timeout=60,
    ).stderr
    peak = re.search(r"max_volume:\s*(-?[0-9.]+) dB", levels)
    assert peak and float(peak.group(1)) <= -0.5

    loudness = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-i", str(final), "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=7:print_format=json", "-f", "null", "-",
        ],
        check=True, capture_output=True, text=True, timeout=60,
    ).stderr
    stats_match = re.search(r"\{\s*\"input_i\".*?\}", loudness, re.DOTALL)
    assert stats_match
    stats = json.loads(stats_match.group(0))
    assert abs(float(stats["input_i"]) - (-16.0)) <= 1.0
    assert float(stats["input_tp"]) <= -1.0
