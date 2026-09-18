"""P18-G2D2D: Global cumulative parent-frame allocation and canonical final-concat frame conservation tests."""

import json
import subprocess
from pathlib import Path

import pytest

from omega.application.beat_asset_policy import BeatAssetAction
from omega.application.beat_render_adapter import BeatAssetDecision, BeatRenderUnit
from omega.application.beat_visual_renderer import (
    BeatVisualRenderError,
    quantize_beat_frame_counts,
    quantize_parent_frame_counts,
)
from omega.application.editorial_beat import (
    BeatMotionIntent,
    BeatTransitionIntent,
)
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


def _make_dummy_unit(idx: int, start_ms: int, end_ms: int, parent_scene_index: int = 1) -> BeatRenderUnit:
    from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
    from omega.application.visual_direction import (
        VisualDirection,
        VisualRenderMode,
        VisualTemplateId,
    )

    duration_ms = end_ms - start_ms
    scene_view = StoryboardScene(
        sequence_index=parent_scene_index,
        section_id="sec_1",
        purpose="purpose",
        source_statement_references=[1],
        narration_excerpt="narration",
        estimated_duration_seconds=duration_ms / 1000.0,
        visual_strategy=VisualStrategy.DIAGRAM,
        visual_brief="brief",
    )
    direction_view = VisualDirection(
        scene_index=parent_scene_index,
        render_mode=VisualRenderMode.TEMPLATE,
        template_id=VisualTemplateId.HERO_TITLE,
        asset_requirements=[],
        motion_profile=None,
        rationale="test rationale",
    )
    return BeatRenderUnit(
        parent_scene_index=parent_scene_index,
        materialized_index=idx,
        source_beat_index=idx,
        start_ms=start_ms,
        end_ms=end_ms,
        duration_ms=duration_ms,
        template_id=VisualTemplateId.HERO_TITLE,
        camera_motion_intent=BeatMotionIntent.STATIC,
        transition_intent=BeatTransitionIntent.HARD_CUT,
        asset_decision=BeatAssetDecision(
            parent_scene_index=parent_scene_index,
            beat_index=idx,
            rationale="test",
            action=BeatAssetAction.LOCAL_TEMPLATE,
        ),
        scene_view=scene_view,
        direction_view=direction_view,
    )


# =========================================================================
# 1. PURE QUANTIZATION TESTS
# =========================================================================

def test_quantize_parent_frame_counts_sunset_exact():
    """Verify that the Sunset T1-D2 timeline yields exactly [176, 291, 294, 219] summing to 980."""
    intervals = [
        (0, 7315),
        (7315, 19434),
        (19434, 31705),
        (31705, 40826),
    ]
    counts = quantize_parent_frame_counts(intervals, 40826, 24)
    assert counts == (176, 291, 294, 219)
    assert sum(counts) == 980


def test_quantize_parent_frame_counts_frame_aligned():
    """Verify that perfectly frame-aligned intervals produce exact integer frame counts."""
    intervals = [
        (0, 1000),
        (1000, 2000),
        (2000, 3000),
    ]
    counts = quantize_parent_frame_counts(intervals, 3000, 24)
    assert counts == (24, 24, 24)
    assert sum(counts) == 72


def test_quantize_parent_frame_counts_non_frame_aligned():
    """Verify that arbitrary non-frame-aligned intervals strictly conserve the global frame budget."""
    intervals = [
        (0, 333),
        (333, 666),
        (666, 1000),
    ]
    counts = quantize_parent_frame_counts(intervals, 1000, 24)
    assert sum(counts) == 24
    assert counts == (8, 8, 8)


@pytest.mark.parametrize(
    ("intervals", "global_dur", "fps", "err_match"),
    [
        ([(50, 1000)], 1000, 24, "First scene interval must start at 0 ms"),
        ([(0, 1000), (1050, 2000)], 2000, 24, "Non-contiguous scene intervals"),
        ([(0, 1000), (950, 2000)], 2000, 24, "Non-contiguous scene intervals"),
        ([(0, 1000)], 1050, 24, "Final scene end_ms"),
        ([(0, 0)], 0, 24, "global_duration_ms must be a positive integer"),
        ([(0, 1000)], 1000, 0, "fps must be a positive integer"),
        ([(0, 1000)], 1000, -24, "fps must be a positive integer"),
        ([(0, 1000)], 1000, True, "fps must be a positive integer"),
        ([(0, 1000)], True, 24, "global_duration_ms must be a positive integer"),
        ([], 1000, 24, "Cannot quantize frame counts for empty intervals"),
    ],
)
def test_quantize_parent_frame_counts_invalid_fails_closed(intervals, global_dur, fps, err_match):
    """Verify that malformed or invalid interval specifications fail closed."""
    with pytest.raises(BeatVisualRenderError, match=err_match):
        quantize_parent_frame_counts(intervals, global_dur, fps)


@pytest.mark.parametrize(
    ("fps", "timeline_start", "err_match"),
    [
        (True, 0, "fps must be a positive integer"),
        (24, True, "timeline_start_ms must be a non-negative integer"),
        (24, -1, "timeline_start_ms must be a non-negative integer"),
    ],
)
def test_quantize_beat_frame_counts_invalid_types_fail_closed(fps, timeline_start, err_match):
    """Verify that boolean or negative arguments to quantize_beat_frame_counts fail closed."""
    unit = _make_dummy_unit(0, 0, 1000)
    with pytest.raises(BeatVisualRenderError, match=err_match):
        quantize_beat_frame_counts([unit], 1000, fps, timeline_start_ms=timeline_start)



# =========================================================================
# 2. BEAT QUANTIZATION TIMELINE-OFFSET TESTS
# =========================================================================

def test_quantize_beat_frame_counts_with_timeline_start_ms():
    """Verify that quantizing beats with timeline_start_ms derives absolute boundaries and matches parent budget."""
    # Scene 4 in Sunset: timeline_start_ms=31705, dur=9121 ms (ends at 40826 ms)
    # Beat 0: 0 -> 3731 ms (dur 3731 ms)
    # Beat 1: 3731 -> 9121 ms (dur 5390 ms)
    units = [
        _make_dummy_unit(0, 0, 3731),
        _make_dummy_unit(1, 3731, 9121),
    ]
    counts = quantize_beat_frame_counts(units, 9121, 24, timeline_start_ms=31705)
    # At timeline_start_ms=31705:
    # Beat 0 absolute end = 31705 + 3731 = 35436 -> ceil(35436*24/1000) = 851 frames
    # Start frame = ceil(31705*24/1000) = 761 frames -> Beat 0 gets 851 - 761 = 90 frames
    # Beat 1 absolute end = 40826 -> ceil(40826*24/1000) = 980 frames -> Beat 1 gets 980 - 851 = 129 frames
    assert counts == (90, 129)
    assert sum(counts) == 219  # Exactly matches Scene 4's allocated budget!


def test_quantize_beat_frame_counts_timeline_start_zero_preserves_legacy():
    """Verify that timeline_start_ms=0 retains legacy behavior."""
    units = [
        _make_dummy_unit(0, 0, 3731),
        _make_dummy_unit(1, 3731, 9121),
    ]
    counts_zero = quantize_beat_frame_counts(units, 9121, 24, timeline_start_ms=0)
    counts_default = quantize_beat_frame_counts(units, 9121, 24)
    assert counts_zero == counts_default == (90, 129)


# =========================================================================
# 3. LEGACY PARENT RENDERING WITH FRAME_COUNT_OVERRIDE
# =========================================================================

@pytest.mark.asyncio
async def test_legacy_parent_render_clip_respects_frame_count_override(tmp_path):
    """Verify that VisualV2VideoRenderer.render_clip strictly respects frame_count_override."""
    from omega.application.visual_template_renderer import (
        RenderedTemplateDocument,
        VisualTemplateId,
    )
    from omega.infrastructure.browser_capture_runtime import BrowserCaptureRuntime
    from omega.infrastructure.visual_v2_video_renderer import VisualV2VideoRenderer

    renderer = VisualV2VideoRenderer()
    doc = RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.HERO_TITLE,
        width=1920,
        height=1080,
        html="<html><body style='background:red;'><h1>Test</h1></body></html>",
        semantic_element_ids=(),
        content_sha256="dummy",
        text_fitting=(),
    )
    out_mp4 = tmp_path / "legacy_override.mp4"

    # For 12.271s at 24 fps, independent ceil is 295 frames.
    # Global parent allocation assigns 294 frames.
    async with BrowserCaptureRuntime() as browser:
        res = await renderer.render_clip(
            document=doc,
            motion_profile=None,
            duration_seconds=12.271,
            output_path=out_mp4,
            browser_runtime=browser,
            fps=24,
            frame_count_override=294,
        )

    probed_frames = _probe_video_frames(out_mp4)
    assert probed_frames == 294
    assert res.frame_count == 294


# =========================================================================
# 4. FOUR-PARENT PHYSICAL REGRESSION AND EXACT MASTER CONCAT
# =========================================================================

@pytest.mark.asyncio
async def test_g2d2d_four_parent_exact_frame_concatenation(tmp_path):
    """Physical regression proving that 4 parent clips concatenated with exact_video_frame_count=980 produce 980 frames."""
    renderer = FFmpegRenderer()
    # Allocated parent frame vector: [176, 291, 294, 219] = 980 frames
    scenes = [
        (1, 176, 7.315351),
        (2, 291, 12.119342),
        (3, 294, 12.271338),
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

    # Concatenate clips with exact_video_frame_count=980
    master_out = tmp_path / "final_master.mp4"
    await renderer.concatenate_clips(
        clip_paths=scene_clips,
        output_path=master_out,
        target_fps=24,
        exact_video_frame_count=980,
    )

    # Verify video frame count is exactly 980
    probed_frames = _probe_video_frames(master_out)
    assert probed_frames == 980, f"Expected exactly 980 frames, got {probed_frames}"

    # Probe frame timestamps for normalized coverage
    frames_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_frames",
        "-show_entries", "frame=pts_time",
        "-of", "json",
        str(master_out),
    ]
    proc = subprocess.run(frames_cmd, capture_output=True, text=True, check=True, timeout=15)
    v_frames = json.loads(proc.stdout)["frames"]
    assert len(v_frames) == 980

    first_v = float(v_frames[0]["pts_time"])
    last_v = float(v_frames[-1]["pts_time"])
    v_cov = (last_v + 1.0 / 24.0) - first_v

    # Video coverage must equal 980 / 24 = 40.833333s within float rounding
    assert abs(v_cov - (980.0 / 24.0)) < 0.001

    # Logical runtime is 40.826s -> delta is ~ 7.333 ms, safely < 1 frame (41.667 ms)
    logical_runtime_s = 40.826
    delta_s = abs(v_cov - logical_runtime_s)
    assert delta_s < (1.0 / 24.0)  # delta is ~ 7.333 ms < 41.667 ms


# =========================================================================
# 5. FINAL A/V TIMELINE INTEGRITY & MASTER NORMALIZATION
# =========================================================================

@pytest.mark.asyncio
async def test_g2d2d_final_av_timeline_integrity_and_normalization(tmp_path):
    """Verify normalized A/V coverage within 1 frame and that master audio normalization preserves 980 frames."""
    renderer = FFmpegRenderer()
    scenes = [
        (1, 176, 7.315351),
        (2, 291, 12.119342),
        (3, 294, 12.271338),
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

    master_out = tmp_path / "final_master.mp4"
    await renderer.concatenate_clips(
        clip_paths=scene_clips,
        output_path=master_out,
        target_fps=24,
        exact_video_frame_count=980,
    )

    # Probe audio packets
    pkts_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_packets",
        "-show_entries", "packet=pts_time,duration_time",
        "-of", "json",
        str(master_out),
    ]
    proc = subprocess.run(pkts_cmd, capture_output=True, text=True, check=True, timeout=15)
    a_pkts = json.loads(proc.stdout)["packets"]
    first_a = float(a_pkts[0]["pts_time"])
    last_a = float(a_pkts[-1]["pts_time"]) + float(a_pkts[-1]["duration_time"])
    a_cov = last_a - first_a

    # Video coverage
    v_cov = 980.0 / 24.0  # 40.833333s

    # Normalized coverage difference between video and audio must be <= 1 frame duration (41.67ms)
    one_frame = 1.0 / 24.0
    assert abs(v_cov - a_cov) <= one_frame

    # Master audio normalization must preserve exact 980 video frames
    normalized_out = tmp_path / "master_normalized.mp4"
    await renderer.normalize_master_audio(
        video_path=master_out,
        output_path=normalized_out,
    )
    norm_frames = _probe_video_frames(normalized_out)
    assert norm_frames == 980, f"Expected 980 frames after audio normalization, got {norm_frames}"


# =========================================================================
# 6. NO-NARRATION GLOBAL TIMELINE AUDIT & INTEGRITY
# =========================================================================

def test_no_narration_global_timeline_deterministic_allocation():
    """Prove that no-narration path builds contiguous integer-ms intervals, equals final end, and strictly conserves frame budget."""
    # Simulate 4 scenes without narration
    estimated_durations = [7.315, 12.119, 12.271, 9.121]
    fps = 24

    parent_intervals: list[tuple[int, int]] = []
    curr_ms = 0
    for dur_s in estimated_durations:
        dur_ms = max(1, int(round(dur_s * 1000)))
        parent_intervals.append((curr_ms, curr_ms + dur_ms))
        curr_ms += dur_ms
    global_logical_duration_ms = curr_ms

    # 1. Contiguous intervals
    for idx in range(1, len(parent_intervals)):
        assert parent_intervals[idx][0] == parent_intervals[idx - 1][1]

    # 2. Global duration matches final parent end
    assert global_logical_duration_ms == parent_intervals[-1][1] == 40826

    # 3. Quantize parent frames
    parent_frame_budgets = quantize_parent_frame_counts(
        parent_intervals, global_logical_duration_ms, fps
    )
    assert parent_frame_budgets == (176, 291, 294, 219)
    assert sum(parent_frame_budgets) == 980

    # 4. Global budget matches integer ceil of global duration
    expected_global = (global_logical_duration_ms * fps + 999) // 1000
    assert sum(parent_frame_budgets) == expected_global
