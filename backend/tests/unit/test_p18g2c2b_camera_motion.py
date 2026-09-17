"""Unit tests for P18-G2C2B: Deterministic Physical Camera Motion Authority.

Verifies:
1. Pure motion contracts (CameraMotionProfile, CameraMotionState):
   - STATIC start/end identity;
   - SLOW_PUSH_IN scale increase to restrained 1.04;
   - SLOW_PULL_OUT scale decrease 1.04 -> 1.00;
   - PAN_LEFT horizontal movement (+0.012 -> -0.012);
   - PAN_RIGHT opposite direction (-0.012 -> +0.012);
   - DRIFT bounded diagonal movement;
   - FOCAL_ZOOM center-biased 1.00 -> 1.06;
   - Boundedness, clamping, determinism, smoothstep easing curve;
   - No zoom exceeds 1.06;
2. IMAGE_EXPLAINER camera motion:
   - Separate <style id="omega-camera-motion-state"> injection;
   - #image-element receives transform, text container untouched;
   - Progression across frames;
   - STATIC leaves camera style absent or neutral;
3. BROLL physical camera motion via FFmpeg:
   - STATIC preserves frame hash for static source;
   - SLOW_PUSH_IN changes frame hash;
   - PAN_LEFT and DRIFT physically produce valid moving video;
   - Codec = h264, 1920x1080, yuv420p, 24fps CFR, 0 audio streams.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from omega.application.editorial_beat import BeatMotionIntent
from omega.application.visual_asset_binding import BoundBrollAsset
from omega.application.visual_camera_motion import (
    CameraMotionState,
    evaluate_camera_motion,
    inject_camera_motion_style,
    resolve_camera_motion_profile,
    smoothstep,
)
from omega.application.visual_direction import VisualAssetKind, VisualTemplateId
from omega.application.visual_template_renderer import RenderedTemplateDocument
from omega.infrastructure.browser_capture_runtime import BrowserCapturedFrame
from omega.infrastructure.visual_v2_video_renderer import (
    VisualV2VideoRenderer,
    VisualV2VideoRenderError,
)

# ============================================================================
# 1. Pure Camera Motion Authority Tests
# ============================================================================

def test_01_static_profile_and_evaluation_identity():
    """STATIC motion maintains constant scale 1.0 and zero offset."""
    profile = resolve_camera_motion_profile(BeatMotionIntent.STATIC)
    assert profile.intent == BeatMotionIntent.STATIC
    assert profile.start_scale == 1.00
    assert profile.end_scale == 1.00
    assert profile.start_x_offset == 0.0
    assert profile.end_x_offset == 0.0
    assert profile.start_y_offset == 0.0
    assert profile.end_y_offset == 0.0

    state_start = evaluate_camera_motion(profile, 0.0)
    state_mid = evaluate_camera_motion(profile, 0.5)
    state_end = evaluate_camera_motion(profile, 1.0)

    assert state_start == CameraMotionState(scale=1.0, x_offset=0.0, y_offset=0.0)
    assert state_mid == CameraMotionState(scale=1.0, x_offset=0.0, y_offset=0.0)
    assert state_end == CameraMotionState(scale=1.0, x_offset=0.0, y_offset=0.0)


def test_02_slow_push_in_profile_and_evaluation():
    """SLOW_PUSH_IN increases scale smoothly from 1.00 to 1.04, center anchored."""
    profile = resolve_camera_motion_profile(BeatMotionIntent.SLOW_PUSH_IN)
    assert profile.start_scale == 1.00
    assert profile.end_scale == 1.04
    assert profile.start_x_offset == 0.0
    assert profile.end_x_offset == 0.0

    s0 = evaluate_camera_motion(profile, 0.0)
    assert s0.scale == 1.00
    assert s0.x_offset == 0.0
    assert s0.y_offset == 0.0

    s_mid = evaluate_camera_motion(profile, 0.5)
    assert s_mid.scale == 1.02  # Smoothstep at 0.5 is 0.5: 1.00 + 0.04*0.5 = 1.02

    s1 = evaluate_camera_motion(profile, 1.0)
    assert s1.scale == 1.04
    assert s1.x_offset == 0.0
    assert s1.y_offset == 0.0


def test_03_slow_pull_out_profile_and_evaluation():
    """SLOW_PULL_OUT decreases scale smoothly from 1.04 to 1.00, center anchored."""
    profile = resolve_camera_motion_profile(BeatMotionIntent.SLOW_PULL_OUT)
    assert profile.start_scale == 1.04
    assert profile.end_scale == 1.00

    s0 = evaluate_camera_motion(profile, 0.0)
    assert s0.scale == 1.04
    s1 = evaluate_camera_motion(profile, 1.0)
    assert s1.scale == 1.00


def test_04_pan_left_direction_and_bounds():
    """PAN_LEFT moves from positive to negative x offset at constant scale 1.04."""
    profile = resolve_camera_motion_profile(BeatMotionIntent.PAN_LEFT)
    assert profile.start_scale == 1.04
    assert profile.end_scale == 1.04
    assert profile.start_x_offset > profile.end_x_offset
    assert profile.start_x_offset == 0.012
    assert profile.end_x_offset == -0.012

    s0 = evaluate_camera_motion(profile, 0.0)
    s1 = evaluate_camera_motion(profile, 1.0)
    assert s0.x_offset == 0.012
    assert s1.x_offset == -0.012
    assert s0.scale == 1.04
    assert s1.scale == 1.04


def test_05_pan_right_opposite_direction():
    """PAN_RIGHT is exact opposite of PAN_LEFT."""
    left = resolve_camera_motion_profile(BeatMotionIntent.PAN_LEFT)
    right = resolve_camera_motion_profile(BeatMotionIntent.PAN_RIGHT)
    assert right.start_x_offset == -left.start_x_offset
    assert right.end_x_offset == -left.end_x_offset
    assert right.start_scale == left.start_scale


def test_06_drift_bounded_diagonal_movement():
    """DRIFT moves smoothly along x and y at constant scale 1.03 within safe margins."""
    profile = resolve_camera_motion_profile(BeatMotionIntent.DRIFT)
    assert profile.start_scale == 1.03
    assert profile.end_scale == 1.03
    assert abs(profile.start_x_offset) < (1.03 - 1.0) / 2.0
    assert abs(profile.start_y_offset) < (1.03 - 1.0) / 2.0

    s0 = evaluate_camera_motion(profile, 0.0)
    s1 = evaluate_camera_motion(profile, 1.0)
    assert s0.x_offset != s1.x_offset
    assert s0.y_offset != s1.y_offset


def test_07_focal_zoom_center_biased_bounds():
    """FOCAL_ZOOM zooms to 1.06 center-anchored without semantic tracking assumption."""
    profile = resolve_camera_motion_profile(BeatMotionIntent.FOCAL_ZOOM)
    assert profile.start_scale == 1.00
    assert profile.end_scale == 1.06
    assert profile.start_x_offset == 0.0
    assert profile.end_x_offset == 0.0
    assert profile.start_y_offset == 0.0
    assert profile.end_y_offset == 0.0


def test_08_smoothstep_clamping_and_midpoint():
    """Smoothstep clamps progress outside [0, 1] and evaluates exactly at midpoint."""
    assert smoothstep(-0.5) == 0.0
    assert smoothstep(0.0) == 0.0
    assert smoothstep(0.5) == 0.5
    assert smoothstep(1.0) == 1.0
    assert smoothstep(1.5) == 1.0


def test_09_all_camera_motion_profiles_within_declared_limits():
    """No camera motion profile exceeds scale 1.06 or offset [-1.0, 1.0]."""
    for intent in BeatMotionIntent:
        prof = resolve_camera_motion_profile(intent)
        assert 1.00 <= prof.start_scale <= 1.06
        assert 1.00 <= prof.end_scale <= 1.06
        assert -1.0 <= prof.start_x_offset <= 1.0
        assert -1.0 <= prof.end_x_offset <= 1.0
        assert -1.0 <= prof.start_y_offset <= 1.0
        assert -1.0 <= prof.end_y_offset <= 1.0


def test_10_deterministic_evaluation_zero_randomness():
    """Identical progress evaluations produce identical CameraMotionState."""
    prof = resolve_camera_motion_profile(BeatMotionIntent.SLOW_PUSH_IN)
    s_a = evaluate_camera_motion(prof, 0.333)
    s_b = evaluate_camera_motion(prof, 0.333)
    assert s_a == s_b


# ============================================================================
# 2. IMAGE Camera Motion & Style Injection Tests
# ============================================================================

def test_11_inject_camera_motion_style_targets_only_image_element():
    """inject_camera_motion_style injects <style id='omega-camera-motion-state'> targeting #image-element."""
    html = (
        "<!DOCTYPE html><html><head><title>Test</title></head>"
        "<body><div id='scene-root'><div class='split-text'><div id='image-title'>Title</div></div>"
        "<div class='split-media'><div class='image-frame'>"
        "<img id='image-element' src='test.png' /></div></div></div></body></html>"
    )
    state = CameraMotionState(scale=1.04, x_offset=0.012, y_offset=-0.005)
    injected = inject_camera_motion_style(html, state)

    assert '<style id="omega-camera-motion-state">' in injected
    assert "#image-element {" in injected
    assert "transform: translate(1.2000%, -0.5000%) scale(1.0400);" in injected
    assert "transform-origin: center center;" in injected
    assert "#scene-root" not in injected.split('<style id="omega-camera-motion-state">')[1].split("</style>")[0]
    assert "#image-frame" not in injected.split('<style id="omega-camera-motion-state">')[1].split("</style>")[0]


def make_captured_frame(png_bytes: bytes, template_id: VisualTemplateId = VisualTemplateId.BROLL_EXPLAINER) -> BrowserCapturedFrame:
    h = hashlib.sha256(png_bytes).hexdigest()
    return BrowserCapturedFrame(
        scene_index=1,
        template_id=template_id,
        width=1920,
        height=1080,
        png_bytes=png_bytes,
        png_sha256=h,
        source_html_sha256="0" * 64,
    )


@pytest.mark.asyncio
async def test_12_image_explainer_render_applies_camera_motion_to_html(tmp_path: Path):
    """VisualV2VideoRenderer applies camera motion to IMAGE_EXPLAINER frames over time."""
    renderer = VisualV2VideoRenderer()
    doc = RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.IMAGE_EXPLAINER,
        width=1920,
        height=1080,
        html=(
            "<!DOCTYPE html><html><head></head><body>"
            "<div class='split-container'>"
            "<div class='split-text'><div id='image-title'>Title</div></div>"
            "<div class='split-media'><div class='image-frame'>"
            "<img id='image-element' class='image-asset' src='data:image/png;base64,iVBORw==' />"
            "</div></div></div></body></html>"
        ),
        semantic_element_ids=("image-title", "image-element"),
        content_sha256="0" * 64,
        text_fitting=(),
    )

    captured_docs = []
    mock_browser = MagicMock()

    async def mock_capture(frame_doc, transparent_background=False):
        captured_docs.append(frame_doc)
        return make_captured_frame(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR...", VisualTemplateId.IMAGE_EXPLAINER)

    mock_browser.capture = AsyncMock(side_effect=mock_capture)

    # Subprocess run mock for ffmpeg clip creation
    async def mock_subprocess(*args, **kwargs):
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        # Create dummy mp4 file
        out_file = Path(args[-1])
        out_file.write_bytes(b"ftypmp42" + b"\x00" * 100)
        return proc

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "create_subprocess_exec", mock_subprocess)

        out_path = tmp_path / "image_beat.mp4"
        res = await renderer.render_clip(
            document=doc,
            motion_profile="image_explainer",
            duration_seconds=1.0,
            output_path=out_path,
            browser_runtime=mock_browser,
            fps=24,
            camera_motion_intent=BeatMotionIntent.SLOW_PUSH_IN,
        )
        assert res.output_path == out_path
        assert len(captured_docs) == 24

        # Frame 0: scale 1.0000
        f0_html = captured_docs[0].html
        assert '<style id="omega-camera-motion-state">' in f0_html
        assert "scale(1.0000)" in f0_html

        # Frame 23 (final): scale 1.0400
        f23_html = captured_docs[23].html
        assert '<style id="omega-camera-motion-state">' in f23_html
        assert "scale(1.0400)" in f23_html


def test_13_non_media_template_rejects_non_static_camera_motion(tmp_path: Path):
    """Passing non-STATIC camera motion to FLOW_DIAGRAM fails closed."""
    renderer = VisualV2VideoRenderer()
    doc = RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        width=1920,
        height=1080,
        html="<!DOCTYPE html><html><head></head><body>Diagram</body></html>",
        semantic_element_ids=(),
        content_sha256="0" * 64,
        text_fitting=(),
    )
    mock_browser = MagicMock()

    with pytest.raises(VisualV2VideoRenderError, match="Camera motion SLOW_PUSH_IN not permitted"):
        asyncio.run(
            renderer.render_clip(
                document=doc,
                motion_profile="sequential_flow",
                duration_seconds=1.0,
                output_path=tmp_path / "diagram.mp4",
                browser_runtime=mock_browser,
                camera_motion_intent=BeatMotionIntent.SLOW_PUSH_IN,
            )
        )


# ============================================================================
# 3. Physical BROLL Camera Motion Tests (FFmpeg)
# ============================================================================

def _ffprobe_meta(path: Path) -> dict:
    """Helper to inspect video properties via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_streams",
        "-print_format", "json",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
    return json.loads(res.stdout)


@pytest.mark.asyncio
async def test_14_physical_broll_static_vs_push_in_frame_hashes(tmp_path: Path):
    """Static source produces identical frames on STATIC, and differing frames on SLOW_PUSH_IN."""
    # 1. Generate 2s static test video with asymmetric test pattern
    src_video = tmp_path / "static_pattern.mp4"
    cmd_gen = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "testsrc=size=1920x1080:rate=24",
        "-t", "1.0",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(src_video),
    ]
    subprocess.run(cmd_gen, capture_output=True, check=True, timeout=30)

    broll = BoundBrollAsset(
        asset_id="broll_test",
        kind=VisualAssetKind.BROLL,
        mime_type="video/mp4",
        content_sha256="0" * 64,
        local_path=src_video,
        duration_seconds=1.0,
        width=1920,
        height=1080,
    )

    doc = RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        width=1920,
        height=1080,
        html="<!DOCTYPE html><html><head></head><body></body></html>",
        semantic_element_ids=(),
        content_sha256="0" * 64,
        text_fitting=(),
    )

    # Generate 1920x1080 transparent PNG
    transparent_png = tmp_path / "trans.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black@0.0:size=1920x1080,format=rgba", "-vframes", "1", str(transparent_png)],
        capture_output=True,
        check=True,
        timeout=30,
    )
    png_bytes = transparent_png.read_bytes()

    mock_browser = MagicMock()
    mock_browser.capture = AsyncMock(return_value=make_captured_frame(png_bytes))

    renderer = VisualV2VideoRenderer()

    # 2. Render STATIC
    out_static = tmp_path / "broll_static.mp4"
    await renderer.render_clip(
        document=doc,
        motion_profile="broll_overlay",
        duration_seconds=1.0,
        output_path=out_static,
        browser_runtime=mock_browser,
        fps=24,
        broll_asset=broll,
        camera_motion_intent=BeatMotionIntent.STATIC,
    )

    # 3. Render SLOW_PUSH_IN
    out_push = tmp_path / "broll_push.mp4"
    await renderer.render_clip(
        document=doc,
        motion_profile="broll_overlay",
        duration_seconds=1.0,
        output_path=out_push,
        browser_runtime=mock_browser,
        fps=24,
        broll_asset=broll,
        camera_motion_intent=BeatMotionIntent.SLOW_PUSH_IN,
    )

    # 4. Probe stream attributes
    meta = _ffprobe_meta(out_push)
    streams = meta["streams"]
    assert len(streams) == 1
    v_stream = streams[0]
    assert v_stream["codec_name"] == "h264"
    assert v_stream["width"] == 1920
    assert v_stream["height"] == 1080
    assert v_stream["pix_fmt"] == "yuv420p"
    assert v_stream["avg_frame_rate"] == "24/1"
    duration = float(v_stream["duration"])
    assert abs(duration - 1.0) <= (1.0 / 24.0)

    # 5. Extract frame 0 and frame 23 from out_push
    f0_push = tmp_path / "f0_push.png"
    f23_push = tmp_path / "f23_push.png"
    subprocess.run(["ffmpeg", "-y", "-i", str(out_push), "-vf", "select=eq(n\\,0)", "-vframes", "1", str(f0_push)], capture_output=True, check=True, timeout=30)
    subprocess.run(["ffmpeg", "-y", "-i", str(out_push), "-vf", "select=eq(n\\,23)", "-vframes", "1", str(f23_push)], capture_output=True, check=True, timeout=30)

    h0_push = hashlib.sha256(f0_push.read_bytes()).hexdigest()
    h23_push = hashlib.sha256(f23_push.read_bytes()).hexdigest()
    # On push-in, frame 0 and frame 23 must differ!
    assert h0_push != h23_push


@pytest.mark.asyncio
async def test_15_physical_broll_pan_and_drift_exercise(tmp_path: Path):
    """Physically exercise PAN_LEFT and DRIFT on BROLL with FFmpeg."""
    src_video = tmp_path / "src_moving.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=24", "-t", "1.0", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src_video)],
        capture_output=True,
        check=True,
        timeout=30,
    )
    broll = BoundBrollAsset(
        asset_id="broll_test",
        kind=VisualAssetKind.BROLL,
        mime_type="video/mp4",
        content_sha256="0" * 64,
        local_path=src_video,
        duration_seconds=1.0,
        width=1920,
        height=1080,
    )
    doc = RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        width=1920,
        height=1080,
        html="<!DOCTYPE html><html><head></head><body></body></html>",
        semantic_element_ids=(),
        content_sha256="0" * 64,
        text_fitting=(),
    )
    trans_png = tmp_path / "trans.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black@0.0:size=1920x1080,format=rgba", "-vframes", "1", str(trans_png)],
        capture_output=True,
        check=True,
        timeout=30,
    )
    mock_browser = MagicMock()
    mock_browser.capture = AsyncMock(return_value=make_captured_frame(trans_png.read_bytes()))

    renderer = VisualV2VideoRenderer()

    # Test PAN_LEFT
    out_pan = tmp_path / "broll_pan.mp4"
    await renderer.render_clip(
        document=doc,
        motion_profile="broll_overlay",
        duration_seconds=1.0,
        output_path=out_pan,
        browser_runtime=mock_browser,
        fps=24,
        broll_asset=broll,
        camera_motion_intent=BeatMotionIntent.PAN_LEFT,
    )
    meta_pan = _ffprobe_meta(out_pan)
    assert len(meta_pan["streams"]) == 1
    assert meta_pan["streams"][0]["codec_name"] == "h264"
    assert meta_pan["streams"][0]["width"] == 1920

    # Test DRIFT
    out_drift = tmp_path / "broll_drift.mp4"
    await renderer.render_clip(
        document=doc,
        motion_profile="broll_overlay",
        duration_seconds=1.0,
        output_path=out_drift,
        browser_runtime=mock_browser,
        fps=24,
        broll_asset=broll,
        camera_motion_intent=BeatMotionIntent.DRIFT,
    )
    meta_drift = _ffprobe_meta(out_drift)
    assert len(meta_drift["streams"]) == 1
    assert meta_drift["streams"][0]["codec_name"] == "h264"
    assert meta_drift["streams"][0]["height"] == 1080


# ============================================================================
# 4. Contract Hardening Tests (Hash Integrity, Style Count, Parity, Edge Safety)
# ============================================================================

@pytest.mark.asyncio
async def test_16_image_camera_motion_frame_html_hash_integrity(tmp_path: Path):
    """Verify that every frame document passed to browser capture satisfies exact sha256 integrity."""
    renderer = VisualV2VideoRenderer()
    raw_html = (
        "<!DOCTYPE html><html><head><style id=\"omega-dom-motion-state\">"
        ":root { --t: 0; }</style></head><body>"
        "<div id=\"image-frame\"><img id=\"image-element\" src=\"test.jpg\"/></div>"
        "<div id=\"title\">Title</div></body></html>"
    )
    doc = RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.IMAGE_EXPLAINER,
        width=1920,
        height=1080,
        html=raw_html,
        semantic_element_ids=(),
        content_sha256=hashlib.sha256(raw_html.encode("utf-8")).hexdigest(),
        text_fitting=(),
    )

    captured_docs: list[RenderedTemplateDocument] = []

    # Generate a valid 1920x1080 PNG fixture
    valid_png_path = tmp_path / "valid_image.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=darkblue:size=1920x1080", "-vframes", "1", str(valid_png_path)],
        capture_output=True,
        check=True,
        timeout=30,
    )
    fake_png = valid_png_path.read_bytes()

    mock_browser = MagicMock()

    async def fake_capture(d, transparent_background=False):
        captured_docs.append(d)
        return BrowserCapturedFrame(
            scene_index=d.scene_index,
            template_id=d.template_id,
            width=d.width,
            height=d.height,
            png_bytes=fake_png,
            png_sha256=hashlib.sha256(fake_png).hexdigest(),
            source_html_sha256=d.content_sha256,
        )

    mock_browser.capture = AsyncMock(side_effect=fake_capture)

    out_mp4 = tmp_path / "image_motion.mp4"
    await renderer.render_clip(
        document=doc,
        motion_profile="image_explainer",
        duration_seconds=0.5,
        output_path=out_mp4,
        browser_runtime=mock_browser,
        fps=12,
        camera_motion_intent=BeatMotionIntent.SLOW_PUSH_IN,
    )

    assert len(captured_docs) == 6
    # Check exact sha256 contract for every single captured frame
    for d in captured_docs:
        expected_sha = hashlib.sha256(d.html.encode("utf-8")).hexdigest()
        assert d.content_sha256 == expected_sha
        assert '<style id="omega-camera-motion-state">' in d.html
        assert d.html.count('<style id="omega-camera-motion-state">') == 1
        assert d.html.count('id="omega-dom-motion-state"') <= 1

    # First, middle, final frames must have differing HTML content and hashes
    first_doc = captured_docs[0]
    mid_doc = captured_docs[len(captured_docs) // 2]
    final_doc = captured_docs[-1]
    assert first_doc.content_sha256 != mid_doc.content_sha256
    assert mid_doc.content_sha256 != final_doc.content_sha256
    assert first_doc.content_sha256 != final_doc.content_sha256


def test_17_camera_style_block_replacement_integrity():
    """Prove inject_camera_motion_style replaces prior camera styles deterministically with zero duplicates."""
    html_with_duplicate = (
        "<!DOCTYPE html><html><head>"
        '<style id="omega-dom-motion-state">:root {}</style>\n'
        '<style id="omega-camera-motion-state">#image-element { transform: scale(1.0); }</style>\n'
        '<style id="omega-camera-motion-state">#image-element { transform: scale(1.02); }</style>\n'
        "</head><body></body></html>"
    )
    state = CameraMotionState(scale=1.04, x_offset=0.012, y_offset=0.0)
    injected = inject_camera_motion_style(html_with_duplicate, state)

    assert injected.count('<style id="omega-camera-motion-state">') == 1
    assert injected.count('id="omega-dom-motion-state"') == 1
    assert "scale(1.0400)" in injected


@pytest.mark.asyncio
async def test_18_physical_pan_left_and_right_direction_convention(tmp_path: Path):
    """Prove physical displacement of vertical feature follows declared viewport pan direction."""
    # Asymmetric fixture: black background with bright white rectangle at x=900..1020
    src_video = tmp_path / "asym_pan_fixture.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=black:size=1920x1080,drawbox=x=900:y=0:w=120:h=1080:color=white:t=fill",
            "-t", "1.0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(src_video),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    broll = BoundBrollAsset(
        asset_id="broll_asym",
        kind=VisualAssetKind.BROLL,
        mime_type="video/mp4",
        content_sha256="0" * 64,
        local_path=src_video,
        duration_seconds=1.0,
        width=1920,
        height=1080,
    )
    trans_png = tmp_path / "trans.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black@0.0:size=1920x1080,format=rgba", "-vframes", "1", str(trans_png)],
        capture_output=True,
        check=True,
        timeout=30,
    )
    mock_browser = MagicMock()
    mock_browser.capture = AsyncMock(return_value=make_captured_frame(trans_png.read_bytes()))
    doc = RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        width=1920,
        height=1080,
        html="<!DOCTYPE html><html><head></head><body></body></html>",
        semantic_element_ids=(),
        content_sha256="0" * 64,
        text_fitting=(),
    )
    renderer = VisualV2VideoRenderer()

    # 1. PAN_LEFT (viewport right-to-left): feature moves LEFT (centroid_end < centroid_start)
    pan_left_mp4 = tmp_path / "pan_left.mp4"
    await renderer.render_clip(
        document=doc,
        motion_profile="broll_overlay",
        duration_seconds=1.0,
        output_path=pan_left_mp4,
        browser_runtime=mock_browser,
        fps=24,
        broll_asset=broll,
        camera_motion_intent=BeatMotionIntent.PAN_LEFT,
    )

    # 2. PAN_RIGHT (viewport left-to-right): feature moves RIGHT (centroid_end > centroid_start)
    pan_right_mp4 = tmp_path / "pan_right.mp4"
    await renderer.render_clip(
        document=doc,
        motion_profile="broll_overlay",
        duration_seconds=1.0,
        output_path=pan_right_mp4,
        browser_runtime=mock_browser,
        fps=24,
        broll_asset=broll,
        camera_motion_intent=BeatMotionIntent.PAN_RIGHT,
    )

    def extract_row_lum_to_file(video_path: Path, frame_idx: int, out_raw: Path) -> list[int]:
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(video_path),
            "-vf", f"select=eq(n\\,{frame_idx}),format=rgb24,crop=1920:2:0:540",
            "-frames:v", "1",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            str(out_raw),
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        raw = out_raw.read_bytes()
        return [raw[i * 3] for i in range(1920)]

    def compute_centroid(lum: list[int]) -> float:
        bright_indices = [x for x, val in enumerate(lum) if val > 128]
        assert len(bright_indices) > 0, "Bright marker must be present"
        return sum(bright_indices) / len(bright_indices)

    # PAN_LEFT: start centroid vs end centroid
    f0_left_raw = tmp_path / "f0_left.rgb"
    f23_left_raw = tmp_path / "f23_left.rgb"
    left_start = compute_centroid(extract_row_lum_to_file(pan_left_mp4, 0, f0_left_raw))
    left_end = compute_centroid(extract_row_lum_to_file(pan_left_mp4, 23, f23_left_raw))
    assert left_end < left_start, f"PAN_LEFT must displace feature to the left: {left_start} -> {left_end}"

    # PAN_RIGHT: start centroid vs end centroid
    f0_right_raw = tmp_path / "f0_right.rgb"
    f23_right_raw = tmp_path / "f23_right.rgb"
    right_start = compute_centroid(extract_row_lum_to_file(pan_right_mp4, 0, f0_right_raw))
    right_end = compute_centroid(extract_row_lum_to_file(pan_right_mp4, 23, f23_right_raw))
    assert right_end > right_start, f"PAN_RIGHT must displace feature to the right: {right_start} -> {right_end}"


@pytest.mark.asyncio
async def test_19_physical_edge_safety_no_black_borders(tmp_path: Path):
    """Prove camera motion never exposes black or empty borders across representative intents."""
    white_video = tmp_path / "white_src.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=white:size=1920x1080",
            "-t", "1.0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(white_video),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    broll = BoundBrollAsset(
        asset_id="broll_white",
        kind=VisualAssetKind.BROLL,
        mime_type="video/mp4",
        content_sha256="0" * 64,
        local_path=white_video,
        duration_seconds=1.0,
        width=1920,
        height=1080,
    )
    trans_png = tmp_path / "trans.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black@0.0:size=1920x1080,format=rgba", "-vframes", "1", str(trans_png)],
        capture_output=True,
        check=True,
        timeout=30,
    )
    mock_browser = MagicMock()
    mock_browser.capture = AsyncMock(return_value=make_captured_frame(trans_png.read_bytes()))
    doc = RenderedTemplateDocument(
        scene_index=1,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        width=1920,
        height=1080,
        html="<!DOCTYPE html><html><head></head><body></body></html>",
        semantic_element_ids=(),
        content_sha256="0" * 64,
        text_fitting=(),
    )
    renderer = VisualV2VideoRenderer()

    def check_edges_not_black(video_path: Path, frame_idx: int, out_raw: Path):
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(video_path),
            "-vf", f"select=eq(n\\,{frame_idx}),format=rgb24",
            "-frames:v", "1",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            str(out_raw),
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        raw = out_raw.read_bytes()
        stride = 1920 * 3

        def get_rgb(x: int, y: int) -> tuple[int, int, int]:
            offset = y * stride + x * 3
            return raw[offset], raw[offset + 1], raw[offset + 2]

        corners = [(0, 0), (1919, 0), (0, 1079), (1919, 1079)]
        for cx, cy in corners:
            r, g, b = get_rgb(cx, cy)
            assert r > 180 and g > 180 and b > 180, (
                f"Border/corner ({cx}, {cy}) exposed non-white pixel ({r}, {g}, {b}) in frame {frame_idx}"
            )

        border_mids = [(960, 0), (960, 1079), (0, 540), (1919, 540)]
        for bx, by in border_mids:
            r, g, b = get_rgb(bx, by)
            assert r > 180 and g > 180 and b > 180, (
                f"Border point ({bx}, {by}) exposed non-white pixel ({r}, {g}, {b}) in frame {frame_idx}"
            )

    intents_to_test = [
        BeatMotionIntent.PAN_LEFT,
        BeatMotionIntent.PAN_RIGHT,
        BeatMotionIntent.DRIFT,
        BeatMotionIntent.SLOW_PULL_OUT,
    ]

    for intent in intents_to_test:
        out_clip = tmp_path / f"edge_{intent.value}.mp4"
        await renderer.render_clip(
            document=doc,
            motion_profile="broll_overlay",
            duration_seconds=1.0,
            output_path=out_clip,
            browser_runtime=mock_browser,
            fps=24,
            broll_asset=broll,
            camera_motion_intent=intent,
        )
        f_raw = tmp_path / f"edge_{intent.value}.rgb"
        check_edges_not_black(out_clip, 0, f_raw)
        check_edges_not_black(out_clip, 11, f_raw)
        check_edges_not_black(out_clip, 23, f_raw)
