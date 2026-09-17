"""Tests for P18-G2C2A Beat Asset Executor.

Verifies:
1. LOCAL_TEMPLATE => zero resolver calls;
2. NONE => zero resolver calls;
3. IMAGE ACQUIRE => exactly one resolver call;
4. BROLL ACQUIRE => exactly one resolver call;
5. Acquired IMAGE materializes BoundVisualAsset;
6. Acquired BROLL materializes BoundBrollAsset;
7. REUSE_COMPATIBLE => zero new resolver calls;
8. Reused beat shares exact resolved asset authority;
9. Reused beat shares correct bound asset;
10. BROLL -> local diagram -> BROLL reuse = one provider call total;
11. Future-beat reuse rejected;
12. Missing reuse source rejected;
13. Cross-kind reuse rejected;
14. Cross-scene reuse rejected;
15. provider_acquisition_allowed=False blocks acquisition;
16. Unsupported SCREENSHOT execution rejected;
17. Meaningless/mismatched query fails closed;
18. Provider error fails closed;
19. Physical SHA mismatch is rejected by materializer;
20. Deterministic decision order preserved.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

import pytest

from omega.application.beat_asset_executor import (
    BeatAssetExecutionError,
    BeatAssetExecutor,
)
from omega.application.beat_asset_policy import BeatAssetAction, BeatAssetDecision
from omega.application.beat_render_adapter import BeatRenderPlan, BeatRenderUnit
from omega.application.editorial_beat import BeatMotionIntent, BeatTransitionIntent
from omega.application.storyboard_engine import StoryboardScene, VisualStrategy
from omega.application.visual_asset_binding import BoundBrollAsset, BoundVisualAsset
from omega.application.visual_asset_engine import (
    ResolvedVisualAsset,
    VisualAssetRequest,
)
from omega.application.visual_direction import (
    VisualAssetKind,
    VisualAssetRequirement,
    VisualDirection,
    VisualRenderMode,
    VisualTemplateId,
)
from omega.domain.production import LicenseStatus


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="omega_asset_exec_test_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def _create_dummy_image(path: Path) -> str:
    """Create valid minimal JPEG bytes."""
    content = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00"
        + b"\x00" * 64
        + b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
    )
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _create_dummy_broll(path: Path) -> str:
    """Create valid minimal MP4 bytes with ftyp box."""
    content = (
        b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom\x00\x00\x00\x08free\x00\x00\x00\x10mdat"
        + b"\x00" * 100
    )
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


class MockOrchestrator:
    """Test orchestrator recording call counts and returning pre-configured assets."""

    def __init__(self, asset_map: dict[VisualAssetKind, ResolvedVisualAsset] | None = None):
        self.call_count = 0
        self.recorded_requests: list[VisualAssetRequest] = []
        self.asset_map = asset_map or {}
        self.should_fail = False

    async def resolve(self, request: VisualAssetRequest) -> ResolvedVisualAsset:
        self.call_count += 1
        self.recorded_requests.append(request)
        if self.should_fail:
            raise RuntimeError("Injected provider failure")
        if request.kind in self.asset_map:
            return self.asset_map[request.kind]
        raise ValueError(f"No asset configured for {request.kind}")


def _make_unit(
    parent_scene_index: int = 1,
    beat_index: int = 0,
    action: BeatAssetAction = BeatAssetAction.LOCAL_TEMPLATE,
    required_kind: VisualAssetKind | None = None,
    reuse_source_beat_index: int | None = None,
    query_hint: str | None = None,
    duration_ms: int = 2000,
) -> BeatRenderUnit:
    scene = StoryboardScene(
        sequence_index=parent_scene_index,
        section_id="Sec",
        purpose="Purpose",
        source_statement_references=[1],
        narration_excerpt="Beat narration.",
        estimated_duration_seconds=duration_ms / 1000.0,
        visual_strategy=VisualStrategy.BROLL,
        visual_brief="Brief",
    )
    reqs = []
    if required_kind:
        reqs.append(
            VisualAssetRequirement(
                kind=required_kind,
                purpose="Illustrate beat",
                query_hint=query_hint or "meaningful test query",
                required=True,
            )
        )
    direction = VisualDirection(
        scene_index=parent_scene_index,
        render_mode=VisualRenderMode.TEMPLATE if not required_kind else VisualRenderMode.BROLL,
        template_id=VisualTemplateId.FLOW_DIAGRAM if not required_kind else VisualTemplateId.BROLL_EXPLAINER,
        asset_requirements=reqs,
        motion_profile=None,
        rationale="Direction rationale",
    )
    decision = BeatAssetDecision(
        parent_scene_index=parent_scene_index,
        beat_index=beat_index,
        action=action,
        required_kind=required_kind,
        reuse_from_beat_index=reuse_source_beat_index,
        query_hint=query_hint,
        rationale="Decision rationale",
    )
    return BeatRenderUnit(
        parent_scene_index=parent_scene_index,
        materialized_index=beat_index,
        source_beat_index=beat_index,
        start_ms=0,
        end_ms=duration_ms,
        duration_ms=duration_ms,
        scene_view=scene,
        direction_view=direction,
        asset_decision=decision,
        camera_motion_intent=BeatMotionIntent.STATIC,
        transition_intent=BeatTransitionIntent.HARD_CUT,
    )


@pytest.mark.asyncio
async def test_01_and_02_local_template_and_none_require_zero_resolver_calls():
    """LOCAL_TEMPLATE and NONE actions execute with zero provider/orchestrator calls, even with resolver=None."""
    executor = BeatAssetExecutor(resolver=None)

    u0 = _make_unit(beat_index=0, action=BeatAssetAction.LOCAL_TEMPLATE)
    u1 = _make_unit(beat_index=1, action=BeatAssetAction.NONE)
    plan = BeatRenderPlan(parent_scene_index=1, units=(u0, u1), total_duration_ms=4000)

    result = await executor.execute_plan(render_plan=plan)
    assert len(result.assets) == 2
    assert result.assets[0].action == BeatAssetAction.LOCAL_TEMPLATE
    assert result.assets[0].resolved_asset is None
    assert result.assets[0].bound_visual_asset is None
    assert result.assets[1].action == BeatAssetAction.NONE


@pytest.mark.asyncio
async def test_acquire_if_needed_with_none_resolver_fails_deterministically():
    """ACQUIRE_IF_NEEDED with resolver=None raises deterministic BeatAssetExecutionError."""
    executor = BeatAssetExecutor(resolver=None)
    unit = _make_unit(beat_index=0, action=BeatAssetAction.ACQUIRE_IF_NEEDED, required_kind=VisualAssetKind.BROLL, query_hint="ocean waves")
    plan = BeatRenderPlan(parent_scene_index=1, units=(unit,), total_duration_ms=2000)

    with pytest.raises(BeatAssetExecutionError, match="No visual asset resolver configured"):
        await executor.execute_plan(render_plan=plan)


@pytest.mark.asyncio
async def test_03_and_05_image_acquire_materializes_bound_visual_asset(tmp_dir):
    """IMAGE ACQUIRE_IF_NEEDED makes exactly one resolver call and materializes BoundVisualAsset."""
    img_path = tmp_dir / "test.jpg"
    img_sha = _create_dummy_image(img_path)

    resolved_img = ResolvedVisualAsset(
        asset_id="img-123",
        kind=VisualAssetKind.IMAGE,
        provider="test-provider",
        source_url="https://example.com/img.jpg",
        source_page_url="https://example.com/page",
        local_path=img_path,
        mime_type="image/jpeg",
        width=1920,
        height=1080,
        duration_seconds=None,
        content_sha256=img_sha,
        license_status=LicenseStatus.LICENSED,
        license_name="Creative Commons",
        license_url="https://creativecommons.org",
        attribution_text="Photo by Author",
        query="ocean waves sunset",
        metadata={},
    )
    orchestrator = MockOrchestrator({VisualAssetKind.IMAGE: resolved_img})
    executor = BeatAssetExecutor(resolver=orchestrator)

    unit = _make_unit(
        beat_index=0,
        action=BeatAssetAction.ACQUIRE_IF_NEEDED,
        required_kind=VisualAssetKind.IMAGE,
        query_hint="ocean waves sunset",
    )
    plan = BeatRenderPlan(parent_scene_index=1, units=(unit,), total_duration_ms=2000)

    res = await executor.execute_plan(render_plan=plan)
    assert orchestrator.call_count == 1
    assert len(res.assets) == 1
    asset = res.assets[0]
    assert asset.resolved_asset == resolved_img
    assert isinstance(asset.bound_visual_asset, BoundVisualAsset)
    assert asset.bound_visual_asset.asset_id == "img-123"
    assert asset.bound_broll_asset is None


@pytest.mark.asyncio
async def test_04_and_06_broll_acquire_materializes_bound_broll_asset(tmp_dir):
    """BROLL ACQUIRE_IF_NEEDED makes exactly one resolver call and materializes BoundBrollAsset."""
    broll_path = tmp_dir / "test.mp4"
    broll_sha = _create_dummy_broll(broll_path)

    resolved_broll = ResolvedVisualAsset(
        asset_id="broll-456",
        kind=VisualAssetKind.BROLL,
        provider="pexels",
        source_url="https://example.com/vid.mp4",
        source_page_url="https://example.com/page",
        local_path=broll_path,
        mime_type="video/mp4",
        width=1920,
        height=1080,
        duration_seconds=5.0,
        content_sha256=broll_sha,
        license_status=LicenseStatus.LICENSED,
        license_name="Pexels License",
        license_url="https://pexels.com/license",
        attribution_text="Video by Creator",
        query="traffic timelapse night",
        metadata={},
    )
    orchestrator = MockOrchestrator({VisualAssetKind.BROLL: resolved_broll})
    executor = BeatAssetExecutor(resolver=orchestrator)

    unit = _make_unit(
        beat_index=0,
        action=BeatAssetAction.ACQUIRE_IF_NEEDED,
        required_kind=VisualAssetKind.BROLL,
        query_hint="traffic timelapse night",
    )
    plan = BeatRenderPlan(parent_scene_index=1, units=(unit,), total_duration_ms=2000)

    res = await executor.execute_plan(render_plan=plan)
    assert orchestrator.call_count == 1
    asset = res.assets[0]
    assert asset.resolved_asset == resolved_broll
    assert isinstance(asset.bound_broll_asset, BoundBrollAsset)
    assert asset.bound_broll_asset.asset_id == "broll-456"
    assert asset.bound_visual_asset is None


@pytest.mark.asyncio
async def test_07_to_10_broll_diagram_broll_reuse_requires_one_provider_call(tmp_dir):
    """BROLL (beat 0) -> LOCAL_TEMPLATE (beat 1) -> REUSE_COMPATIBLE (beat 2) performs exactly one provider call."""
    broll_path = tmp_dir / "traffic.mp4"
    broll_sha = _create_dummy_broll(broll_path)

    resolved_broll = ResolvedVisualAsset(
        asset_id="broll-traffic",
        kind=VisualAssetKind.BROLL,
        provider="pexels",
        source_url="https://example.com/vid.mp4",
        source_page_url=None,
        local_path=broll_path,
        mime_type="video/mp4",
        width=1920,
        height=1080,
        duration_seconds=10.0,
        content_sha256=broll_sha,
        license_status=LicenseStatus.LICENSED,
        license_name="Pexels",
        license_url=None,
        attribution_text=None,
        query="city traffic drone",
        metadata={},
    )
    orchestrator = MockOrchestrator({VisualAssetKind.BROLL: resolved_broll})
    executor = BeatAssetExecutor(resolver=orchestrator)

    u0 = _make_unit(
        beat_index=0,
        action=BeatAssetAction.ACQUIRE_IF_NEEDED,
        required_kind=VisualAssetKind.BROLL,
        query_hint="city traffic drone",
    )
    u1 = _make_unit(
        beat_index=1,
        action=BeatAssetAction.LOCAL_TEMPLATE,
    )
    u2 = _make_unit(
        beat_index=2,
        action=BeatAssetAction.REUSE_COMPATIBLE,
        required_kind=VisualAssetKind.BROLL,
        reuse_source_beat_index=0,
    )
    plan = BeatRenderPlan(parent_scene_index=1, units=(u0, u1, u2), total_duration_ms=6000)

    res = await executor.execute_plan(render_plan=plan)

    # Exactly 1 provider call across all 3 beats!
    assert orchestrator.call_count == 1
    assert len(res.assets) == 3

    # Beat 0: ACQUIRED
    assert res.assets[0].action == BeatAssetAction.ACQUIRE_IF_NEEDED
    assert res.assets[0].resolved_asset == resolved_broll

    # Beat 1: LOCAL_TEMPLATE
    assert res.assets[1].action == BeatAssetAction.LOCAL_TEMPLATE
    assert res.assets[1].resolved_asset is None

    # Beat 2: REUSE_COMPATIBLE from beat 0
    assert res.assets[2].action == BeatAssetAction.REUSE_COMPATIBLE
    assert res.assets[2].reuse_from_beat_index == 0
    # Shares exact same resolved and bound asset authority
    assert res.assets[2].resolved_asset == res.assets[0].resolved_asset
    assert res.assets[2].bound_broll_asset == res.assets[0].bound_broll_asset


@pytest.mark.asyncio
async def test_11_future_beat_reuse_rejected():
    """Attempting to reuse from a future beat index raises BeatAssetExecutionError."""
    executor = BeatAssetExecutor()
    u0 = _make_unit(beat_index=0, action=BeatAssetAction.REUSE_COMPATIBLE, required_kind=VisualAssetKind.BROLL, reuse_source_beat_index=1)
    u1 = _make_unit(beat_index=1, action=BeatAssetAction.ACQUIRE_IF_NEEDED, required_kind=VisualAssetKind.BROLL)
    plan = BeatRenderPlan(parent_scene_index=1, units=(u0, u1), total_duration_ms=4000)

    with pytest.raises(BeatAssetExecutionError, match="Cannot reuse from future or current beat"):
        await executor.execute_plan(render_plan=plan)


@pytest.mark.asyncio
async def test_12_missing_reuse_source_rejected():
    """Attempting to reuse from a non-existent beat index raises BeatAssetExecutionError."""
    executor = BeatAssetExecutor()
    u0 = _make_unit(beat_index=1, action=BeatAssetAction.REUSE_COMPATIBLE, required_kind=VisualAssetKind.BROLL, reuse_source_beat_index=0)
    plan = BeatRenderPlan(parent_scene_index=1, units=(u0,), total_duration_ms=2000)

    with pytest.raises(BeatAssetExecutionError, match="Missing reuse source beat 0"):
        await executor.execute_plan(render_plan=plan)


@pytest.mark.asyncio
async def test_13_cross_kind_reuse_rejected(tmp_dir):
    """Attempting to reuse BROLL when source beat resolved an IMAGE raises BeatAssetExecutionError."""
    img_path = tmp_dir / "test.jpg"
    img_sha = _create_dummy_image(img_path)
    resolved_img = ResolvedVisualAsset(
        asset_id="img-1", kind=VisualAssetKind.IMAGE, provider="p", source_url="u", source_page_url=None,
        local_path=img_path, mime_type="image/jpeg", width=100, height=100, duration_seconds=None,
        content_sha256=img_sha, license_status=LicenseStatus.LICENSED, license_name=None, license_url=None,
        attribution_text=None, query="test query", metadata={},
    )
    orchestrator = MockOrchestrator({VisualAssetKind.IMAGE: resolved_img})
    executor = BeatAssetExecutor(resolver=orchestrator)

    u0 = _make_unit(beat_index=0, action=BeatAssetAction.ACQUIRE_IF_NEEDED, required_kind=VisualAssetKind.IMAGE, query_hint="test query")
    u1 = _make_unit(beat_index=1, action=BeatAssetAction.REUSE_COMPATIBLE, required_kind=VisualAssetKind.BROLL, reuse_source_beat_index=0)
    plan = BeatRenderPlan(parent_scene_index=1, units=(u0, u1), total_duration_ms=4000)

    with pytest.raises(BeatAssetExecutionError, match="Cross-kind reuse rejected"):
        await executor.execute_plan(render_plan=plan)


@pytest.mark.asyncio
async def test_14_cross_scene_beat_rejected():
    """Units containing mismatched parent_scene_index are rejected."""
    executor = BeatAssetExecutor()
    u0 = _make_unit(parent_scene_index=1, beat_index=0, action=BeatAssetAction.LOCAL_TEMPLATE)
    u1 = _make_unit(parent_scene_index=2, beat_index=1, action=BeatAssetAction.LOCAL_TEMPLATE)
    plan = BeatRenderPlan(parent_scene_index=1, units=(u0, u1), total_duration_ms=4000)

    with pytest.raises(BeatAssetExecutionError, match="Cross-scene beat detected"):
        await executor.execute_plan(render_plan=plan)


@pytest.mark.asyncio
async def test_15_provider_acquisition_disallowed_blocks_execution():
    """When provider_acquisition_allowed=False, ACQUIRE_IF_NEEDED raises BeatAssetExecutionError."""
    executor = BeatAssetExecutor()
    unit = _make_unit(beat_index=0, action=BeatAssetAction.ACQUIRE_IF_NEEDED, required_kind=VisualAssetKind.BROLL, query_hint="waves")
    plan = BeatRenderPlan(parent_scene_index=1, units=(unit,), total_duration_ms=2000)

    with pytest.raises(BeatAssetExecutionError, match="Provider acquisition not allowed"):
        await executor.execute_plan(render_plan=plan, provider_acquisition_allowed=False)


@pytest.mark.asyncio
async def test_16_unsupported_screenshot_execution_rejected():
    """ACQUIRE_IF_NEEDED with SCREENSHOT kind raises BeatAssetExecutionError."""
    executor = BeatAssetExecutor()
    unit = _make_unit(beat_index=0, action=BeatAssetAction.ACQUIRE_IF_NEEDED, required_kind=VisualAssetKind.SCREENSHOT, query_hint="dashboard")
    plan = BeatRenderPlan(parent_scene_index=1, units=(unit,), total_duration_ms=2000)

    with pytest.raises(BeatAssetExecutionError, match="Unsupported beat asset kind"):
        await executor.execute_plan(render_plan=plan)


@pytest.mark.asyncio
async def test_17_meaningless_query_fails_closed():
    """An ACQUIRE_IF_NEEDED decision with generic/placeholder query raises BeatAssetExecutionError."""
    executor = BeatAssetExecutor()
    unit = _make_unit(beat_index=0, action=BeatAssetAction.ACQUIRE_IF_NEEDED, required_kind=VisualAssetKind.BROLL, query_hint="placeholder")
    plan = BeatRenderPlan(parent_scene_index=1, units=(unit,), total_duration_ms=2000)

    with pytest.raises(BeatAssetExecutionError, match=r"(?i)meaningless query"):
        await executor.execute_plan(render_plan=plan)


@pytest.mark.asyncio
async def test_18_provider_error_fails_closed():
    """When orchestrator.resolve raises an error, executor raises typed BeatAssetExecutionError."""
    orchestrator = MockOrchestrator()
    orchestrator.should_fail = True
    executor = BeatAssetExecutor(resolver=orchestrator)

    unit = _make_unit(beat_index=0, action=BeatAssetAction.ACQUIRE_IF_NEEDED, required_kind=VisualAssetKind.BROLL, query_hint="ocean storm")
    plan = BeatRenderPlan(parent_scene_index=1, units=(unit,), total_duration_ms=2000)

    with pytest.raises(BeatAssetExecutionError, match="Provider resolution failed"):
        await executor.execute_plan(render_plan=plan)


@pytest.mark.asyncio
async def test_19_physical_sha_mismatch_fails_closed(tmp_dir):
    """When local file content does not match declared content_sha256, materializer raises and fails closed."""
    img_path = tmp_dir / "corrupted.jpg"
    _create_dummy_image(img_path)

    # Deliberately supply wrong SHA
    resolved = ResolvedVisualAsset(
        asset_id="img-corrupted", kind=VisualAssetKind.IMAGE, provider="p", source_url=None, source_page_url=None,
        local_path=img_path, mime_type="image/jpeg", width=100, height=100, duration_seconds=None,
        content_sha256="0" * 64,
        license_status=LicenseStatus.LICENSED, license_name=None, license_url=None, attribution_text=None,
        query="valid query", metadata={},
    )
    orchestrator = MockOrchestrator({VisualAssetKind.IMAGE: resolved})
    executor = BeatAssetExecutor(resolver=orchestrator)

    unit = _make_unit(beat_index=0, action=BeatAssetAction.ACQUIRE_IF_NEEDED, required_kind=VisualAssetKind.IMAGE, query_hint="valid query")
    plan = BeatRenderPlan(parent_scene_index=1, units=(unit,), total_duration_ms=2000)

    with pytest.raises(BeatAssetExecutionError, match="Asset materialization failed"):
        await executor.execute_plan(render_plan=plan)


@pytest.mark.asyncio
async def test_20_deterministic_decision_order_preserved():
    """Units are executed in strictly ascending beat_index order even if supplied out-of-order."""
    executor = BeatAssetExecutor()
    u2 = _make_unit(beat_index=2, action=BeatAssetAction.LOCAL_TEMPLATE)
    u0 = _make_unit(beat_index=0, action=BeatAssetAction.LOCAL_TEMPLATE)
    u1 = _make_unit(beat_index=1, action=BeatAssetAction.NONE)
    plan = BeatRenderPlan(parent_scene_index=1, units=(u2, u0, u1), total_duration_ms=6000)

    res = await executor.execute_plan(render_plan=plan)
    assert [a.beat_index for a in res.assets] == [0, 1, 2]


@pytest.mark.asyncio
async def test_21_real_orchestrator_integration_compatibility(tmp_dir):
    """Prove BeatAssetExecutor is structurally compatible with real VisualAssetOrchestrator.resolve()."""
    from omega.application.visual_asset_engine import VisualAssetCandidate, VisualAssetEngine
    from omega.application.visual_asset_orchestrator import VisualAssetOrchestrator

    img_path = tmp_dir / "real_orch.jpg"
    img_sha = _create_dummy_image(img_path)

    class FakeProvider:
        @property
        def provider_name(self) -> str:
            return "fake-pexels"

        async def search(self, request: VisualAssetRequest, limit: int = 5) -> list[VisualAssetCandidate]:
            return [
                VisualAssetCandidate(
                    provider_id="cand-1",
                    kind=request.kind,
                    provider="fake-pexels",
                    source_url="https://example.com/photo.jpg",
                    source_page_url="https://example.com/page",
                    mime_type="image/jpeg",
                    width=1920,
                    height=1080,
                    duration_seconds=None,
                    license_status=LicenseStatus.LICENSED,
                    license_name="Free to use",
                    license_url="https://example.com/license",
                    attribution_text="Photo by Test",
                    metadata={},
                )
            ]

        async def fetch(self, candidate: VisualAssetCandidate) -> ResolvedVisualAsset:
            return ResolvedVisualAsset(
                asset_id="asset-real-orch",
                kind=candidate.kind,
                provider=candidate.provider,
                source_url=candidate.source_url,
                source_page_url=candidate.source_page_url,
                local_path=img_path,
                mime_type=candidate.mime_type,
                width=candidate.width,
                height=candidate.height,
                duration_seconds=candidate.duration_seconds,
                content_sha256=img_sha,
                license_status=candidate.license_status,
                license_name=candidate.license_name,
                license_url=candidate.license_url,
                attribution_text=candidate.attribution_text,
                query="realistic sunset landscape",
                metadata={},
            )

    provider = FakeProvider()
    engine = VisualAssetEngine()
    real_orchestrator = VisualAssetOrchestrator(engine=engine, providers=[provider])

    executor = BeatAssetExecutor(resolver=real_orchestrator, engine=engine)

    unit = _make_unit(
        beat_index=0,
        action=BeatAssetAction.ACQUIRE_IF_NEEDED,
        required_kind=VisualAssetKind.IMAGE,
        query_hint="realistic sunset landscape",
    )
    plan = BeatRenderPlan(parent_scene_index=1, units=(unit,), total_duration_ms=2000)

    res = await executor.execute_plan(render_plan=plan)
    assert len(res.assets) == 1
    assert res.assets[0].resolved_asset is not None
    assert res.assets[0].resolved_asset.asset_id == "asset-real-orch"
    assert isinstance(res.assets[0].bound_visual_asset, BoundVisualAsset)
    assert res.assets[0].bound_visual_asset.asset_id == "asset-real-orch"


@pytest.mark.asyncio
async def test_22_reuse_chain_provenance_and_zero_calls(tmp_dir):
    """Prove reuse chain: beat 0 ACQUIRE -> beat 1 REUSE(0) -> beat 2 REUSE(1) makes exactly 1 resolver call total."""
    broll_path = tmp_dir / "chain.mp4"
    broll_sha = _create_dummy_broll(broll_path)

    resolved_broll = ResolvedVisualAsset(
        asset_id="broll-chain",
        kind=VisualAssetKind.BROLL,
        provider="pexels",
        source_url="https://example.com/chain.mp4",
        source_page_url=None,
        local_path=broll_path,
        mime_type="video/mp4",
        width=1920,
        height=1080,
        duration_seconds=8.0,
        content_sha256=broll_sha,
        license_status=LicenseStatus.LICENSED,
        license_name="Pexels",
        license_url=None,
        attribution_text=None,
        query="river flowing mountain",
        metadata={},
    )
    orchestrator = MockOrchestrator({VisualAssetKind.BROLL: resolved_broll})
    executor = BeatAssetExecutor(resolver=orchestrator)

    u0 = _make_unit(
        beat_index=0,
        action=BeatAssetAction.ACQUIRE_IF_NEEDED,
        required_kind=VisualAssetKind.BROLL,
        query_hint="river flowing mountain",
    )
    u1 = _make_unit(
        beat_index=1,
        action=BeatAssetAction.REUSE_COMPATIBLE,
        required_kind=VisualAssetKind.BROLL,
        reuse_source_beat_index=0,
    )
    u2 = _make_unit(
        beat_index=2,
        action=BeatAssetAction.REUSE_COMPATIBLE,
        required_kind=VisualAssetKind.BROLL,
        reuse_source_beat_index=1,
    )
    plan = BeatRenderPlan(parent_scene_index=1, units=(u0, u1, u2), total_duration_ms=6000)

    res = await executor.execute_plan(render_plan=plan)

    # Exactly 1 resolver call for entire chain
    assert orchestrator.call_count == 1
    assert len(res.assets) == 3

    # All three external media executions share identical resolved and bound asset authority
    for idx in range(3):
        assert res.assets[idx].resolved_asset == resolved_broll
        assert res.assets[idx].resolved_asset.content_sha256 == broll_sha
        assert res.assets[idx].resolved_asset.provider == "pexels"
        assert res.assets[idx].bound_broll_asset is not None
        assert res.assets[idx].bound_broll_asset == res.assets[0].bound_broll_asset
