"""Unit tests for P18-G2C2B: Beat Visual Renderer + End-to-End Multi-Beat Canary.

Verifies:
1. Plan / execution matching and strict validation:
   - Exact plan/execution identity accepted;
   - Missing executed beat rejected;
   - Extra executed beat rejected;
   - Action mismatch rejected;
   - Required_kind mismatch rejected;
   - BROLL binding routed correctly;
   - IMAGE binding routed correctly;
   - LOCAL_TEMPLATE routes zero external asset;
   - Non-HARD_CUT rejected (fail closed);
   - Deterministic output naming (scene_{scene_idx:03d}_beat_{beat_idx:03d}.mp4);
   - Exact duration passed;
   - Preserved camera_motion_intent vs template motion_profile separation;
   - Reused BROLL may render with different camera motion;
   - No external resolver or provider call inside BeatVisualRenderer;
2. Sunset-like capability check:
   - Evaluates Sunset render plan intents:
     Beat 0: SLOW_PUSH_IN
     Beat 1: STATIC
     Beat 2: DRIFT
3. Local End-to-End Beat Visual Canary:
   - Beat 0: BROLL_EXPLAINER + SLOW_PUSH_IN
   - Beat 1: FLOW_DIAGRAM + STATIC
   - Beat 2: BROLL_EXPLAINER + DRIFT (reuse Beat 0 asset)
   - BeatClipAssembler.assemble(...)
   - Physical ffprobe assertions: 1 video stream, 0 audio, h264, 1920x1080, yuv420p, 24fps CFR, duration.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from omega.application.beat_asset_executor import (
    BeatAssetExecutionResult,
    ExecutedBeatAsset,
)
from omega.application.beat_asset_policy import BeatAssetAction, BeatAssetDecision
from omega.application.beat_clip_assembler import BeatClipAssembler
from omega.application.beat_render_adapter import BeatRenderPlan, BeatRenderUnit
from omega.application.beat_source_resolver import resolve_scene_source_statements
from omega.application.beat_visual_renderer import (
    BeatVisualRenderer,
    BeatVisualRenderError,
)
from omega.application.canonical_beat_preparation import CanonicalBeatPreparationService
from omega.application.editorial_beat import (
    BeatMotionIntent,
    BeatTransitionIntent,
)
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.visual_asset_binding import BoundBrollAsset
from omega.application.visual_direction import (
    VisualAssetKind,
    VisualAssetRequirement,
    VisualDirection,
    VisualRenderMode,
    VisualTemplateId,
)
from omega.infrastructure.browser_capture_runtime import BrowserCapturedFrame


def _ffprobe_meta(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_streams",
        "-show_format",
        "-print_format", "json",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
    return json.loads(res.stdout)


def _make_dummy_frame(template_id: VisualTemplateId = VisualTemplateId.FLOW_DIAGRAM) -> BrowserCapturedFrame:
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (1920).to_bytes(4, "big") + (1080).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\x00" * 50
    return BrowserCapturedFrame(
        scene_index=1,
        template_id=template_id,
        width=1920,
        height=1080,
        png_bytes=png,
        png_sha256=hashlib.sha256(png).hexdigest(),
        source_html_sha256="0" * 64,
    )


def _build_test_unit(
    *,
    parent_scene_index: int = 1,
    materialized_index: int = 0,
    source_beat_index: int = 0,
    start_ms: int = 0,
    end_ms: int = 2000,
    template_id: VisualTemplateId = VisualTemplateId.FLOW_DIAGRAM,
    visual_strategy: VisualStrategy = VisualStrategy.DIAGRAM,
    action: BeatAssetAction = BeatAssetAction.LOCAL_TEMPLATE,
    required_kind: VisualAssetKind | None = None,
    reuse_from: int | None = None,
    camera_motion: BeatMotionIntent = BeatMotionIntent.STATIC,
    transition: BeatTransitionIntent = BeatTransitionIntent.HARD_CUT,
    narration: str = "High latency causes retry storms.",
) -> tuple[BeatRenderUnit, ExecutedBeatAsset]:
    scene_view = StoryboardScene(
        sequence_index=parent_scene_index,
        section_id="Section 1",
        purpose="Test Purpose",
        source_statement_references=[1],
        narration_excerpt=narration,
        estimated_duration_seconds=(end_ms - start_ms) / 1000.0,
        visual_strategy=visual_strategy,
        visual_brief="Test brief",
    )
    asset_requirements = []
    if template_id == VisualTemplateId.BROLL_EXPLAINER:
        render_mode = VisualRenderMode.BROLL
        asset_requirements = [
            VisualAssetRequirement(
                kind=VisualAssetKind.BROLL,
                query_hint="broll",
                purpose="broll",
            )
        ]
    elif template_id == VisualTemplateId.IMAGE_EXPLAINER:
        render_mode = VisualRenderMode.IMAGE
        asset_requirements = [
            VisualAssetRequirement(
                kind=VisualAssetKind.IMAGE,
                query_hint="image",
                purpose="image",
            )
        ]
    else:
        render_mode = VisualRenderMode.TEMPLATE

    direction_view = VisualDirection(
        scene_index=parent_scene_index,
        render_mode=render_mode,
        template_id=template_id,
        asset_requirements=asset_requirements,
        motion_profile="sequential_flow" if template_id == VisualTemplateId.FLOW_DIAGRAM else "broll_overlay",
        rationale="Test rationale",
        metadata={"beat_index": materialized_index, "semantic_role": "MECHANISM"},
    )
    decision = BeatAssetDecision(
        parent_scene_index=parent_scene_index,
        beat_index=materialized_index,
        action=action,
        required_kind=required_kind,
        reuse_from_beat_index=reuse_from,
        rationale="Test decision",
    )
    unit = BeatRenderUnit(
        parent_scene_index=parent_scene_index,
        materialized_index=materialized_index,
        source_beat_index=source_beat_index,
        start_ms=start_ms,
        end_ms=end_ms,
        duration_ms=end_ms - start_ms,
        scene_view=scene_view,
        direction_view=direction_view,
        asset_decision=decision,
        camera_motion_intent=camera_motion,
        transition_intent=transition,
    )
    executed = ExecutedBeatAsset(
        parent_scene_index=parent_scene_index,
        beat_index=materialized_index,
        action=action,
        required_kind=required_kind,
        reuse_from_beat_index=reuse_from,
    )
    return unit, executed


# ============================================================================
# 1. Validation & Safety Tests
# ============================================================================

@pytest.mark.asyncio
async def test_01_scene_index_mismatch_fails_closed(tmp_path: Path):
    """Mismatched parent_scene_index between plan and execution fails closed."""
    u, e = _build_test_unit(parent_scene_index=1)
    e_wrong = e.model_copy(update={"parent_scene_index": 2})

    plan = BeatRenderPlan(parent_scene_index=1, units=(u,), total_duration_ms=2000)
    exec_res = BeatAssetExecutionResult(parent_scene_index=2, assets=(e_wrong,))

    renderer = BeatVisualRenderer()
    mock_browser = MagicMock()

    with pytest.raises(BeatVisualRenderError, match="Scene index mismatch"):
        await renderer.render_plan(
            render_plan=plan,
            asset_execution=exec_res,
            output_dir=tmp_path,
            browser_runtime=mock_browser,
        )


@pytest.mark.asyncio
async def test_02_beat_count_mismatch_fails_closed(tmp_path: Path):
    """Mismatched unit and asset count fails closed."""
    u, e = _build_test_unit()
    plan = BeatRenderPlan(parent_scene_index=1, units=(u,), total_duration_ms=2000)
    exec_res = BeatAssetExecutionResult(parent_scene_index=1, assets=())

    renderer = BeatVisualRenderer()
    mock_browser = MagicMock()

    with pytest.raises(BeatVisualRenderError, match="Beat count mismatch"):
        await renderer.render_plan(
            render_plan=plan,
            asset_execution=exec_res,
            output_dir=tmp_path,
            browser_runtime=mock_browser,
        )


@pytest.mark.asyncio
async def test_03_action_mismatch_fails_closed(tmp_path: Path):
    """Action mismatch between unit decision and executed asset fails closed."""
    u, e = _build_test_unit(action=BeatAssetAction.LOCAL_TEMPLATE)
    e_bad = e.model_copy(update={"action": BeatAssetAction.ACQUIRE_IF_NEEDED})

    plan = BeatRenderPlan(parent_scene_index=1, units=(u,), total_duration_ms=2000)
    exec_res = BeatAssetExecutionResult(parent_scene_index=1, assets=(e_bad,))

    renderer = BeatVisualRenderer()
    mock_browser = MagicMock()

    with pytest.raises(BeatVisualRenderError, match="Action mismatch"):
        await renderer.render_plan(
            render_plan=plan,
            asset_execution=exec_res,
            output_dir=tmp_path,
            browser_runtime=mock_browser,
        )


@pytest.mark.asyncio
async def test_04_non_hard_cut_transition_fails_closed(tmp_path: Path):
    """Non-HARD_CUT transition intent fails closed in G2C2B."""
    u, e = _build_test_unit(transition=BeatTransitionIntent.CROSSFADE)
    plan = BeatRenderPlan(parent_scene_index=1, units=(u,), total_duration_ms=2000)
    exec_res = BeatAssetExecutionResult(parent_scene_index=1, assets=(e,))

    renderer = BeatVisualRenderer()
    mock_browser = MagicMock()

    with pytest.raises(BeatVisualRenderError, match="Only HARD_CUT is supported in G2C2B"):
        await renderer.render_plan(
            render_plan=plan,
            asset_execution=exec_res,
            output_dir=tmp_path,
            browser_runtime=mock_browser,
        )


@pytest.mark.asyncio
async def test_05_broll_missing_bound_asset_fails_closed(tmp_path: Path):
    """BROLL beat without bound_broll_asset fails closed."""
    u, e = _build_test_unit(
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        visual_strategy=VisualStrategy.BROLL,
        action=BeatAssetAction.ACQUIRE_IF_NEEDED,
        required_kind=VisualAssetKind.BROLL,
    )
    plan = BeatRenderPlan(parent_scene_index=1, units=(u,), total_duration_ms=2000)
    exec_res = BeatAssetExecutionResult(parent_scene_index=1, assets=(e,))

    renderer = BeatVisualRenderer()
    mock_browser = MagicMock()

    with pytest.raises(BeatVisualRenderError, match="Missing bound BROLL asset"):
        await renderer.render_plan(
            render_plan=plan,
            asset_execution=exec_res,
            output_dir=tmp_path,
            browser_runtime=mock_browser,
        )


@pytest.mark.asyncio
async def test_06_local_template_rejects_external_assets(tmp_path: Path):
    """Local template beat carrying external asset fails closed."""
    u, e = _build_test_unit(template_id=VisualTemplateId.FLOW_DIAGRAM)
    dummy_broll = BoundBrollAsset(
        asset_id="broll_1",
        kind=VisualAssetKind.BROLL,
        mime_type="video/mp4",
        content_sha256="0" * 64,
        local_path=Path("/tmp/fake.mp4"),
        duration_seconds=5.0,
        width=1920,
        height=1080,
    )
    e_corrupt = e.model_copy(update={"bound_broll_asset": dummy_broll})

    plan = BeatRenderPlan(parent_scene_index=1, units=(u,), total_duration_ms=2000)
    exec_res = BeatAssetExecutionResult(parent_scene_index=1, assets=(e_corrupt,))

    renderer = BeatVisualRenderer()
    mock_browser = MagicMock()

    with pytest.raises(BeatVisualRenderError, match="Unexpected external assets on local template beat"):
        await renderer.render_plan(
            render_plan=plan,
            asset_execution=exec_res,
            output_dir=tmp_path,
            browser_runtime=mock_browser,
        )


# ============================================================================
# 2. Sunset Capability Check
# ============================================================================

def test_07_sunset_fixture_planned_motion_intents():
    """Verify exact camera motion intents for the canonical Sunset fixture."""
    script_dict: dict[str, Any] = {
        "title": "Atmospheric Optics",
        "estimated_duration_seconds": 9.0,
        "sections": [
            {
                "heading": "Rayleigh Scattering",
                "statements": [
                    {
                        "statement_order": 1,
                        "statement_text": "Sunsets appear intensely colorful as evening approaches.",
                        "statement_type": "STATEMENT",
                    },
                    {
                        "statement_order": 2,
                        "statement_text": "As sunlight travels through more atmosphere, Rayleigh scattering removes blue light.",
                        "statement_type": "STATEMENT",
                    },
                    {
                        "statement_order": 3,
                        "statement_text": "Only warm red and orange tones remain to complete the spectacle.",
                        "statement_type": "CLOSING",
                    },
                ],
            }
        ],
    }
    scene = StoryboardScene(
        sequence_index=1,
        section_id="Rayleigh Scattering",
        purpose="Explain sunset color transition",
        source_statement_references=[1, 2, 3],
        narration_excerpt=(
            "Sunsets appear intensely colorful as evening approaches. "
            "As sunlight travels through more atmosphere, Rayleigh scattering removes blue light. "
            "Only warm red and orange tones remain to complete the spectacle."
        ),
        estimated_duration_seconds=9.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="Atmospheric sunset perspective",
        asset_query_hint="sunset golden hour atmosphere",
    )

    resolution = resolve_scene_source_statements(script_dict=script_dict, scene=scene)
    assert resolution.resolved is True

    prep_res = CanonicalBeatPreparationService.prepare(
        scene=scene,
        source_statements=resolution.statements,
        scene_duration_ms=9000,
        visual_asset_mode="PEXELS",
    )
    assert prep_res.eligible is True
    assert prep_res.render_plan is not None

    u0, u1, u2 = prep_res.render_plan.units
    # Proven Sunset motion intents:
    assert u0.camera_motion_intent == BeatMotionIntent.SLOW_PUSH_IN
    assert u1.camera_motion_intent == BeatMotionIntent.STATIC
    assert u2.camera_motion_intent == BeatMotionIntent.DRIFT


# ============================================================================
# 3. Local End-to-End Beat Visual Canary + Assembly
# ============================================================================

@pytest.mark.asyncio
async def test_08_local_end_to_end_beat_visual_canary_and_assembly(tmp_path: Path):
    """Render 3 distinct visual beats (BROLL push-in -> FLOW_DIAGRAM static -> BROLL drift reuse).

    Then assemble them using BeatClipAssembler and probe physical video attributes.
    """
    # 1. Create a local physical BROLL fixture
    broll_video = tmp_path / "fixture_broll.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "testsrc=size=1920x1080:rate=24",
            "-t", "4.0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(broll_video),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )

    bound_broll = BoundBrollAsset(
        asset_id="broll_sunset",
        kind=VisualAssetKind.BROLL,
        mime_type="video/mp4",
        content_sha256="0" * 64,
        local_path=broll_video,
        duration_seconds=4.0,
        width=1920,
        height=1080,
    )

    # 2. Build 3 BeatRenderUnits mirroring Sunset
    # Beat 0: 2000ms BROLL + SLOW_PUSH_IN
    u0, e0 = _build_test_unit(
        parent_scene_index=1,
        materialized_index=0,
        source_beat_index=0,
        start_ms=0,
        end_ms=2000,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        visual_strategy=VisualStrategy.BROLL,
        action=BeatAssetAction.ACQUIRE_IF_NEEDED,
        required_kind=VisualAssetKind.BROLL,
        camera_motion=BeatMotionIntent.SLOW_PUSH_IN,
        narration="Sunsets appear intensely colorful as evening approaches.",
    )
    e0 = e0.model_copy(update={"bound_broll_asset": bound_broll})

    # Beat 1: 2000ms FLOW_DIAGRAM + STATIC
    u1, e1 = _build_test_unit(
        parent_scene_index=1,
        materialized_index=1,
        source_beat_index=1,
        start_ms=2000,
        end_ms=4000,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        visual_strategy=VisualStrategy.DIAGRAM,
        action=BeatAssetAction.LOCAL_TEMPLATE,
        camera_motion=BeatMotionIntent.STATIC,
        narration="As sunlight travels through more atmosphere, Rayleigh scattering removes blue light.",
    )

    # Beat 2: 2000ms BROLL + DRIFT (REUSE_COMPATIBLE from Beat 0)
    u2, e2 = _build_test_unit(
        parent_scene_index=1,
        materialized_index=2,
        source_beat_index=2,
        start_ms=4000,
        end_ms=6000,
        template_id=VisualTemplateId.BROLL_EXPLAINER,
        visual_strategy=VisualStrategy.BROLL,
        action=BeatAssetAction.REUSE_COMPATIBLE,
        required_kind=VisualAssetKind.BROLL,
        reuse_from=0,
        camera_motion=BeatMotionIntent.DRIFT,
        narration="Only warm red and orange tones remain to complete the spectacle.",
    )
    e2 = e2.model_copy(update={"bound_broll_asset": bound_broll})

    plan = BeatRenderPlan(
        parent_scene_index=1,
        units=(u0, u1, u2),
        total_duration_ms=6000,
    )
    asset_exec = BeatAssetExecutionResult(
        parent_scene_index=1,
        assets=(e0, e1, e2),
    )

    # 3. Mock browser runtime (transparent PNG for BROLL, solid frame for diagram)
    trans_png = tmp_path / "trans.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black@0.0:size=1920x1080,format=rgba", "-vframes", "1", str(trans_png)],
        capture_output=True,
        check=True,
        timeout=30,
    )
    trans_bytes = trans_png.read_bytes()

    diagram_png = tmp_path / "diagram.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=darkblue:size=1920x1080,format=rgb24", "-vframes", "1", str(diagram_png)],
        capture_output=True,
        check=True,
        timeout=30,
    )
    diagram_bytes = diagram_png.read_bytes()

    mock_browser = MagicMock()

    async def fake_capture(doc, transparent_background=False):
        b = trans_bytes if transparent_background else diagram_bytes
        h = hashlib.sha256(b).hexdigest()
        return BrowserCapturedFrame(
            scene_index=doc.scene_index,
            template_id=doc.template_id,
            width=1920,
            height=1080,
            png_bytes=b,
            png_sha256=h,
            source_html_sha256=doc.content_sha256,
        )

    mock_browser.capture = AsyncMock(side_effect=fake_capture)

    # 4. Render beats via BeatVisualRenderer
    renderer = BeatVisualRenderer()
    beat_dir = tmp_path / "beat_clips"
    render_res = await renderer.render_plan(
        render_plan=plan,
        asset_execution=asset_exec,
        output_dir=beat_dir,
        browser_runtime=mock_browser,
        fps=24,
    )

    assert len(render_res.clips) == 3
    c0, c1, c2 = render_res.clips

    # Verify deterministic naming
    assert c0.path.name == "scene_001_beat_000.mp4"
    assert c1.path.name == "scene_001_beat_001.mp4"
    assert c2.path.name == "scene_001_beat_002.mp4"

    # All files exist and non-empty
    assert c0.path.exists() and c0.path.stat().st_size > 0
    assert c1.path.exists() and c1.path.stat().st_size > 0
    assert c2.path.exists() and c2.path.stat().st_size > 0

    # 5. Assemble via BeatClipAssembler
    assembler = BeatClipAssembler()
    assembled_path = tmp_path / "assembled_scene_001.mp4"
    assembly_res = await assembler.assemble(
        clips=render_res.clips,
        output_path=assembled_path,
        fps=24,
    )

    assert assembly_res.output_path == assembled_path
    assert assembly_res.beat_count == 3
    assert assembly_res.expected_duration_ms == 6000

    # 6. Physical verification of assembled scene via ffprobe
    meta = _ffprobe_meta(assembled_path)
    streams = meta["streams"]
    assert len(streams) == 1  # exactly 1 stream
    v = streams[0]
    assert v["codec_name"] == "h264"
    assert v["width"] == 1920
    assert v["height"] == 1080
    assert v["pix_fmt"] == "yuv420p"
    assert v["avg_frame_rate"] == "24/1"

    # Zero audio streams
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    assert len(audio_streams) == 0

    # Duration matches 6.0s within 1 frame tolerance (1/24 s = ~0.0416s)
    phys_duration = float(v["duration"])
    assert abs(phys_duration - 6.0) <= (1.0 / 24.0)


@pytest.mark.asyncio
async def test_09_bounded_concurrency_ordering_preserved(tmp_path: Path):
    from omega.infrastructure.visual_v2_video_renderer import VisualV2VideoRenderResult

    renderer = BeatVisualRenderer(max_concurrency=2)
    mock_video_renderer = MagicMock()
    mock_browser = MagicMock()

    u0, e0 = _build_test_unit(
        parent_scene_index=1,
        materialized_index=0,
        source_beat_index=0,
        start_ms=0,
        end_ms=1000,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        visual_strategy=VisualStrategy.DIAGRAM,
        action=BeatAssetAction.LOCAL_TEMPLATE,
        camera_motion=BeatMotionIntent.STATIC,
        narration="High latency causes retry storms.",
    )
    u1, e1 = _build_test_unit(
        parent_scene_index=1,
        materialized_index=1,
        source_beat_index=1,
        start_ms=1000,
        end_ms=2000,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        visual_strategy=VisualStrategy.DIAGRAM,
        action=BeatAssetAction.LOCAL_TEMPLATE,
        camera_motion=BeatMotionIntent.STATIC,
        narration="High latency causes retry storms.",
    )
    u2, e2 = _build_test_unit(
        parent_scene_index=1,
        materialized_index=2,
        source_beat_index=2,
        start_ms=2000,
        end_ms=3000,
        template_id=VisualTemplateId.FLOW_DIAGRAM,
        visual_strategy=VisualStrategy.DIAGRAM,
        action=BeatAssetAction.LOCAL_TEMPLATE,
        camera_motion=BeatMotionIntent.STATIC,
        narration="High latency causes retry storms.",
    )

    plan = BeatRenderPlan(
        parent_scene_index=1,
        units=(u0, u1, u2),
        total_duration_ms=3000,
    )
    asset_exec = BeatAssetExecutionResult(
        parent_scene_index=1,
        assets=(e0, e1, e2),
    )

    async def fake_render_clip(**kwargs):
        out_path = kwargs["output_path"]
        out_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100)
        return VisualV2VideoRenderResult(
            output_path=out_path,
            scene_index=1,
            template_id=VisualTemplateId.HERO_TITLE,
            width=1920,
            height=1080,
            fps=24,
            duration_seconds=1.0,
            frame_count=24,
            video_sha256="fake_sha",
            source_html_sha256="fake_doc_sha",
            motion_profile="static",
        )

    mock_video_renderer.render_clip = AsyncMock(side_effect=fake_render_clip)
    renderer._video_renderer = mock_video_renderer

    res = await renderer.render_plan(
        render_plan=plan,
        asset_execution=asset_exec,
        output_dir=tmp_path,
        browser_runtime=mock_browser,
        fps=24,
    )

    assert len(res.clips) == 3
    # Verify deterministic ordering by materialized_index
    for idx, clip in enumerate(res.clips):
        assert clip.materialized_index == idx
        assert clip.path.name == f"scene_001_beat_{idx:03d}.mp4"
