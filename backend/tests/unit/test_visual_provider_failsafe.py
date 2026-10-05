"""Provider degradation through real orchestration, payload and HTML paths; video is mocked."""
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from omega.application.beat_asset_executor import BeatAssetExecutionError, BeatAssetExecutor
from omega.application.beat_asset_policy import BeatAssetAction, BeatAssetDecision
from omega.application.beat_render_adapter import BeatRenderPlan, BeatRenderUnit
from omega.application.beat_visual_renderer import BeatVisualRenderer
from omega.application.editorial_beat import BeatMotionIntent, BeatTransitionIntent
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.visual_asset_engine import ResolvedVisualAsset
from omega.application.visual_direction import VisualAssetKind, VisualDirector, VisualTemplateId
from omega.application.visual_production_v2_service import (
    VerticalSliceError,
    VisualProductionV2Service,
)
from omega.domain.production import LicenseStatus


def scene(strategy=VisualStrategy.IMAGE):
    return StoryboardScene(
        sequence_index=1, section_id="section", purpose="Explain", source_statement_references=[1],
        narration_excerpt="Clouds gather above the ocean.", estimated_duration_seconds=2,
        visual_strategy=strategy, visual_brief="Clouds", asset_query_hint="ocean clouds",
    )


def unit(index, kind=VisualAssetKind.IMAGE, reuse=None):
    view = scene(VisualStrategy(kind.value))
    direction = VisualDirector().resolve(view)
    direction = direction.model_copy(update={"metadata": {**direction.metadata, "semantic_role": "EXPLANATION"}})
    return BeatRenderUnit(
        parent_scene_index=1, materialized_index=index, source_beat_index=index,
        start_ms=index * 2000, end_ms=(index + 1) * 2000, duration_ms=2000,
        scene_view=view, direction_view=direction,
        asset_decision=BeatAssetDecision(
            parent_scene_index=1, beat_index=index, required_kind=kind,
            action=BeatAssetAction.ACQUIRE_IF_NEEDED if reuse is None else BeatAssetAction.REUSE_COMPATIBLE,
            reuse_from_beat_index=reuse, query_hint="ocean clouds", rationale="test",
        ), camera_motion_intent=BeatMotionIntent.SLOW_PUSH_IN, transition_intent=BeatTransitionIntent.HARD_CUT,
    )


def asset(tmp_path, kind):
    data = b"\xff\xd8\xffmock-image" if kind == VisualAssetKind.IMAGE else b"\x00\x00\x00\x18ftypmp42mock-broll"
    path = tmp_path / kind.value
    path.write_bytes(data)
    return ResolvedVisualAsset(
        asset_id="asset-" + kind.value, kind=kind, provider="pexels",
        source_url="https://user:secret@example.com/media?token=secret#secret",
        source_page_url="https://example.com/page?key=secret", local_path=path,
        mime_type="image/jpeg" if kind == VisualAssetKind.IMAGE else "video/mp4",
        width=1920, height=1080, duration_seconds=8 if kind == VisualAssetKind.BROLL else None,
        content_sha256=hashlib.sha256(data).hexdigest(), license_status=LicenseStatus.LICENSED,
        license_name="Pexels", license_url="https://example.com/license?key=secret",
        attribution_text="Photo by Author", query="ocean clouds",
        metadata={"id": 9, "token": "secret", "url": "https://example.com/photo?token=secret"},
    )


def service(tmp_path, resolver, plan=None):
    video = MagicMock()
    async def render(**kwargs):
        if kwargs["document"].template_id == VisualTemplateId.KINETIC_TEXT:
            assert kwargs.get("camera_motion_intent", BeatMotionIntent.STATIC) == BeatMotionIntent.STATIC
        kwargs["output_path"].write_bytes(b"mock-video")
        return SimpleNamespace(video_sha256="f" * 64, width=1920, height=1080)
    video.render_clip = AsyncMock(side_effect=render)
    renderer = BeatVisualRenderer(video_renderer=video)
    renderer.render_plan = AsyncMock(wraps=renderer.render_plan)
    prep = MagicMock()
    prep.prepare_from_script_dict.return_value = SimpleNamespace(eligible=plan is not None, render_plan=plan)
    assembler = MagicMock()
    assembler.assemble = AsyncMock(return_value=SimpleNamespace(
        parent_scene_index=1, expected_duration_ms=6000, content_sha256="f" * 64,
    ))
    svc = VisualProductionV2Service(
        resolver, tmp_path, video_renderer=video, beat_preparation_service=prep,
        beat_visual_renderer=renderer, beat_clip_assembler=assembler,
    )
    return svc, video, renderer


async def parent(svc, tmp_path, view, duration=2):
    return await svc._render_parent_visual(
        script_dict={}, scene=view, duration_seconds=duration, canonical_visual_mode="PEXELS",
        work_dir=tmp_path, browser=MagicMock(), fps=24, style_profile=None, narration_enabled=True,
    )


def fallback_truth(truth, reason):
    assert truth.visual_origin == "TEMPLATE"
    assert truth.asset_action == "LOCAL_TEMPLATE"
    assert truth.template_id == "KINETIC_TEXT"
    for field in ("provider", "provider_asset_id", "source_url", "source_page_url", "provider_asset_content_sha256",
                  "license_name", "license_url", "attribution"):
        assert getattr(truth, field) is None
    assert truth.license_status == LicenseStatus.GENERATED
    assert truth.provider_metadata == {"fallback_reason_code": reason}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["absent", "auth", "rate_limit", "timeout", "no_candidate", "fetch", "none"])
@pytest.mark.parametrize("kind", [VisualAssetKind.IMAGE, VisualAssetKind.BROLL])
async def test_resolution_failures_single_and_executor(tmp_path, failure, kind):
    resolver = None if failure == "absent" else AsyncMock()
    if resolver is not None:
        if failure == "none":
            resolver.resolve.return_value = None
        else:
            resolver.resolve.side_effect = RuntimeError(f"{failure} https://user:secret@example.com/?token=secret")
    reason = "PROVIDER_UNAVAILABLE" if failure == "absent" else "PROVIDER_RESOLUTION_FAILED"
    planned = BeatRenderPlan(parent_scene_index=1, units=(unit(0, kind),), total_duration_ms=2000)
    result = await BeatAssetExecutor(resolver=resolver).execute_plan(render_plan=planned)
    assert result.assets[0].fallback_reason_code == reason
    assert result.assets[0].required_kind == kind
    svc, video, _ = service(tmp_path, resolver)
    view = scene(VisualStrategy(kind.value))
    before = view.model_dump()
    rendered = await parent(svc, tmp_path, view)
    fallback_truth(rendered["runtime_beats"][0], reason)
    assert view.model_dump() == before
    assert video.render_clip.call_args.kwargs["duration_seconds"] == 2
    assert svc._visual_asset_mode == "PEXELS"
    assert rendered["effective_strategy"] == VisualStrategy.KINETIC_TEXT


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [VisualAssetKind.IMAGE, VisualAssetKind.BROLL])
async def test_materialization_failure_single_and_executor(tmp_path, kind):
    resolved = asset(tmp_path, kind)
    resolved.local_path.write_bytes(b"corrupt")
    resolver = AsyncMock()
    resolver.resolve.return_value = resolved
    plan = BeatRenderPlan(parent_scene_index=1, units=(unit(0, kind),), total_duration_ms=2000)
    result = await BeatAssetExecutor(resolver=resolver).execute_plan(render_plan=plan)
    assert result.assets[0].fallback_reason_code == "PROVIDER_MATERIALIZATION_FAILED"
    assert result.assets[0].resolved_asset is None
    svc, _, _ = service(tmp_path, resolver)
    rendered = await parent(svc, tmp_path, scene(VisualStrategy(kind.value)))
    fallback_truth(rendered["runtime_beats"][0], "PROVIDER_MATERIALIZATION_FAILED")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [VisualAssetKind.IMAGE, VisualAssetKind.BROLL])
async def test_partial_parent_preserves_success_and_immutable_timeline(tmp_path, kind):
    resolved = asset(tmp_path, kind)
    resolver = AsyncMock()
    resolver.resolve.side_effect = [resolved, RuntimeError("secret-provider-body"), resolved]
    plan = BeatRenderPlan(parent_scene_index=1, units=tuple(unit(i, kind) for i in range(3)), total_duration_ms=6000)
    before = plan.model_dump()
    svc, video, renderer = service(tmp_path, resolver, plan)
    rendered = await parent(svc, tmp_path, scene(), duration=6)
    assert rendered["execution_mode"] == "MULTI_BEAT"
    execution_plan = renderer.render_plan.call_args.kwargs["render_plan"]
    assert plan.model_dump() == before
    assert execution_plan is not plan
    assert execution_plan.units[0] is plan.units[0]
    assert execution_plan.units[2] is plan.units[2]
    assert execution_plan.units[1] is not plan.units[1]
    assert execution_plan.units[1].scene_view.visual_strategy == VisualStrategy.KINETIC_TEXT
    assert execution_plan.units[1].direction_view.asset_requirements == []
    assert execution_plan.units[1].camera_motion_intent == BeatMotionIntent.STATIC
    assert plan.units[1].camera_motion_intent == BeatMotionIntent.SLOW_PUSH_IN
    for old, new in zip(plan.units, execution_plan.units, strict=True):
        for field in ("parent_scene_index", "source_beat_index", "materialized_index", "start_ms", "end_ms", "duration_ms"):
            assert getattr(old, field) == getattr(new, field)
        assert old.scene_view.narration_excerpt == new.scene_view.narration_excerpt
        assert old.scene_view.source_statement_references == new.scene_view.source_statement_references
        assert old.direction_view.metadata["semantic_role"] == new.direction_view.metadata["semantic_role"]
    assert resolver.resolve.await_count == 3
    assert video.render_clip.await_count == 3
    fallback_truth(rendered["runtime_beats"][1], "PROVIDER_RESOLUTION_FAILED")
    for i in (0, 2):
        truth = rendered["runtime_beats"][i]
        assert truth.visual_origin == "PROVIDER"
        assert truth.provider_asset_id == resolved.asset_id
        assert truth.provider_asset_content_sha256 == resolved.content_sha256
        assert truth.provider == resolved.provider
        assert truth.license_name == resolved.license_name
        assert truth.license_status == resolved.license_status
        assert truth.attribution == resolved.attribution_text
        assert truth.source_url == "https://example.com/media"
        assert truth.source_page_url == "https://example.com/page"
        assert truth.license_url == "https://example.com/license"
        assert truth.provider_metadata == {"id": 9, "url": "https://example.com/photo"}
        assert truth.asset_action == "ACQUIRE_IF_NEEDED"
    assert renderer.render_plan.call_args.kwargs["asset_execution"].assets[0].resolved_asset is resolved


@pytest.mark.asyncio
async def test_reuse_of_degraded_source_is_local_without_second_call(tmp_path):
    resolver = AsyncMock()
    resolver.resolve.side_effect = RuntimeError("unavailable")
    plan = BeatRenderPlan(parent_scene_index=1, units=(unit(0), unit(1, reuse=0), unit(2, reuse=1)), total_duration_ms=6000)
    svc, _, renderer = service(tmp_path, resolver, plan)
    rendered = await parent(svc, tmp_path, scene(), duration=6)
    assert resolver.resolve.await_count == 1
    for truth in rendered["runtime_beats"]:
        fallback_truth(truth, "PROVIDER_RESOLUTION_FAILED")
    assert renderer.render_plan.call_args.kwargs["render_plan"] is not plan


@pytest.mark.asyncio
@pytest.mark.parametrize("multi", [False, True])
async def test_successful_provider_does_not_mask_renderer_failure(tmp_path, multi):
    resolved = asset(tmp_path, VisualAssetKind.IMAGE)
    resolver = AsyncMock()
    resolver.resolve.return_value = resolved
    plan = BeatRenderPlan(parent_scene_index=1, units=tuple(unit(i) for i in range(3)), total_duration_ms=6000) if multi else None
    svc, video, _ = service(tmp_path, resolver, plan)
    video.render_clip.side_effect = RuntimeError("renderer programming failure")
    with pytest.raises(VerticalSliceError, match="renderer programming failure"):
        await parent(svc, tmp_path, scene(), duration=6 if multi else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["duplicate", "cross_scene", "query", "missing_requirement", "unsupported", "resolved_kind", "future_reuse", "reuse_kind"])
async def test_internal_corruption_remains_hard(tmp_path, corruption):
    resolver = AsyncMock()
    resolver.resolve.return_value = asset(tmp_path, VisualAssetKind.BROLL)
    u = unit(0)
    units = (u,)
    if corruption == "duplicate":
        units = (u, u)
    elif corruption == "cross_scene":
        units = (u.model_copy(update={"parent_scene_index": 2}),)
    elif corruption == "query":
        units = (u.model_copy(update={"asset_decision": u.asset_decision.model_copy(update={"query_hint": "different query"})}),)
    elif corruption == "missing_requirement":
        units = (u.model_copy(update={"direction_view": u.direction_view.model_copy(update={"asset_requirements": []})}),)
    elif corruption == "unsupported":
        units = (u.model_copy(update={"asset_decision": u.asset_decision.model_copy(update={"required_kind": VisualAssetKind.SCREENSHOT})}),)
    elif corruption == "future_reuse":
        units = (unit(0, reuse=1),)
    elif corruption == "reuse_kind":
        resolver.resolve.side_effect = RuntimeError("provider unavailable")
        units = (u, unit(1, VisualAssetKind.BROLL, reuse=0))
    plan = BeatRenderPlan(parent_scene_index=1, units=units, total_duration_ms=2000)
    with pytest.raises(BeatAssetExecutionError):
        await BeatAssetExecutor(resolver=resolver).execute_plan(render_plan=plan)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["search", "no_candidate", "fetch"])
async def test_real_orchestrator_external_failure_degrades(tmp_path, failure):
    from omega.application.visual_asset_engine import VisualAssetCandidate, VisualAssetEngine
    from omega.application.visual_asset_orchestrator import VisualAssetOrchestrator

    provider = MagicMock()
    provider.provider_name = "pexels"
    resolved = asset(tmp_path, VisualAssetKind.IMAGE)
    candidate = VisualAssetCandidate.model_validate({**resolved.model_dump(), "provider_id": "candidate"})
    provider.search = AsyncMock(return_value=[] if failure == "no_candidate" else [candidate])
    provider.fetch = AsyncMock(side_effect=RuntimeError("secret fetch body"))
    if failure == "search":
        provider.search.side_effect = RuntimeError("secret search body")
    resolver = VisualAssetOrchestrator(engine=VisualAssetEngine(), providers=[provider])
    svc, _, _ = service(tmp_path, resolver)
    rendered = await parent(svc, tmp_path, scene())
    fallback_truth(rendered["runtime_beats"][0], "PROVIDER_RESOLUTION_FAILED")
    provider.search.assert_awaited_once()
    assert provider.fetch.await_count == (1 if failure == "fetch" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["order", "timing", "strategy", "template"])
async def test_malformed_timeline_still_fails_after_provider_degradation(tmp_path, corruption):
    units = tuple(unit(i) for i in range(3))
    if corruption == "order":
        units = (units[1], units[0], units[2])
    elif corruption == "timing":
        units = (units[0], units[1].model_copy(update={"start_ms": 2300}), units[2])
    elif corruption == "strategy":
        units = (units[0], units[1].model_copy(update={
            "scene_view": units[1].scene_view.model_copy(update={"visual_strategy": "UNSUPPORTED"}),
        }), units[2])
    else:
        units = (units[0], units[1].model_copy(update={
            "direction_view": units[1].direction_view.model_copy(update={"template_id": "UNSUPPORTED"}),
        }), units[2])
    plan = BeatRenderPlan(parent_scene_index=1, units=units, total_duration_ms=6000)
    svc, _, _ = service(tmp_path, None, plan)
    with pytest.raises(VerticalSliceError):
        await parent(svc, tmp_path, scene(), duration=6)
