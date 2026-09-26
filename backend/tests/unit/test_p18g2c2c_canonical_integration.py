from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from omega.application.beat_asset_policy import BeatAssetAction
from omega.application.editorial_beat import BeatMotionIntent, BeatTransitionIntent
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.visual_direction import VisualAssetKind, VisualTemplateId
from omega.application.visual_production_v2_service import (
    CANONICAL_RENDER_SEMANTICS_VERSION,
    SUBTITLE_SEMANTICS_VERSION,
    VerticalSliceError,
    VerticalSliceRuntimeBeatVisual,
    VisualProductionV2Service,
)
from omega.domain.production import LicenseStatus


def _value(value):
    return SimpleNamespace(value=value)


def _scene():
    return StoryboardScene(
        sequence_index=1,
        section_id="section-1",
        purpose="Explain",
        source_statement_references=[1, 2, 3],
        narration_excerpt="A mechanism has three visible beats.",
        estimated_duration_seconds=1.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="Show the mechanism",
        asset_query_hint="mechanism factory",
    )


def _legacy_scene():
    scene = _scene()
    return scene.model_copy(
        update={
            "visual_strategy": VisualStrategy.TITLE_MOTION,
            "asset_query_hint": None,
        }
    )


def _plan(count=3):
    units = []
    spans = [(0, 300), (300, 700), (700, 1000)][:count]
    if count == 1:
        spans = [(0, 1000)]
    for index, (start, end) in enumerate(spans):
        provider = index != 1
        units.append(
            SimpleNamespace(
                parent_scene_index=1,
                materialized_index=index,
                source_beat_index=index,
                start_ms=start,
                end_ms=end,
                duration_ms=end - start,
                scene_view=SimpleNamespace(source_statement_references=[index + 1]),
                direction_view=SimpleNamespace(metadata={"semantic_role": "EVIDENCE"}),
                asset_decision=SimpleNamespace(
                    query_hint="mechanism factory" if provider else None
                ),
                camera_motion_intent=(
                    BeatMotionIntent.DRIFT if index == 2 else
                    BeatMotionIntent.SLOW_PUSH_IN if index == 0 else
                    BeatMotionIntent.STATIC
                ),
                transition_intent=BeatTransitionIntent.HARD_CUT,
            )
        )
    return SimpleNamespace(parent_scene_index=1, units=tuple(units), total_duration_ms=1000)


def _provider_asset():
    return SimpleNamespace(
        provider="FAKE",
        asset_id="asset-a",
        source_url="https://cdn.example/a.mp4?token=redacted",
        source_page_url="https://example/a",
        license_status=LicenseStatus.ATTRIBUTION_REQUIRED,
        license_name="Fake License",
        license_url="https://example/license",
        attribution_text="Creator A",
        allowed_attribution_channels=("PUBLISH_METADATA",),
        metadata={"fixture": True},
        content_sha256="9" * 64,
    )


def _execution(plan):
    acquired = _provider_asset()
    assets = []
    for index, _unit in enumerate(plan.units):
        provider = index != 1
        assets.append(
            SimpleNamespace(
                action=(
                    BeatAssetAction.REUSE_COMPATIBLE if index == 2 else
                    BeatAssetAction.ACQUIRE_IF_NEEDED if provider else
                    BeatAssetAction.LOCAL_TEMPLATE
                ),
                required_kind=VisualAssetKind.BROLL if provider else None,
                reuse_from_beat_index=0 if index == 2 else None,
                resolved_asset=acquired if provider else None,
            )
        )
    return SimpleNamespace(parent_scene_index=1, assets=tuple(assets))


def _rendered(plan):
    metadata = []
    for index, _unit in enumerate(plan.units):
        metadata.append(
            SimpleNamespace(
                template_id=(
                    VisualTemplateId.FLOW_DIAGRAM
                    if index == 1 else VisualTemplateId.BROLL_EXPLAINER
                ),
                video_sha256=str(index + 1) * 64,
            )
        )
    return SimpleNamespace(clips=tuple(range(len(plan.units))), beat_metadata=tuple(metadata))


def _service(tmp_path: Path, *, prep, executor=None, renderer=None, assembler=None):
    legacy_renderer = MagicMock()
    legacy_renderer.render_clip = AsyncMock()
    service = VisualProductionV2Service(
        asset_orchestrator=MagicMock(),
        output_root=tmp_path,
        video_renderer=legacy_renderer,
        beat_preparation_service=prep,
        beat_asset_executor=executor,
        beat_visual_renderer=renderer,
        beat_clip_assembler=assembler,
    )
    return service, legacy_renderer


@pytest.mark.asyncio
async def test_local_canonical_multi_beat_canary_is_exact_and_provider_is_acquired_once(tmp_path):
    plan = _plan()
    prep = MagicMock()
    prep.prepare_from_script_dict.return_value = SimpleNamespace(
        eligible=True, render_plan=plan
    )
    executor = MagicMock()
    provider_resolver = AsyncMock(return_value=_provider_asset())

    async def execute_plan(**_kwargs):
        # The local provider-like acquisition happens once; the third beat reuses it.
        await provider_resolver("mechanism factory")
        return _execution(plan)

    executor.execute_plan = AsyncMock(side_effect=execute_plan)
    renderer = MagicMock()
    renderer.render_plan = AsyncMock(return_value=_rendered(plan))
    assembler = MagicMock()

    async def assemble(_clips, *, output_path, fps):
        Path(output_path).write_bytes(b"visual-only-parent")
        return SimpleNamespace(
            parent_scene_index=1,
            expected_duration_ms=1000,
            content_sha256="e" * 64,
        )

    assembler.assemble = AsyncMock(side_effect=assemble)
    service, legacy_renderer = _service(
        tmp_path,
        prep=prep,
        executor=executor,
        renderer=renderer,
        assembler=assembler,
    )
    result = await service._render_parent_visual(
        script_dict={"sections": []},
        scene=_scene(),
        duration_seconds=1.0,
        canonical_visual_mode="PEXELS",
        work_dir=tmp_path,
        browser=MagicMock(),
        fps=24,
        style_profile=None,
        narration_enabled=True,
    )

    assert result["execution_mode"] == "MULTI_BEAT"
    assert [beat.visual_origin for beat in result["runtime_beats"]] == [
        "PROVIDER", "TEMPLATE", "PROVIDER"
    ]
    assert [beat.start_offset_ms for beat in result["runtime_beats"]] == [0, 300, 700]
    assert [beat.end_offset_ms for beat in result["runtime_beats"]] == [300, 700, 1000]
    assert result["runtime_beats"][0].provider_asset_id == result["runtime_beats"][2].provider_asset_id
    assert result["runtime_beats"][0].provider_asset_content_sha256 == "9" * 64
    assert result["runtime_beats"][0].rendered_beat_clip_sha256 != "9" * 64
    assert result["template_id"] is None
    assert result["resolved_asset"] is None
    assert result["asset_kind"] is None
    assert result["asset_provider"] is None
    assert result["asset_id"] is None
    assert result["asset_query"] is None
    assert result["visual_origin"] is None
    assert result["visual_content_sha256"] is None
    executor.execute_plan.assert_awaited_once()
    provider_resolver.assert_awaited_once_with("mechanism factory")
    renderer.render_plan.assert_awaited_once()
    assembler.assemble.assert_awaited_once()
    legacy_renderer.render_clip.assert_not_awaited()
    prep.prepare_from_script_dict.assert_called_once()

    cached_beats = tuple(
        VerticalSliceRuntimeBeatVisual(**beat.model_dump(mode="json"))
        for beat in result["runtime_beats"]
    )
    assert cached_beats == result["runtime_beats"]


@pytest.mark.asyncio
async def test_parent_subtitle_burn_and_narration_mux_each_run_exactly_once(tmp_path):
    prep = MagicMock()
    service, _legacy_renderer = _service(tmp_path, prep=prep)
    visual_path = tmp_path / "parent_visual.mp4"
    audio_path = tmp_path / "parent_audio.wav"
    ass_path = tmp_path / "parent.ass"
    output_path = tmp_path / "parent.mp4"
    visual_path.write_bytes(b"visual")
    audio_path.write_bytes(b"audio")
    ass_path.write_text("ass", encoding="utf-8")

    async def burn_ass_subtitles(*, video_path, ass_path, output_path):
        Path(output_path).write_bytes(Path(video_path).read_bytes() + b"-subtitles")

    async def mux_video_audio(*, video_path, audio_path, output_path, **kwargs):
        Path(output_path).write_bytes(
            Path(video_path).read_bytes() + Path(audio_path).read_bytes()
        )

    service._ffmpeg_renderer.burn_ass_subtitles = AsyncMock(
        side_effect=burn_ass_subtitles
    )
    service._ffmpeg_renderer.mux_video_audio = AsyncMock(side_effect=mux_video_audio)
    result_sha = await service._finalize_narrated_parent_scene(
        scene_index=1,
        scene_visual_path=visual_path,
        scene_output_path=output_path,
        audio_path=audio_path,
        ass_path=ass_path,
        subtitle_enabled=True,
        work_dir=tmp_path,
    )
    assert len(result_sha) == 64
    service._ffmpeg_renderer.burn_ass_subtitles.assert_awaited_once()
    service._ffmpeg_renderer.mux_video_audio.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_stage",
    ["provider_resolution", "materialization", "render", "assembly"],
)
async def test_failure_after_multi_beat_commit_never_falls_back(tmp_path, failure_stage):
    plan = _plan()
    prep = MagicMock()
    prep.prepare_from_script_dict.return_value = SimpleNamespace(
        eligible=True, render_plan=plan
    )
    executor = MagicMock()
    executor.execute_plan = AsyncMock(return_value=_execution(plan))
    renderer = MagicMock()
    renderer.render_plan = AsyncMock(return_value=_rendered(plan))
    assembler = MagicMock()
    assembler.assemble = AsyncMock(return_value=SimpleNamespace(
        parent_scene_index=1, expected_duration_ms=1000, content_sha256="e" * 64
    ))
    target = {
        "provider_resolution": executor.execute_plan,
        "materialization": executor.execute_plan,
        "render": renderer.render_plan,
        "assembly": assembler.assemble,
    }[failure_stage]
    target.side_effect = RuntimeError(f"{failure_stage} failed")
    service, legacy_renderer = _service(
        tmp_path, prep=prep, executor=executor, renderer=renderer, assembler=assembler
    )
    service._visual_director.resolve = MagicMock()
    service._orchestrator.resolve = AsyncMock()
    with pytest.raises(VerticalSliceError, match="Committed multi-beat render failed"):
        await service._render_parent_visual(
            script_dict={"sections": []},
            scene=_scene(),
            duration_seconds=1.0,
            canonical_visual_mode="PEXELS",
            work_dir=tmp_path,
            browser=MagicMock(),
            fps=24,
            style_profile=None,
            narration_enabled=False,
        )
    legacy_renderer.render_clip.assert_not_awaited()
    service._visual_director.resolve.assert_not_called()
    service._orchestrator.resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("preparation", ["ineligible", "one_beat", "error"])
async def test_pre_provider_ineligibility_and_one_beat_plan_use_legacy(tmp_path, preparation):
    prep = MagicMock()
    if preparation == "ineligible":
        prep.prepare_from_script_dict.return_value = SimpleNamespace(
            eligible=False, render_plan=None
        )
    elif preparation == "one_beat":
        prep.prepare_from_script_dict.return_value = SimpleNamespace(
            eligible=True, render_plan=_plan(1)
        )
    else:
        prep.prepare_from_script_dict.side_effect = RuntimeError("capability unavailable")
    executor = MagicMock()
    executor.execute_plan = AsyncMock()
    service, legacy_renderer = _service(tmp_path, prep=prep, executor=executor)

    async def render_clip(**kwargs):
        Path(kwargs["output_path"]).write_bytes(b"legacy-visual")
        return SimpleNamespace(
            video_sha256="7" * 64,
            width=1920,
            height=1080,
        )

    legacy_renderer.render_clip.side_effect = render_clip
    result = await service._render_parent_visual(
        script_dict={"sections": []},
        scene=_legacy_scene(),
        duration_seconds=1.0,
        canonical_visual_mode="PEXELS",
        work_dir=tmp_path,
        browser=MagicMock(),
        fps=24,
        style_profile=None,
        narration_enabled=False,
    )
    assert result["execution_mode"] == "LEGACY_SINGLE_SCENE"
    assert len(result["runtime_beats"]) == 1
    executor.execute_plan.assert_not_awaited()
    legacy_renderer.render_clip.assert_awaited_once()


def test_g2c2c_version_boundaries_are_exact():
    assert CANONICAL_RENDER_SEMANTICS_VERSION == 6
    assert SUBTITLE_SEMANTICS_VERSION == 3


def test_default_multi_beat_collaborators_share_parent_authorities(tmp_path):
    orchestrator = MagicMock()
    video_renderer = MagicMock()
    ffmpeg_renderer = MagicMock()
    service = VisualProductionV2Service(
        asset_orchestrator=orchestrator,
        output_root=tmp_path,
        video_renderer=video_renderer,
        ffmpeg_renderer=ffmpeg_renderer,
    )

    assert service._beat_asset_executor._resolver is orchestrator
    assert service._beat_visual_renderer._payload_resolver is service._template_resolver
    assert service._beat_visual_renderer._template_renderer is service._template_renderer
    assert service._beat_visual_renderer._video_renderer is video_renderer
    assert service._beat_visual_renderer._max_concurrency == 2
    assert service._beat_clip_assembler._renderer is ffmpeg_renderer
