"""P18-G2D2C: Parent-scene video frame preservation through narration mux and A/V timeline integrity tests."""

import json
import subprocess
from pathlib import Path

import pytest

from omega.application.ffmpeg_renderer import FFmpegRenderer


def _probe_video_frames(path: Path) -> int:
    """Return integer video frame count probed via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=nb_frames",
        "-of", "json",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True)
    data = json.loads(proc.stdout)
    return int(data["streams"][0]["nb_frames"])


def _probe_stream_and_format_durations(path: Path) -> dict:
    """Return probed video, audio, and format durations and timestamps."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,nb_frames,start_time,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True)
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    v_s = next((s for s in streams if s["codec_type"] == "video"), {})
    a_s = next((s for s in streams if s["codec_type"] == "audio"), {})
    return {
        "video_frames": int(v_s.get("nb_frames", 0)),
        "video_duration": float(v_s.get("duration", 0.0)),
        "audio_duration": float(a_s.get("duration", 0.0)),
        "format_duration": float(fmt.get("duration", 0.0)),
    }


def _create_synthetic_video(output_path: Path, frame_count: int, fps: int = 24) -> Path:
    """Create a synthetic CFR test video with exact frame count."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"testsrc=size=320x180:rate={fps}",
        "-frames:v", str(frame_count),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=15)
    return output_path


def _create_synthetic_audio(output_path: Path, duration_seconds: float) -> Path:
    """Create a synthetic mono 44.1kHz WAV narration audio with exact duration."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={duration_seconds:.6f}",
        "-c:a", "pcm_s16le",
        "-ar", "44100",
        "-ac", "1",
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=15)
    return output_path


@pytest.mark.asyncio
async def test_g2d2c_scene4_reproduction_before_and_after(tmp_path):
    """Prove that historical -shortest mux drops frames (219 -> 216), whereas repaired mux preserves all 219."""
    renderer = FFmpegRenderer()
    v_input = _create_synthetic_video(tmp_path / "scene4_raw.mp4", frame_count=219)

    real_s4_audio = Path(
        "/app/data/channels/1fa71808-97ff-4c74-964f-8894519388cc/production/"
        "4bbe7123-f499-46f6-84c9-f805225f3542/narration/audio_e36efd0274.wav"
    )
    if real_s4_audio.is_file():
        a_input = real_s4_audio
    else:
        a_input = _create_synthetic_audio(tmp_path / "scene4_audio.wav", duration_seconds=9.121338)

    # 1. Historical / default legacy behavior: preserve_video_duration=False (uses -shortest)
    legacy_out = tmp_path / "scene4_legacy.mp4"
    await renderer.mux_video_audio(
        video_path=v_input,
        audio_path=a_input,
        output_path=legacy_out,
        preserve_video_duration=False,
    )
    legacy_frames = _probe_video_frames(legacy_out)
    if real_s4_audio.is_file():
        # Exact reproduction: historical behavior truncated 219 frames down to 216
        assert legacy_frames == 216, f"Expected historical truncation to 216, got {legacy_frames}"
    else:
        assert legacy_frames < 219, f"Expected truncation below 219, got {legacy_frames}"

    # 2. Repaired canonical behavior: preserve_video_duration=True (omits -shortest)
    import time
    canonical_out = tmp_path / "scene4_canonical.mp4"
    t0 = time.monotonic()
    await renderer.mux_video_audio(
        video_path=v_input,
        audio_path=a_input,
        output_path=canonical_out,
        preserve_video_duration=True,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 30.0, f"Mux process must terminate < 30s, took {elapsed:.2f}s"

    info = _probe_stream_and_format_durations(canonical_out)
    assert info["video_frames"] == 219, f"Expected exact frame preservation of 219, got {info['video_frames']}"
    assert info["audio_duration"] > 0.0, "Expected valid audio stream to exist"
    one_frame = 1.0 / 24.0
    assert abs(info["video_duration"] - info["audio_duration"]) <= one_frame + 0.005, (
        f"A/V normalized delta must be <= one frame, got video={info['video_duration']}, audio={info['audio_duration']}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scene_index", "expected_frames", "audio_dur_s", "dur_ms"),
    [
        (1, 176, 7.315351, 7315),
        (2, 291, 12.119342, 12119),
        (3, 295, 12.271338, 12271),
        (4, 219, 9.121338, 9121),
    ],
    ids=["scene1-7315ms", "scene2-12119ms", "scene3-12271ms", "scene4-9121ms"],
)
async def test_g2d2c_all_four_parent_durations_preserve_exact_frames(
    tmp_path, scene_index, expected_frames, audio_dur_s, dur_ms
):
    """Verify that every parent scene duration preserves exact video frame count through narration mux."""
    renderer = FFmpegRenderer()
    v_in = _create_synthetic_video(tmp_path / f"scene_{scene_index}_v.mp4", frame_count=expected_frames)
    a_in = _create_synthetic_audio(tmp_path / f"scene_{scene_index}_a.wav", duration_seconds=audio_dur_s)
    out_p = tmp_path / f"scene_{scene_index}_mux.mp4"

    await renderer.mux_video_audio(
        video_path=v_in,
        audio_path=a_in,
        output_path=out_p,
        preserve_video_duration=True,
    )

    info = _probe_stream_and_format_durations(out_p)
    assert info["video_frames"] == expected_frames
    # Video end and audio end must be within 1 frame duration (1/24 = 0.041667s)
    one_frame = 1.0 / 24.0
    assert abs(info["video_duration"] - info["audio_duration"]) <= one_frame + 0.005


@pytest.mark.asyncio
async def test_g2d2c_subtitle_burn_and_mux_pipeline_preservation(tmp_path):
    """Test full canonical pipeline: video -> ASS subtitle burn -> narration mux preserves all 219 frames."""
    renderer = FFmpegRenderer()
    v_in = _create_synthetic_video(tmp_path / "scene4_raw.mp4", frame_count=219)
    a_in = _create_synthetic_audio(tmp_path / "scene4_a.wav", duration_seconds=9.121338)

    ass_path = tmp_path / "scene4.ass"
    ass_path.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 160\nPlayResY: 90\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,10,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,5,5,5,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:05.00,Default,,0,0,0,,Sunset horizon\n",
        encoding="utf-8",
    )

    # 1. Burn subtitles
    subtitled_out = tmp_path / "scene4_subtitled.mp4"
    await renderer.burn_ass_subtitles(
        video_path=v_in,
        ass_path=ass_path,
        output_path=subtitled_out,
    )
    assert _probe_video_frames(subtitled_out) == 219

    # 2. Mux with narration using preserve_video_duration=True
    muxed_out = tmp_path / "scene4_final.mp4"
    await renderer.mux_video_audio(
        video_path=subtitled_out,
        audio_path=a_in,
        output_path=muxed_out,
        preserve_video_duration=True,
    )
    assert _probe_video_frames(muxed_out) == 219


@pytest.mark.asyncio
async def test_g2d2c_four_scene_concatenation_and_master_av_integrity(tmp_path):
    """Test concatenating all 4 parent clips after safe mux and verify master A/V integrity."""
    renderer = FFmpegRenderer()
    scenes = [
        (1, 176, 7.315351),
        (2, 291, 12.119342),
        (3, 295, 12.271338),
        (4, 219, 9.121338),
    ]
    scene_clips = []
    for idx, frames, audio_dur in scenes:
        v_p = _create_synthetic_video(tmp_path / f"scene_{idx}_v.mp4", frame_count=frames)
        a_p = _create_synthetic_audio(tmp_path / f"scene_{idx}_a.wav", duration_seconds=audio_dur)
        m_p = tmp_path / f"scene_{idx}_mux.mp4"
        await renderer.mux_video_audio(
            video_path=v_p,
            audio_path=a_p,
            output_path=m_p,
            preserve_video_duration=True,
        )
        scene_clips.append(m_p)

    # Concatenate clips
    master_out = tmp_path / "final_master.mp4"
    await renderer.concatenate_clips(
        clip_paths=scene_clips,
        output_path=master_out,
        target_fps=24,
    )

    info = _probe_stream_and_format_durations(master_out)
    expected_sum_frames = 176 + 291 + 295 + 219  # 981
    # Concatenation produces 981 or 982 frames depending on PTS container alignment, well within 1 frame
    assert abs(info["video_frames"] - expected_sum_frames) <= 1
    # Final video and audio must end together within 1 frame tolerance
    one_frame = 1.0 / 24.0
    assert abs(info["video_duration"] - info["audio_duration"]) <= one_frame + 0.005


@pytest.mark.asyncio
async def test_g2d2c_master_normalization_preserves_video_frames(tmp_path):
    """Verify that normalize_master_audio preserves video frames exactly via stream copy."""
    renderer = FFmpegRenderer()
    v_in = _create_synthetic_video(tmp_path / "master_v.mp4", frame_count=100)
    a_in = _create_synthetic_audio(tmp_path / "master_a.wav", duration_seconds=4.166667)
    master_mp4 = tmp_path / "master.mp4"

    await renderer.mux_video_audio(
        video_path=v_in,
        audio_path=a_in,
        output_path=master_mp4,
        preserve_video_duration=True,
    )
    initial_frames = _probe_video_frames(master_mp4)
    assert initial_frames == 100

    normalized_out = tmp_path / "master_normalized.mp4"
    await renderer.normalize_master_audio(
        video_path=master_mp4,
        output_path=normalized_out,
    )

    normalized_frames = _probe_video_frames(normalized_out)
    assert normalized_frames == initial_frames == 100
