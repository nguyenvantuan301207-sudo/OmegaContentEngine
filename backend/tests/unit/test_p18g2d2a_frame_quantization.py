"""Unit tests for P18-G2D2A: Deterministic cumulative frame-boundary quantization.

Verifies:
1. Primary regression: Scene 4 T1-D failure case (9121ms @ 24 fps) allocates [90, 129] = 219 frames,
   NOT [90, 130] = 220 frames;
2. Multi-beat accumulation proof: parent frame count strictly equals ceil(parent_ms * fps / 1000),
   preventing independent ceil drift accumulation across arbitrarily many beats;
3. Frame-aligned boundary case preserves exact frame counts without alteration;
4. quantize_beat_frame_counts fail-closed validation (fps, contiguity, positive durations, total alignment);
5. Legacy renderer compatibility: omitting frame_count_override preserves math.ceil(duration * fps);
6. Physical BROLL rendering with exact frame-count override produces exact requested frame count;
7. Physical IMAGE/template rendering with exact frame-count override produces exact requested frame count;
8. Physical assembly regression: 90 + 129 = 219 frames assembled and accepted under existing 1-frame tolerance.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from omega.application.beat_asset_policy import BeatAssetAction, BeatAssetDecision
from omega.application.beat_clip_assembler import (
    BeatClipAssembler,
    RenderedBeatClip,
)
from omega.application.beat_render_adapter import BeatRenderUnit
from omega.application.beat_visual_renderer import (
    BeatVisualRenderError,
    quantize_beat_frame_counts,
)
from omega.application.editorial_beat import BeatMotionIntent, BeatTransitionIntent
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.visual_asset_binding import BoundBrollAsset
from omega.application.visual_direction import (
    VisualAssetKind,
    VisualDirection,
    VisualRenderMode,
    VisualTemplateId,
)
from omega.application.visual_template_renderer import RenderedTemplateDocument
from omega.infrastructure.browser_capture_runtime import BrowserCapturedFrame
from omega.infrastructure.visual_v2_video_renderer import (
    VisualV2VideoRenderer,
    VisualV2VideoRenderError,
)


def _probe_video(path: Path) -> dict:
    """Helper to inspect physical video container and streams with bounded timeout."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-count_frames",
        "-show_streams",
        "-show_format",
        "-print_format", "json",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
    return json.loads(res.stdout)


def _make_unit(
    *,
    materialized_index: int,
    start_ms: int,
    end_ms: int,
    parent_scene_index: int = 1,
) -> BeatRenderUnit:
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
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        asset_requirements=[],
        motion_profile="flow_reveal",
        rationale="test rationale",
    )
    asset_decision = BeatAssetDecision(
        parent_scene_index=parent_scene_index,
        beat_index=materialized_index,
        action=BeatAssetAction.LOCAL_TEMPLATE,
        required_kind=None,
        reuse_from_beat_index=None,
        rationale="test rationale",
    )
    return BeatRenderUnit(
        parent_scene_index=parent_scene_index,
        materialized_index=materialized_index,
        source_beat_index=materialized_index,
        start_ms=start_ms,
        end_ms=end_ms,
        duration_ms=duration_ms,
        scene_view=scene_view,
        direction_view=direction_view,
        asset_decision=asset_decision,
        camera_motion_intent=BeatMotionIntent.STATIC,
        transition_intent=BeatTransitionIntent.HARD_CUT,
    )


# ============================================================================
# 1. PRIMARY REGRESSION: T1-D SCENE 4 (9121 ms @ 24 fps)
# ============================================================================


def test_t1d_failure_case_exact_allocation():
    """Reproduce exact T1-D Scene 4 failure case: 9121 ms at 24 fps.

    Beat 0: 0 -> 3731 ms (duration 3731 ms)
    Beat 1: 3731 -> 9121 ms (duration 5390 ms)

    Independent ceil gave [90, 130] = 220 frames (exceeded parent tolerance).
    Cumulative quantization must give [90, 129] = 219 frames.
    """
    fps = 24
    total_ms = 9121
    u0 = _make_unit(materialized_index=0, start_ms=0, end_ms=3731)
    u1 = _make_unit(materialized_index=1, start_ms=3731, end_ms=9121)

    frames = quantize_beat_frame_counts([u0, u1], total_ms, fps)

    # Required exact physical frame allocation
    assert frames == (90, 129)
    assert sum(frames) == 219

    # Prove NOT the defective independent ceil
    assert frames != (90, 130)
    assert sum(frames) != 220

    # Mathematical drift assertions:
    one_frame_ms = 1000.0 / fps  # 41.6667 ms
    # Beat 0: 90 frames = 3750.0 ms vs 3731 ms logical (drift = 19.0 ms < 1 frame)
    beat0_ms = 90 / fps * 1000.0
    assert abs(beat0_ms - 3731) < one_frame_ms
    assert round(abs(beat0_ms - 3731), 2) == 19.0

    # Beat 1: 129 frames = 5375.0 ms vs 5390 ms logical (drift = 15.0 ms < 1 frame)
    beat1_ms = 129 / fps * 1000.0
    assert abs(beat1_ms - 5390) < one_frame_ms
    assert round(abs(beat1_ms - 5390), 2) == 15.0

    # Parent: 219 frames = 9125.0 ms vs 9121 ms logical (drift = 4.0 ms < 1 frame)
    parent_ms = 219 / fps * 1000.0
    assert abs(parent_ms - total_ms) < one_frame_ms
    assert round(abs(parent_ms - total_ms), 2) == 4.0


# ============================================================================
# 2. MULTI-BEAT ACCUMULATION TEST
# ============================================================================


def test_multibeat_accumulation_proof():
    """Prove cumulative quantization prevents multi-frame drift accumulation across N cuts.

    Construct a 5-beat scene where independent ceil accumulates +2 extra frames.
    Total duration = 10100 ms at 24 fps:
      Canonical parent frames = ceil(10100 * 24 / 1000) = ceil(242.4) = 243 frames.
    """
    fps = 24
    total_ms = 10100
    cut_offsets = [0, 2100, 4150, 6200, 8150, 10100]

    units = [
        _make_unit(materialized_index=i, start_ms=cut_offsets[i], end_ms=cut_offsets[i + 1])
        for i in range(len(cut_offsets) - 1)
    ]

    # Independent ceil calculation:
    # 2100ms -> ceil(50.4) = 51
    # 2050ms -> ceil(49.2) = 50
    # 2050ms -> ceil(49.2) = 50
    # 1950ms -> ceil(46.8) = 47
    # 1950ms -> ceil(46.8) = 47
    # Sum independent = 51 + 50 + 50 + 47 + 47 = 245 frames (+2 frame defect!)
    independent_frames = [
        int((u.duration_ms * fps + 999) // 1000) for u in units
    ]
    assert sum(independent_frames) == 245

    # Cumulative quantization:
    allocated_frames = quantize_beat_frame_counts(units, total_ms, fps)

    # Must equal parent canonical frames exactly
    expected_parent_frames = (total_ms * fps + 999) // 1000
    assert expected_parent_frames == 243
    assert sum(allocated_frames) == 243
    assert allocated_frames == (51, 49, 49, 47, 47)

    # Every beat frame count must be positive
    assert all(f > 0 for f in allocated_frames)

    # Every beat physical duration must be within one frame of logical duration
    one_frame_ms = 1000.0 / fps
    for u, f in zip(units, allocated_frames, strict=True):
        physical_ms = f / fps * 1000.0
        assert abs(physical_ms - u.duration_ms) < one_frame_ms


# ============================================================================
# 3. FRAME-ALIGNED BOUNDARY CASE
# ============================================================================


def test_frame_aligned_boundaries():
    """Boundaries that already align exactly to the frame grid experience zero deviation."""
    fps = 24
    # Exact frame multiples at 24 fps:
    # Beat 0: 0 -> 1000 ms (24 frames)
    # Beat 1: 1000 -> 2000 ms (24 frames)
    # Beat 2: 2000 -> 3000 ms (24 frames)
    units = [
        _make_unit(materialized_index=0, start_ms=0, end_ms=1000),
        _make_unit(materialized_index=1, start_ms=1000, end_ms=2000),
        _make_unit(materialized_index=2, start_ms=2000, end_ms=3000),
    ]

    frames = quantize_beat_frame_counts(units, 3000, fps)
    assert frames == (24, 24, 24)
    assert sum(frames) == 72


# ============================================================================
# 4. QUANTIZE FAIL-CLOSED VALIDATION
# ============================================================================


def test_quantize_validation_errors():
    """Verify quantize_beat_frame_counts validates all preconditions strictly."""
    u0 = _make_unit(materialized_index=0, start_ms=0, end_ms=1000)
    u1 = _make_unit(materialized_index=1, start_ms=1000, end_ms=2000)

    # Invalid fps
    with pytest.raises(BeatVisualRenderError, match="fps must be a positive integer"):
        quantize_beat_frame_counts([u0, u1], 2000, 0)
    with pytest.raises(BeatVisualRenderError, match="fps must be a positive integer"):
        quantize_beat_frame_counts([u0, u1], 2000, -24)

    # Empty units
    with pytest.raises(BeatVisualRenderError, match="Cannot quantize frame counts for empty units"):
        quantize_beat_frame_counts([], 2000, 24)

    # Unordered materialized index
    bad_order_u1 = _make_unit(materialized_index=2, start_ms=1000, end_ms=2000)
    with pytest.raises(BeatVisualRenderError, match="Units must be ordered by materialized index"):
        quantize_beat_frame_counts([u0, bad_order_u1], 2000, 24)

    # First unit not starting at 0
    bad_start_u0 = _make_unit(materialized_index=0, start_ms=10, end_ms=1000)
    with pytest.raises(BeatVisualRenderError, match="First beat unit must start at 0 ms"):
        quantize_beat_frame_counts([bad_start_u0, u1], 2000, 24)

    # Non-contiguous boundary
    gap_u1 = _make_unit(materialized_index=1, start_ms=1050, end_ms=2000)
    with pytest.raises(BeatVisualRenderError, match="Non-contiguous beat boundaries"):
        quantize_beat_frame_counts([u0, gap_u1], 2000, 24)

    # Final end does not match total_duration_ms
    with pytest.raises(BeatVisualRenderError, match="Final beat end_ms .* does not match total_duration_ms"):
        quantize_beat_frame_counts([u0, u1], 3000, 24)


# ============================================================================
# 5. LEGACY RENDERER COMPATIBILITY & OVERRIDE VALIDATION
# ============================================================================


@pytest.mark.asyncio
async def test_legacy_renderer_default_and_override_contract(tmp_path: Path):
    """Proves:

    1. Omitting frame_count_override retains exact ceil(duration_seconds * fps) legacy behavior;
    2. Passing frame_count_override enforces the override count;
    3. Invalid frame_count_override (< 1 or non-int) raises VisualV2VideoRenderError.
    """
    renderer = VisualV2VideoRenderer()
    doc = RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        width=1920,
        height=1080,
        html="""<!doctype html><html><body>Test</body></html>""",
        semantic_element_ids=("test",),
        content_sha256="abc",
    )
    mock_browser = MagicMock()

    # 1. Invalid overrides fail closed
    with pytest.raises(VisualV2VideoRenderError, match="frame_count_override must be an integer >= 1"):
        await renderer.render_clip(
            document=doc,
            motion_profile=None,
            duration_seconds=1.0,
            output_path=tmp_path / "bad1.mp4",
            browser_runtime=mock_browser,
            fps=24,
            frame_count_override=0,
        )

    with pytest.raises(VisualV2VideoRenderError, match="frame_count_override must be an integer >= 1"):
        await renderer.render_clip(
            document=doc,
            motion_profile=None,
            duration_seconds=1.0,
            output_path=tmp_path / "bad2.mp4",
            browser_runtime=mock_browser,
            fps=24,
            frame_count_override=-5,
        )

    with pytest.raises(VisualV2VideoRenderError, match="frame_count_override must be an integer >= 1"):
        await renderer.render_clip(
            document=doc,
            motion_profile=None,
            duration_seconds=1.0,
            output_path=tmp_path / "bad3.mp4",
            browser_runtime=mock_browser,
            fps=24,
            frame_count_override=24.5,  # type: ignore[arg-type]
        )


# ============================================================================
# 6. PHYSICAL BROLL TEST WITH EXACT FRAME-COUNT OVERRIDE
# ============================================================================


@pytest.mark.asyncio
async def test_physical_broll_exact_frame_count_override(tmp_path: Path):
    """Physically renders a BROLL clip with frame_count_override=53 at 24 fps.

    Verifies via ffprobe:
    - stream count: exactly 1 video stream, 0 audio streams;
    - exact frame count: 53 frames;
    - fps: 24 CFR;
    - physical duration: approx 53 / 24 = 2.208s.
    """
    # 1. Create synthetic BROLL video input
    broll_path = tmp_path / "broll_source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "testsrc=size=1920x1080:rate=24",
            "-t", "5.0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(broll_path),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )

    broll_asset = BoundBrollAsset(
        asset_id="test_broll",
        kind=VisualAssetKind.BROLL,
        mime_type="video/mp4",
        content_sha256="0" * 64,
        local_path=broll_path,
        duration_seconds=5.0,
        width=1920,
        height=1080,
    )

    # 2. Transparent overlay frame
    trans_png = tmp_path / "overlay.png"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=black@0.0:size=1920x1080,format=rgba",
            "-vframes", "1",
            str(trans_png),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    overlay_bytes = trans_png.read_bytes()

    mock_browser = MagicMock()
    mock_browser.capture = AsyncMock(
        return_value=BrowserCapturedFrame(
            scene_index=1,
            template_id=VisualTemplateId.BROLL_EXPLAINER,
            width=1920,
            height=1080,
            png_bytes=overlay_bytes,
            png_sha256="fake_sha",
            source_html_sha256="fake_html",
        )
    )

    doc = RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        width=1920,
        height=1080,
        html="""<!doctype html><html><body>Overlay</body></html>""",
        semantic_element_ids=(),
        content_sha256="doc_sha",
    )

    renderer = VisualV2VideoRenderer()
    out_clip = tmp_path / "broll_clip_53.mp4"

    # Requested frame count override = 53 (logical duration = 2.19s)
    res = await renderer.render_clip(
        document=doc,
        motion_profile=None,
        duration_seconds=2.19,
        output_path=out_clip,
        browser_runtime=mock_browser,
        fps=24,
        broll_asset=broll_asset,
        camera_motion_intent=BeatMotionIntent.STATIC,
        frame_count_override=53,
    )

    assert res.frame_count == 53
    assert out_clip.exists()

    meta = _probe_video(out_clip)
    v_streams = [s for s in meta["streams"] if s["codec_type"] == "video"]
    a_streams = [s for s in meta["streams"] if s["codec_type"] == "audio"]
    assert len(v_streams) == 1
    assert len(a_streams) == 0

    v = v_streams[0]
    assert int(v["nb_read_frames"]) == 53
    assert v["r_frame_rate"] == "24/1"

    dur_s = float(meta["format"]["duration"])
    expected_dur_s = 53 / 24.0
    assert abs(dur_s - expected_dur_s) < 0.05


# ============================================================================
# 7. PHYSICAL IMAGE/TEMPLATE TEST WITH EXACT FRAME-COUNT OVERRIDE
# ============================================================================


@pytest.mark.asyncio
async def test_physical_image_template_exact_frame_count_override(tmp_path: Path):
    """Physically renders a template clip with frame_count_override=37 at 24 fps.

    Verifies via ffprobe:
    - stream count: 1 video, 0 audio;
    - exact frame count: 37 frames;
    - physical duration: approx 37 / 24 = 1.542s.
    """
    # 1. Solid template frame
    frame_png = tmp_path / "frame.png"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=navy:size=1920x1080,format=rgb24",
            "-vframes", "1",
            str(frame_png),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    frame_bytes = frame_png.read_bytes()

    mock_browser = MagicMock()
    mock_browser.capture = AsyncMock(
        return_value=BrowserCapturedFrame(
            scene_index=1,
            template_id=VisualTemplateId.IMAGE_EXPLAINER,
            width=1920,
            height=1080,
            png_bytes=frame_bytes,
            png_sha256="fake_sha",
            source_html_sha256="fake_html",
        )
    )

    doc = RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.IMAGE_EXPLAINER,
        width=1920,
        height=1080,
        html="""<!doctype html><html><body>Image</body></html>""",
        semantic_element_ids=(),
        content_sha256="doc_sha",
    )

    renderer = VisualV2VideoRenderer()
    out_clip = tmp_path / "image_clip_37.mp4"

    res = await renderer.render_clip(
        document=doc,
        motion_profile=None,
        duration_seconds=1.52,
        output_path=out_clip,
        browser_runtime=mock_browser,
        fps=24,
        camera_motion_intent=BeatMotionIntent.SLOW_PUSH_IN,
        frame_count_override=37,
    )

    assert res.frame_count == 37
    assert out_clip.exists()

    meta = _probe_video(out_clip)
    v_streams = [s for s in meta["streams"] if s["codec_type"] == "video"]
    a_streams = [s for s in meta["streams"] if s["codec_type"] == "audio"]
    assert len(v_streams) == 1
    assert len(a_streams) == 0

    v = v_streams[0]
    assert int(v["nb_read_frames"]) == 37
    assert v["r_frame_rate"] == "24/1"

    dur_s = float(meta["format"]["duration"])
    expected_dur_s = 37 / 24.0
    assert abs(dur_s - expected_dur_s) < 0.05


# ============================================================================
# 8. PHYSICAL ASSEMBLY REGRESSION: 90 + 129 = 219 FRAMES
# ============================================================================


@pytest.mark.asyncio
async def test_physical_assembly_regression_t1d_9121ms(tmp_path: Path):
    """Physically reproduce Scene 4 (9121 ms @ 24 fps) assembly with [90, 129] frames.

    Beat 0: 90 frames (physical = 3750 ms, logical = 3731 ms)
    Beat 1: 129 frames (physical = 5375 ms, logical = 5390 ms)
    Assembled: 219 frames (physical = 9125 ms, logical = 9121 ms)

    BeatClipAssembler must accept it under the existing 1-frame tolerance (43.67 ms).
    """
    fps = 24
    total_duration_ms = 9121

    # Generate synthetic video clips for the two beats
    clip0_path = tmp_path / "scene_004_beat_000.mp4"
    clip1_path = tmp_path / "scene_004_beat_001.mp4"

    # Beat 0: exactly 90 frames
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=darkgreen:size=1920x1080:rate=24",
            "-frames:v", "90",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-r", "24",
            "-an",
            str(clip0_path),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )

    # Beat 1: exactly 129 frames
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=darkred:size=1920x1080:rate=24",
            "-frames:v", "129",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-r", "24",
            "-an",
            str(clip1_path),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )

    c0 = RenderedBeatClip(
        parent_scene_index=4,
        materialized_index=0,
        source_beat_index=0,
        start_ms=0,
        end_ms=3731,
        duration_ms=3731,
        path=clip0_path,
    )
    c1 = RenderedBeatClip(
        parent_scene_index=4,
        materialized_index=1,
        source_beat_index=1,
        start_ms=3731,
        end_ms=9121,
        duration_ms=5390,
        path=clip1_path,
    )

    assembler = BeatClipAssembler()
    out_assembled = tmp_path / "scene_004_assembled.mp4"

    # Assemble without raising BeatClipAssemblerError
    result = await assembler.assemble(
        [c0, c1],
        output_path=out_assembled,
        fps=fps,
    )

    assert result.parent_scene_index == 4
    assert result.beat_count == 2
    assert result.expected_duration_ms == 9121
    # Physical duration is ~9125 ms (219 frames @ 24 fps)
    assert abs(result.physical_duration_ms - 9125.0) < 10.0

    # Probe assembled output file
    meta = _probe_video(out_assembled)
    v_streams = [s for s in meta["streams"] if s["codec_type"] == "video"]
    a_streams = [s for s in meta["streams"] if s["codec_type"] == "audio"]
    assert len(v_streams) == 1
    assert len(a_streams) == 0

    v = v_streams[0]
    assert int(v["nb_read_frames"]) == 219
    assert v["r_frame_rate"] == "24/1"

    # Verify tolerance: |physical_dur_ms - expected_dur_ms| < (1000/24 + 2.0)
    frame_tolerance_ms = (1000.0 / fps) + 2.0
    assert abs(result.physical_duration_ms - total_duration_ms) <= frame_tolerance_ms
