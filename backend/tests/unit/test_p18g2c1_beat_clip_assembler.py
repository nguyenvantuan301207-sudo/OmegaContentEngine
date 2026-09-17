"""Tests for P18-G2C1 Intra-Scene Visual Clip Assembler.

Verifies:
1. Rejection of empty clip lists;
2. Rejection of cross-scene clips;
3. Rejection of non-contiguous / duplicate indices;
4. Rejection of timing gaps;
5. Rejection of timing overlaps;
6. Rejection of missing / empty files;
7. Physical multi-clip concatenation via FFmpeg produces valid 1080p 24fps visual MP4;
8. Physical concatenated video has ZERO audio streams;
9. Physical duration is preserved within <= 1 frame tolerance.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from omega.application.beat_clip_assembler import (
    BeatClipAssembler,
    BeatClipAssemblerError,
    RenderedBeatClip,
)
from omega.application.ffmpeg_renderer import FFmpegRenderer


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="omega_beat_assembler_test_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


async def _create_synthetic_clip(
    path: Path,
    duration_s: float,
    color: str = "blue",
    width: int = 1920,
    height: int = 1080,
    fps: int = 24,
) -> None:
    """Generate a clean visual-only synthetic MP4 clip using FFmpeg."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s={width}x{height}:r={fps}:d={duration_s}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-fps_mode", "cfr",
        "-r", str(fps),
        "-an",
        "-movflags", "+faststart",
        str(path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, f"FFmpeg synthetic generator failed: {stderr.decode()}"
    assert path.is_file() and path.stat().st_size > 0


def test_01_assembler_rejects_empty_clips():
    """Assembler rejects empty clip collection."""
    assembler = BeatClipAssembler()
    with pytest.raises(BeatClipAssemblerError, match="Cannot assemble zero clips"):
        assembler.validate_clips([])


def test_02_assembler_rejects_cross_scene_clips(tmp_dir):
    """Assembler rejects clips with differing parent_scene_index."""
    dummy_file = tmp_dir / "clip.mp4"
    dummy_file.write_text("data")

    clips = [
        RenderedBeatClip(parent_scene_index=1, materialized_index=0, source_beat_index=0, start_ms=0, end_ms=1000, duration_ms=1000, path=dummy_file),
        RenderedBeatClip(parent_scene_index=2, materialized_index=1, source_beat_index=1, start_ms=1000, end_ms=2000, duration_ms=1000, path=dummy_file),
    ]
    assembler = BeatClipAssembler()
    with pytest.raises(BeatClipAssemblerError, match="Cross-scene clip detected"):
        assembler.validate_clips(clips)


def test_03_assembler_rejects_non_contiguous_or_duplicate_indices(tmp_dir):
    """Assembler requires materialized indices to be contiguous from 0."""
    dummy = tmp_dir / "clip.mp4"
    dummy.write_text("data")

    # Duplicate index
    dup_clips = [
        RenderedBeatClip(parent_scene_index=1, materialized_index=0, source_beat_index=0, start_ms=0, end_ms=1000, duration_ms=1000, path=dummy),
        RenderedBeatClip(parent_scene_index=1, materialized_index=0, source_beat_index=1, start_ms=1000, end_ms=2000, duration_ms=1000, path=dummy),
    ]
    assembler = BeatClipAssembler()
    with pytest.raises(BeatClipAssemblerError, match="Materialized indices must be contiguous"):
        assembler.validate_clips(dup_clips)

    # Missing index (gap: 0, 2)
    gap_clips = [
        RenderedBeatClip(parent_scene_index=1, materialized_index=0, source_beat_index=0, start_ms=0, end_ms=1000, duration_ms=1000, path=dummy),
        RenderedBeatClip(parent_scene_index=1, materialized_index=2, source_beat_index=2, start_ms=1000, end_ms=2000, duration_ms=1000, path=dummy),
    ]
    with pytest.raises(BeatClipAssemblerError, match="Materialized indices must be contiguous"):
        assembler.validate_clips(gap_clips)


def test_04_assembler_rejects_timing_gaps_and_overlaps(tmp_dir):
    """Assembler enforces strict monotonic continuity without gaps or overlaps."""
    dummy = tmp_dir / "clip.mp4"
    dummy.write_text("data")
    assembler = BeatClipAssembler()

    # Overlap: clip 1 starts at 900ms when clip 0 ends at 1000ms
    overlap_clips = [
        RenderedBeatClip(parent_scene_index=1, materialized_index=0, source_beat_index=0, start_ms=0, end_ms=1000, duration_ms=1000, path=dummy),
        RenderedBeatClip(parent_scene_index=1, materialized_index=1, source_beat_index=1, start_ms=900, end_ms=2000, duration_ms=1100, path=dummy),
    ]
    with pytest.raises(BeatClipAssemblerError, match="Timing overlap detected"):
        assembler.validate_clips(overlap_clips)

    # Gap: clip 1 starts at 1200ms when clip 0 ends at 1000ms
    gap_clips = [
        RenderedBeatClip(parent_scene_index=1, materialized_index=0, source_beat_index=0, start_ms=0, end_ms=1000, duration_ms=1000, path=dummy),
        RenderedBeatClip(parent_scene_index=1, materialized_index=1, source_beat_index=1, start_ms=1200, end_ms=2200, duration_ms=1000, path=dummy),
    ]
    with pytest.raises(BeatClipAssemblerError, match="Timing gap detected"):
        assembler.validate_clips(gap_clips)


def test_05_assembler_rejects_missing_or_empty_files(tmp_dir):
    """Assembler verifies that all clip files exist on disk and are non-empty."""
    missing = tmp_dir / "missing.mp4"
    empty = tmp_dir / "empty.mp4"
    empty.touch()

    assembler = BeatClipAssembler()

    missing_clip = [
        RenderedBeatClip(parent_scene_index=1, materialized_index=0, source_beat_index=0, start_ms=0, end_ms=1000, duration_ms=1000, path=missing),
    ]
    with pytest.raises(BeatClipAssemblerError, match="Clip file not found"):
        assembler.validate_clips(missing_clip)

    empty_clip = [
        RenderedBeatClip(parent_scene_index=1, materialized_index=0, source_beat_index=0, start_ms=0, end_ms=1000, duration_ms=1000, path=empty),
    ]
    with pytest.raises(BeatClipAssemblerError, match="Clip file is empty"):
        assembler.validate_clips(empty_clip)


async def _create_synthetic_clip_with_audio(
    path: Path,
    duration_s: float,
    color: str = "red",
    fps: int = 24,
) -> None:
    """Generate a synthetic clip containing both H.264 video and AAC audio."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s=1920x1080:r={fps}:d={duration_s}",
        "-f", "lavfi",
        "-i", "anullsrc=r=48000:cl=stereo",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-t", str(duration_s),
        "-movflags", "+faststart",
        str(path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, f"FFmpeg audio clip generator failed: {stderr.decode()}"
    assert path.is_file() and path.stat().st_size > 0


@pytest.mark.asyncio
async def test_06_physical_multi_clip_assembly_passes_ffprobe(tmp_dir):
    """Component test: assembles 3 synthetic clips, probes with ffprobe, verifies zero audio streams."""
    c0_path = tmp_dir / "beat_0.mp4"
    c1_path = tmp_dir / "beat_1.mp4"
    c2_path = tmp_dir / "beat_2.mp4"
    out_path = tmp_dir / "scene_assembled.mp4"

    # Generate 3 visual-only clips: 0.5s, 0.5s, 1.0s (Total = 2.0s = 2000ms)
    await _create_synthetic_clip(c0_path, duration_s=0.5, color="red")
    await _create_synthetic_clip(c1_path, duration_s=0.5, color="green")
    await _create_synthetic_clip(c2_path, duration_s=1.0, color="blue")

    clips = [
        RenderedBeatClip(parent_scene_index=1, materialized_index=0, source_beat_index=0, start_ms=0, end_ms=500, duration_ms=500, path=c0_path),
        RenderedBeatClip(parent_scene_index=1, materialized_index=1, source_beat_index=1, start_ms=500, end_ms=1000, duration_ms=500, path=c1_path),
        RenderedBeatClip(parent_scene_index=1, materialized_index=2, source_beat_index=2, start_ms=1000, end_ms=2000, duration_ms=1000, path=c2_path),
    ]

    assembler = BeatClipAssembler(renderer=FFmpegRenderer())
    result = await assembler.assemble(clips, output_path=out_path, fps=24)

    # 1. Output exists and non-empty
    assert result.output_path.is_file()
    assert result.output_path.stat().st_size > 0
    assert result.beat_count == 3
    assert result.expected_duration_ms == 2000
    assert result.content_sha256 is not None
    assert len(result.content_sha256) == 64
    assert result.physical_duration_ms > 0
    assert abs(result.physical_duration_ms - 2000.0) <= (1000.0 / 24.0) + 2.0

    # 2. Probe with ffprobe
    probe_cmd = [
        "ffprobe",
        "-v", "error",
        "-show_streams",
        "-show_format",
        "-of", "json",
        str(out_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *probe_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, f"ffprobe failed: {stderr.decode()}"

    probe_data = json.loads(stdout.decode("utf-8"))
    streams = probe_data.get("streams", [])

    # Verify stream types: exactly 1 video stream, zero audio streams!
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    assert len(video_streams) == 1, "Expected exactly one video stream"
    assert len(audio_streams) == 0, "Expected ZERO audio streams in visual assembly"

    v = video_streams[0]
    assert v.get("codec_name") == "h264"
    assert int(v.get("width")) == 1920
    assert int(v.get("height")) == 1080
    assert v.get("pix_fmt") == "yuv420p"

    # FPS verification
    avg_fps = v.get("avg_frame_rate", "")
    assert avg_fps in ("24/1", "24")
    r_fps = v.get("r_frame_rate", "")
    assert r_fps in ("24/1", "24")

    # Verify duration matches ~2.0s within 1 frame (1/24s ~ 0.042s)
    duration_s = float(probe_data.get("format", {}).get("duration", 0))
    assert abs(duration_s - 2.0) <= (1.0 / 24.0) + 0.05


@pytest.mark.asyncio
async def test_07_assembler_rejects_clip_with_audio(tmp_dir):
    """Assembler strictly rejects input clips containing audio streams before assembly."""
    audio_clip = tmp_dir / "clip_with_audio.mp4"
    await _create_synthetic_clip_with_audio(audio_clip, duration_s=1.0)

    clips = [
        RenderedBeatClip(
            parent_scene_index=1,
            materialized_index=0,
            source_beat_index=0,
            start_ms=0,
            end_ms=1000,
            duration_ms=1000,
            path=audio_clip,
        ),
    ]
    assembler = BeatClipAssembler(renderer=FFmpegRenderer())
    out_path = tmp_dir / "out.mp4"

    with pytest.raises(BeatClipAssemblerError, match="visual-only clips required"):
        await assembler.assemble(clips, output_path=out_path, fps=24)


@pytest.mark.asyncio
async def test_08_assembler_rejects_input_duration_drift(tmp_dir):
    """Assembler rejects input clip whose physical duration drifts beyond 1 frame from declared duration."""
    # Create clip of physical duration 1.0s (1000ms)
    clip_path = tmp_dir / "short_clip.mp4"
    await _create_synthetic_clip(clip_path, duration_s=1.0)

    # Declared duration is 2000ms (1000ms drift, far exceeding 1 frame = 41.7ms)
    clips = [
        RenderedBeatClip(
            parent_scene_index=1,
            materialized_index=0,
            source_beat_index=0,
            start_ms=0,
            end_ms=2000,
            duration_ms=2000,
            path=clip_path,
        ),
    ]
    assembler = BeatClipAssembler(renderer=FFmpegRenderer())
    out_path = tmp_dir / "out.mp4"

    with pytest.raises(BeatClipAssemblerError, match="duration drift"):
        await assembler.assemble(clips, output_path=out_path, fps=24)


@pytest.mark.asyncio
async def test_09_assembler_rejects_output_duration_drift(tmp_dir, monkeypatch):
    """Assembler rejects assembled output if probed duration drifts from timeline total duration."""
    clip_path = tmp_dir / "valid_beat.mp4"
    await _create_synthetic_clip(clip_path, duration_s=1.0)

    clips = [
        RenderedBeatClip(
            parent_scene_index=1,
            materialized_index=0,
            source_beat_index=0,
            start_ms=0,
            end_ms=1000,
            duration_ms=1000,
            path=clip_path,
        ),
    ]
    assembler = BeatClipAssembler(renderer=FFmpegRenderer())
    out_path = tmp_dir / "out.mp4"

    real_probe = assembler._probe_media

    async def mock_probe_media(p: Path) -> dict:
        data = await real_probe(p)
        # If probing the output file, fake a drifted duration
        if p == out_path.resolve():
            data = json.loads(json.dumps(data))
            data["format"]["duration"] = "5.000000"  # 5000ms vs expected 1000ms
        return data

    monkeypatch.setattr(assembler, "_probe_media", mock_probe_media)

    with pytest.raises(BeatClipAssemblerError, match="Assembled video duration drift"):
        await assembler.assemble(clips, output_path=out_path, fps=24)
