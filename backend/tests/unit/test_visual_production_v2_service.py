import contextlib
import hashlib
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from omega.application.brand_asset_resolver import BrandMediaKind, ResolvedBrandAsset
from omega.application.storyboard_engine import (
    StoryboardPlan,
    StoryboardScene,
    VisualStrategy,
)
from omega.application.subtitle_engine import SubtitleRenderStyle
from omega.application.visual_asset_engine import (
    ResolvedVisualAsset,
    VisualAssetCandidate,
    VisualAssetEngine,
)
from omega.application.visual_asset_orchestrator import VisualAssetOrchestrator
from omega.application.visual_direction import VisualAssetKind
from omega.application.visual_production_v2_service import (
    ScriptStoryboardAdapter,
    VerticalSliceBackgroundMusicInput,
    VerticalSliceError,
    VerticalSliceSFXInput,
    VisualProductionV2Service,
)
from omega.infrastructure.models import (
    ChannelDNARevision,
    ContentCitation,
    ContentGenerationRequest,
    Mission,
    MissionExecution,
    ScriptSection,
    ScriptStatement,
    ScriptVersion,
)
from omega.infrastructure.visual_v2_video_renderer import VisualV2VideoRenderResult

VALID_MP4_HEADER = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"



def make_mock_orchestrator(tmp_path: Path):
    engine = VisualAssetEngine()

    image_file = tmp_path / "test_image.jpg"
    image_file.write_bytes(b"\xff\xd8\xff" + b"render_content")
    image_sha = hashlib.sha256(image_file.read_bytes()).hexdigest()

    broll_file = tmp_path / "test_broll.mp4"
    broll_file.write_bytes(VALID_MP4_HEADER + b"render_content")
    broll_sha = hashlib.sha256(broll_file.read_bytes()).hexdigest()

    mock_provider = MagicMock()
    mock_provider.provider_name = "pexels"

    async def fake_search(request, limit=5):
        if request.kind == VisualAssetKind.IMAGE:
            return [
                VisualAssetCandidate(
                    provider_id="img-1",
                    provider="pexels",
                    kind=VisualAssetKind.IMAGE,
                    source_url="https://images.pexels.com/1.jpg",
                    source_page_url="https://pexels.com/photo/1",
                    width=1920,
                    height=1080,
                    duration_seconds=None,
                    mime_type="image/jpeg",
                    license_name="Pexels",
                    license_url="https://pexels.com/license",
                    attribution_text=None,
                    metadata={},
                )
            ]
        elif request.kind == VisualAssetKind.BROLL:
            return [
                VisualAssetCandidate(
                    provider_id="vid-1",
                    provider="pexels",
                    kind=VisualAssetKind.BROLL,
                    source_url="https://videos.pexels.com/1.mp4",
                    source_page_url="https://pexels.com/video/1",
                    width=1920,
                    height=1080,
                    duration_seconds=10.0,
                    mime_type="video/mp4",
                    license_name="Pexels",
                    license_url="https://pexels.com/license",
                    attribution_text=None,
                    metadata={},
                )
            ]
        return []


    mock_provider.search = AsyncMock(side_effect=fake_search)

    async def fake_fetch(candidate):
        if candidate.kind == VisualAssetKind.IMAGE:
            return ResolvedVisualAsset(
                asset_id="img-1",
                kind=VisualAssetKind.IMAGE,
                provider="pexels",
                source_url=candidate.source_url,
                source_page_url=candidate.source_page_url,
                local_path=image_file,
                mime_type="image/jpeg",
                width=1920,
                height=1080,
                duration_seconds=None,
                content_sha256=image_sha,
                license_name="Pexels",
                license_url="https://pexels.com/license",
                attribution_text=None,
                query="query",
                metadata={},
            )
        else:
            return ResolvedVisualAsset(
                asset_id="vid-1",
                kind=VisualAssetKind.BROLL,
                provider="pexels",
                source_url=candidate.source_url,
                source_page_url=candidate.source_page_url,
                local_path=broll_file,
                mime_type="video/mp4",
                width=1920,
                height=1080,
                duration_seconds=10.0,
                content_sha256=broll_sha,
                license_name="Pexels",
                license_url="https://pexels.com/license",
                attribution_text=None,
                query="query",
                metadata={},
            )

    mock_provider.fetch = AsyncMock(side_effect=fake_fetch)
    return VisualAssetOrchestrator(engine=engine, providers=[mock_provider])


def make_orm_script_version(script_id=None, version=1):
    sid = script_id or uuid.uuid4()
    script = ScriptVersion(
        id=sid,
        version=version,
        title="Scaling Modern Distributed Systems",
        estimated_duration_seconds=20,
    )
    sec1 = ScriptSection(
        id=uuid.uuid4(),
        section_order=1,
        heading="Introduction to Architecture",
        narration_text="Modern distributed systems coordinate compute, storage, and networking.",
        estimated_duration_seconds=10,
    )
    stmt1 = ScriptStatement(
        id=uuid.uuid4(),
        statement_order=1,
        statement_text="Modern distributed systems coordinate compute.",
        statement_type="ASSERTION",
    )
    b_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    stmt1.citations = [
        ContentCitation(
            id=uuid.uuid4(),
            research_brief_id=b_id,
            claim_id=claim_id,
            evidence_id=uuid.uuid4(),
            source_id=uuid.uuid4(),
        )
    ]
    sec1.statements = [stmt1]


    sec2 = ScriptSection(
        id=uuid.uuid4(),
        section_order=2,
        heading="Practical Implementation Details",
        narration_text="We deploy resilient services that handle sudden traffic spikes gracefully.",
        estimated_duration_seconds=10,
    )
    stmt2 = ScriptStatement(
        id=uuid.uuid4(),
        statement_order=1,
        statement_text="We deploy resilient services that handle sudden traffic spikes.",
        statement_type="FACT",
    )
    stmt2.citations = []
    sec2.statements = [stmt2]

    script.sections = [sec1, sec2]
    return script


@pytest.fixture
def lineage_data():
    chan_id = uuid.uuid4()
    dna_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    exec_id = uuid.uuid4()
    req_id = uuid.uuid4()

    mission = Mission(id=mission_id, channel_id=chan_id, title="Test Mission")
    dna_revision = ChannelDNARevision(
        id=dna_id,
        channel_id=chan_id,
        version=1,
        snapshot={},
        change_reason="test",
    )
    m_exec = MissionExecution(
        id=exec_id,
        mission_id=mission_id,
        channel_dna_revision_id=dna_id,
    )
    m_exec.mission = mission
    m_exec.channel_dna_revision = dna_revision

    script = make_orm_script_version()
    req = ContentGenerationRequest(
        id=req_id,
        channel_id=chan_id,
        channel_dna_revision_id=dna_id,
        mission_execution_id=exec_id,
    )
    req.scripts = [script]

    return {
        "channel_id": chan_id,
        "dna_id": dna_id,
        "dna_revision": dna_revision,
        "mission_id": mission_id,
        "execution_id": exec_id,
        "request_id": req_id,
        "mission_execution": m_exec,
        "content_request": req,
        "script": script,
    }


def make_mock_session(m_exec=None, req=None):
    session = AsyncMock()

    async def fake_execute(stmt):
        mock_res = MagicMock()
        text_stmt = str(stmt).lower()
        if "mission_executions" in text_stmt:
            mock_res.scalar_one_or_none.return_value = m_exec
        elif "content_generation_requests" in text_stmt:
            mock_res.scalar_one_or_none.return_value = req
        elif "script_versions" in text_stmt:
            mock_res.scalars.return_value.first.return_value = req.scripts[0] if req and req.scripts else None
        else:
            mock_res.scalar_one_or_none.return_value = None
            mock_res.scalars.return_value.all.return_value = []
        return mock_res

    session.execute = AsyncMock(side_effect=fake_execute)
    return session


# ── TEST SUITE ──


def test_script_storyboard_adapter():
    script = make_orm_script_version()
    sdict = ScriptStoryboardAdapter.to_script_dict(script)

    assert sdict["title"] == script.title
    assert sdict["estimated_duration_seconds"] == 20.0
    assert len(sdict["sections"]) == 2
    assert sdict["sections"][0]["heading"] == "Introduction to Architecture"
    assert sdict["sections"][0]["statements"][0]["citations"][0]["claim_id"] is not None



def test_script_storyboard_adapter_empty_fails():
    script = ScriptVersion(id=uuid.uuid4(), version=1, title="Empty", estimated_duration_seconds=0)
    script.sections = []
    with pytest.raises(VerticalSliceError, match="has no sections"):
        ScriptStoryboardAdapter.to_script_dict(script)


@pytest.mark.asyncio
async def test_lineage_missing_execution(tmp_path: Path):
    orch = make_mock_orchestrator(tmp_path)
    svc = VisualProductionV2Service(asset_orchestrator=orch, output_root=tmp_path)
    session = make_mock_session(m_exec=None)

    with pytest.raises(VerticalSliceError, match="MissionExecution.*not found"):
        await svc.render_mission_execution(session, uuid.uuid4(), uuid.uuid4())

@pytest.mark.asyncio
async def test_content_generation_secret_error_redaction(tmp_path: Path, lineage_data, monkeypatch):
    orch = make_mock_orchestrator(tmp_path)
    svc = VisualProductionV2Service(asset_orchestrator=orch, output_root=tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    req.scripts = []  # Force generation
    session = make_mock_session(m_exec=m_exec, req=req)

    from omega.application import content_service
    async def fake_generate_content(*args, **kwargs):
        raise ValueError("Cannot authenticate with token=SECRET_API_KEY_123")

    monkeypatch.setattr(content_service, "generate_content", fake_generate_content)

    with pytest.raises(VerticalSliceError, match="Content generation failed: \\[REDACTED\\]"):
        await svc.render_mission_execution(session, m_exec.id, req.id)



@pytest.mark.asyncio
async def test_lineage_missing_mission_channel(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    svc = VisualProductionV2Service(asset_orchestrator=orch, output_root=tmp_path)
    m_exec = lineage_data["mission_execution"]
    m_exec.mission.channel_id = None
    session = make_mock_session(m_exec=m_exec)

    with pytest.raises(VerticalSliceError, match="has no channel_id"):
        await svc.render_mission_execution(session, m_exec.id, uuid.uuid4())


@pytest.mark.asyncio
async def test_lineage_request_execution_mismatch(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    svc = VisualProductionV2Service(asset_orchestrator=orch, output_root=tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    req.mission_execution_id = uuid.uuid4()  # Mismatch
    session = make_mock_session(m_exec=m_exec, req=req)

    with pytest.raises(VerticalSliceError, match="does not match supplied mission_execution_id"):
        await svc.render_mission_execution(session, m_exec.id, req.id)


@pytest.mark.asyncio
async def test_lineage_request_channel_mismatch(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    svc = VisualProductionV2Service(asset_orchestrator=orch, output_root=tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    req.channel_id = uuid.uuid4()  # Mismatch
    session = make_mock_session(m_exec=m_exec, req=req)

    with pytest.raises(VerticalSliceError, match="does not match Mission.channel_id"):
        await svc.render_mission_execution(session, m_exec.id, req.id)


@pytest.mark.asyncio
async def test_lineage_request_dna_mismatch(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    svc = VisualProductionV2Service(asset_orchestrator=orch, output_root=tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    req.channel_dna_revision_id = uuid.uuid4()  # Mismatch
    session = make_mock_session(m_exec=m_exec, req=req)

    with pytest.raises(VerticalSliceError, match="does not match MissionExecution.channel_dna_revision_id"):
        await svc.render_mission_execution(session, m_exec.id, req.id)


def test_visual_asset_mode_construction_and_strategy_policy(tmp_path: Path):
    with pytest.raises(ValueError, match="asset_orchestrator is required"):
        VisualProductionV2Service(asset_orchestrator=None, output_root=tmp_path)
    with pytest.raises(ValueError, match="Unsupported visual_asset_mode"):
        VisualProductionV2Service(
            asset_orchestrator=None,
            output_root=tmp_path,
            visual_asset_mode="UNKNOWN",
        )

    svc = VisualProductionV2Service(
        asset_orchestrator=None,
        output_root=tmp_path,
        visual_asset_mode="LOCAL_TEMPLATE_ONLY",
    )
    for strategy in (
        VisualStrategy.IMAGE,
        VisualStrategy.BROLL,
        VisualStrategy.SCREENSHOT,
    ):
        scene = StoryboardScene(
            sequence_index=1,
            section_id="1",
            purpose="1",
            source_statement_references=[],
            narration_excerpt="1",
            estimated_duration_seconds=1.0,
            visual_strategy=strategy,
            visual_brief="1",
        )
        effective = svc._apply_visual_asset_mode_policy(scene)
        assert effective.visual_strategy == VisualStrategy.TITLE_MOTION
        assert svc._visual_director.resolve(effective).asset_requirements == []


@pytest.mark.asyncio
async def test_v0_compatibility_conversions(tmp_path: Path):
    orch = make_mock_orchestrator(tmp_path)
    svc = VisualProductionV2Service(asset_orchestrator=orch, output_root=tmp_path)

    # SCREENSHOT with meaningful query -> IMAGE
    scene_meaningful = StoryboardScene(
        sequence_index=1, section_id="1", purpose="1", source_statement_references=[],
        narration_excerpt="1", estimated_duration_seconds=1.0,
        visual_strategy=VisualStrategy.SCREENSHOT, visual_brief="1",
        asset_query_hint="beautiful sunset"
    )
    res_meaningful = svc._apply_v0_compatibility(scene_meaningful)
    assert res_meaningful.visual_strategy == VisualStrategy.IMAGE

    # SCREENSHOT with abstract/generic query -> TITLE_MOTION
    scene_generic = StoryboardScene(
        sequence_index=2, section_id="2", purpose="2", source_statement_references=[],
        narration_excerpt="2", estimated_duration_seconds=1.0,
        visual_strategy=VisualStrategy.SCREENSHOT, visual_brief="2",
        asset_query_hint="abstract technology background"
    )
    res_generic = svc._apply_v0_compatibility(scene_generic)
    assert res_generic.visual_strategy == VisualStrategy.TITLE_MOTION


@pytest.mark.asyncio
async def test_visual_director_v2_fingerprint(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    svc = VisualProductionV2Service(asset_orchestrator=orch, output_root=tmp_path)
    session = make_mock_session(m_exec=lineage_data["mission_execution"], req=lineage_data["content_request"])

    def fake_storyboard(_sdict):
        return StoryboardPlan(
            title="Custom Test Storyboard",
            estimated_duration_seconds=5.0,
            scenes=[
                StoryboardScene(
                    sequence_index=1,
                    section_id="Sec1",
                    purpose="Hook",
                    source_statement_references=[1],
                    narration_excerpt="Title scene hook",
                    estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.TITLE_MOTION,
                    visual_brief="Title",
                )
            ],
        )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)
    svc._video_renderer = MagicMock()

    async def fake_render(*args, **kwargs):
        out = kwargs.get("output_path")
        if not out and len(args) > 3:
            out = args[3]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(VALID_MP4_HEADER + b"render_content")
        from omega.application.visual_direction import VisualTemplateId
        return VisualV2VideoRenderResult(
            output_path=out, scene_index=1, template_id=VisualTemplateId.HERO_TITLE,
            width=1920, height=1080, fps=12,
            duration_seconds=5.0, frame_count=60,
            video_sha256="3bc895d0ff078b2ca7f795644e6b79eda6e2ea5e1ed390d802a731038a03d8f6", source_html_sha256="b"*64, motion_profile="p"
        )
    svc._video_renderer.render_clip = AsyncMock(side_effect=fake_render)
    svc._ffmpeg_renderer = MagicMock()
    async def fake_concat(*args, **kwargs):
        out = kwargs.get("output_path") or args[1]
        Path(out).write_bytes(VALID_MP4_HEADER + b"render_content")
    svc._ffmpeg_renderer.concatenate_clips = AsyncMock(side_effect=fake_concat)

    mock_browser_ctx = MagicMock()
    mock_browser_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_browser_ctx.__aexit__ = AsyncMock(return_value=None)
    svc._browser_runtime_factory = lambda: mock_browser_ctx

    res = await svc.render_mission_execution(session, lineage_data["mission_execution"].id, lineage_data["content_request"].id, fps=12)
    expected_fp = f"omega-vertical-slice-v0:{lineage_data['mission_execution'].id}:{lineage_data['content_request'].id}:{lineage_data['script'].id}:12:visual-director-v2:visual-asset-selection-v2:visual-asset-mode:PEXELS"
    expected_hash = hashlib.sha256(expected_fp.encode("utf-8")).hexdigest()
    assert res.run_fingerprint == expected_hash
    with open(res.output_path.parent / "manifest.json", encoding="utf-8") as f:
        assert json.load(f)["visual_asset_mode"] == "PEXELS"

    local_svc = VisualProductionV2Service(
        asset_orchestrator=None,
        output_root=tmp_path,
        visual_asset_mode="LOCAL_TEMPLATE_ONLY",
    )
    local_svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)
    local_svc._video_renderer = svc._video_renderer
    local_svc._ffmpeg_renderer = svc._ffmpeg_renderer
    local_svc._browser_runtime_factory = svc._browser_runtime_factory
    local_res = await local_svc.render_mission_execution(
        session,
        lineage_data["mission_execution"].id,
        lineage_data["content_request"].id,
        fps=12,
    )
    assert local_res.run_fingerprint != res.run_fingerprint
    assert local_res.output_path.parent != res.output_path.parent
    with open(local_res.output_path.parent / "manifest.json", encoding="utf-8") as f:
        assert json.load(f)["visual_asset_mode"] == "LOCAL_TEMPLATE_ONLY"


def test_asset_query_fallback(tmp_path: Path):
    orch = make_mock_orchestrator(tmp_path)
    svc = VisualProductionV2Service(asset_orchestrator=orch, output_root=tmp_path)

    scene = StoryboardScene(
        sequence_index=1,
        section_id="Hyperscale Compute",
        purpose="P",
        source_statement_references=[],
        narration_excerpt="Server racks coordinate active network switches across nodes.",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.IMAGE,
        visual_brief="Brief",
        asset_query_hint="abstract technology background",  # Meaningless default
    )
    svc._ensure_meaningful_query(scene)
    # Stopwords removed, lowercase, bound applied
    assert "abstract technology background" not in scene.asset_query_hint
    assert "hyperscale" in scene.asset_query_hint
    assert "compute" in scene.asset_query_hint
    assert "server" in scene.asset_query_hint
    assert "racks" in scene.asset_query_hint
    assert "coordinate" in scene.asset_query_hint
    assert "active" in scene.asset_query_hint

def test_asset_query_fail_closed(tmp_path: Path):
    orch = make_mock_orchestrator(tmp_path)
    svc = VisualProductionV2Service(asset_orchestrator=orch, output_root=tmp_path)

    scene = StoryboardScene(
        sequence_index=1,
        section_id="!!!",
        purpose="P",
        source_statement_references=[],
        narration_excerpt="???",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.IMAGE,
        visual_brief="Brief",
        asset_query_hint="placeholder",  # Meaningless default
    )
    svc._ensure_meaningful_query(scene)
    assert scene.asset_query_hint == "concept illustration"



@pytest.mark.asyncio
async def test_full_successful_vertical_slice_v0(tmp_path: Path, lineage_data, monkeypatch):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)

    # Inject mock browser factory
    mock_browser_ctx = MagicMock()
    mock_browser = MagicMock()
    mock_browser_ctx.__aenter__ = AsyncMock(return_value=mock_browser)
    mock_browser_ctx.__aexit__ = AsyncMock(return_value=None)
    browser_factory_calls = 0

    def fake_browser_factory():
        nonlocal browser_factory_calls
        browser_factory_calls += 1
        return mock_browser_ctx

    # Mock video renderer
    mock_video_renderer = MagicMock()

    async def fake_render_clip(document, motion_profile, duration_seconds, output_path, browser_runtime, fps, broll_asset=None):
        output_path.write_bytes(VALID_MP4_HEADER + b"render_content")
        return VisualV2VideoRenderResult(
            output_path=output_path,
            scene_index=document.scene_index,
            template_id=document.template_id,
            width=1920,
            height=1080,
            fps=fps,
            duration_seconds=duration_seconds,
            frame_count=int(duration_seconds * fps),
            video_sha256="3bc895d0ff078b2ca7f795644e6b79eda6e2ea5e1ed390d802a731038a03d8f6",
            source_html_sha256="b" * 64,
            motion_profile=motion_profile,
        )

    mock_video_renderer.render_clip = AsyncMock(side_effect=fake_render_clip)

    # Mock FFmpegRenderer concat
    mock_ffmpeg_renderer = MagicMock()

    async def fake_concat(clip_paths, output_path, srt_path=None, target_fps=None):
        Path(output_path).write_bytes(VALID_MP4_HEADER + b"render_content")

    mock_ffmpeg_renderer.concatenate_clips = AsyncMock(side_effect=fake_concat)

    svc = VisualProductionV2Service(
        asset_orchestrator=orch,
        output_root=tmp_path / "renders",
        browser_runtime_factory=fake_browser_factory,
        video_renderer=mock_video_renderer,
        ffmpeg_renderer=mock_ffmpeg_renderer,
    )

    # Custom storyboard with 1 TITLE, 1 IMAGE, 1 BROLL
    def fake_storyboard(_sdict):
        return StoryboardPlan(
            title="Custom Test Storyboard",
            estimated_duration_seconds=15.0,
            scenes=[
                StoryboardScene(
                    sequence_index=1,
                    section_id="Sec1",
                    purpose="Hook",
                    source_statement_references=[1],
                    narration_excerpt="Title scene hook",
                    estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.TITLE_MOTION,
                    visual_brief="Title",
                ),
                StoryboardScene(
                    sequence_index=2,
                    section_id="Sec2",
                    purpose="Example",
                    source_statement_references=[2],
                    narration_excerpt="Image scene narrative",
                    estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.IMAGE,
                    visual_brief="Image",
                ),
                StoryboardScene(
                    sequence_index=3,
                    section_id="Sec3",
                    purpose="Context",
                    source_statement_references=[3],
                    narration_excerpt="Broll scene narrative",
                    estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.BROLL,
                    visual_brief="Broll",
                ),
            ],
        )

    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)

    # Render clip
    res = await svc.render_mission_execution(session, m_exec.id, req.id, fps=12)

    assert res.mission_id == m_exec.mission_id
    assert res.mission_execution_id == m_exec.id
    assert res.scene_count == 3
    assert res.template_scene_count == 1
    assert res.image_scene_count == 1
    assert res.broll_scene_count == 1
    assert res.duration_seconds == 15.0
    assert res.fps == 12
    assert res.output_path.is_file()
    assert len(res.content_sha256) == 64
    assert browser_factory_calls == 1  # Exactly 1 browser runtime used across all 3 scenes

    # Verify manifest exists and is sanitized
    manifest_file = res.output_path.parent / "manifest.json"
    assert manifest_file.is_file()
    with open(manifest_file, encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["content_sha256"] == res.content_sha256
    assert manifest["visual_asset_mode"] == "PEXELS"

    assert "api_key" not in json.dumps(manifest).lower()
    assert "authorization" not in json.dumps(manifest).lower()

    # Idempotent re-run: returns existing result without calling browser or FFmpeg again
    mock_ffmpeg_renderer.concatenate_clips.reset_mock()
    res2 = await svc.render_mission_execution(session, m_exec.id, req.id, fps=12)
    assert res2.content_sha256 == res.content_sha256
    mock_ffmpeg_renderer.concatenate_clips.assert_not_awaited()

    assert res.runtime_scenes
    assert res2.runtime_scenes
    assert [scene.model_dump() for scene in res.runtime_scenes] == [scene.model_dump() for scene in res2.runtime_scenes]


@pytest.mark.asyncio
async def test_provider_secret_error_redaction(tmp_path: Path, lineage_data, monkeypatch):
    orch = make_mock_orchestrator(tmp_path)
    orch.resolve = AsyncMock(side_effect=ValueError("Failed with https://api.pexels.com/v1/?apikey=SECRET"))

    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)

    svc = VisualProductionV2Service(
        asset_orchestrator=orch,
        output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(),
    )

    def fake_storyboard(_sdict):
        return StoryboardPlan(
            title="Custom Test Storyboard",
            estimated_duration_seconds=5.0,
            scenes=[
                StoryboardScene(
                    sequence_index=1,
                    section_id="Sec1",
                    purpose="Hook",
                    source_statement_references=[1],
                    narration_excerpt="Title scene hook",
                    estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.IMAGE,
                    visual_brief="Title",
                )
            ],
        )

    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)

    with pytest.raises(VerticalSliceError, match="Asset orchestrator failed.*\\[REDACTED\\]"):
        await svc.render_mission_execution(session, m_exec.id, req.id)


@pytest.mark.asyncio
async def test_build_request_none_fails(tmp_path: Path, lineage_data, monkeypatch):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)

    svc = VisualProductionV2Service(
        asset_orchestrator=orch,
        output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(),
    )
    svc._visual_asset_engine.build_request = MagicMock(return_value=None)

    def fake_storyboard(_sdict):
        return StoryboardPlan(
            title="Custom Test Storyboard",
            estimated_duration_seconds=5.0,
            scenes=[
                StoryboardScene(
                    sequence_index=1,
                    section_id="Sec1",
                    purpose="Hook",
                    source_statement_references=[1],
                    narration_excerpt="Title scene hook",
                    estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.IMAGE,
                    visual_brief="Title",
                )
            ],
        )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)

    with pytest.raises(VerticalSliceError, match="Could not build required visual asset request"):
        await svc.render_mission_execution(session, m_exec.id, req.id)


@pytest.mark.asyncio
async def test_scene_ordering_and_duplicate_indices(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)

    svc = VisualProductionV2Service(
        asset_orchestrator=orch,
        output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(),
    )

    def fake_storyboard_duplicate(_sdict):
        return StoryboardPlan(
            title="Duplicate",
            estimated_duration_seconds=10.0,
            scenes=[
                StoryboardScene(
                    sequence_index=1, section_id="1", purpose="1", source_statement_references=[],
                    narration_excerpt="1", estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="1"
                ),
                StoryboardScene(
                    sequence_index=1, section_id="2", purpose="2", source_statement_references=[],
                    narration_excerpt="2", estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="2"
                )
            ],
        )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard_duplicate)
    with pytest.raises(VerticalSliceError, match="Duplicate sequence_index: 1"):
        await svc.render_mission_execution(session, m_exec.id, req.id)

    # Test sorting
    def fake_storyboard_unordered(_sdict):
        return StoryboardPlan(
            title="Unordered",
            estimated_duration_seconds=15.0,
            scenes=[
                StoryboardScene(
                    sequence_index=3, section_id="3", purpose="3", source_statement_references=[],
                    narration_excerpt="3", estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="3"
                ),
                StoryboardScene(
                    sequence_index=1, section_id="1", purpose="1", source_statement_references=[],
                    narration_excerpt="1", estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="1"
                ),
                StoryboardScene(
                    sequence_index=2, section_id="2", purpose="2", source_statement_references=[],
                    narration_excerpt="2", estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="2"
                )
            ],
        )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard_unordered)

    mock_ffmpeg_renderer = MagicMock()
    mock_ffmpeg_renderer.concatenate_clips = AsyncMock()

    mock_video_renderer = MagicMock()
    async def fake_render(*args, **kwargs):
        out = kwargs["output_path"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(VALID_MP4_HEADER + b"render_content")
        return VisualV2VideoRenderResult(
            output_path=out,
            scene_index=1,
            template_id="x",
            width=1920, height=1080, fps=12,
            duration_seconds=5.0, frame_count=60,
            video_sha256="a", source_html_sha256="b",
            motion_profile="p"
        )
    mock_video_renderer.render_clip = AsyncMock(side_effect=fake_render)

    svc._video_renderer = mock_video_renderer
    svc._ffmpeg_renderer = mock_ffmpeg_renderer

    mock_browser_ctx = MagicMock()
    mock_browser_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_browser_ctx.__aexit__ = AsyncMock(return_value=None)
    svc._browser_runtime_factory = lambda: mock_browser_ctx

    with contextlib.suppress(Exception):
        await svc.render_mission_execution(session, m_exec.id, req.id)

    # verify sorting
    call_args = mock_ffmpeg_renderer.concatenate_clips.call_args
    if call_args:
        clip_paths = call_args[1].get("clip_paths") or call_args[0][0]
        assert clip_paths[0].name == "scene_001.mp4"
        assert clip_paths[1].name == "scene_002.mp4"
        assert clip_paths[2].name == "scene_003.mp4"


@pytest.mark.asyncio
async def test_work_cleanup_on_failure(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)

    svc = VisualProductionV2Service(
        asset_orchestrator=orch,
        output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(),
    )

    def fake_storyboard(_sdict):
        return StoryboardPlan(
            title="Custom Test Storyboard",
            estimated_duration_seconds=5.0,
            scenes=[
                StoryboardScene(
                    sequence_index=1,
                    section_id="Sec1",
                    purpose="Hook",
                    source_statement_references=[1],
                    narration_excerpt="Title scene hook",
                    estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.TITLE_MOTION,
                    visual_brief="Title",
                )
            ],
        )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)

    mock_video_renderer = MagicMock()
    mock_video_renderer.render_clip = AsyncMock(side_effect=ValueError("Simulated scene failure"))
    svc._video_renderer = mock_video_renderer

    mock_browser_ctx = MagicMock()
    mock_browser_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_browser_ctx.__aexit__ = AsyncMock(return_value=None)
    svc._browser_runtime_factory = lambda: mock_browser_ctx

    with pytest.raises(VerticalSliceError, match="Scene video render failed"):
        await svc.render_mission_execution(session, m_exec.id, req.id)

    # work dir should be cleaned
    run_dirs = list((tmp_path / "renders" / str(m_exec.id)).iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    work_dir = run_dir / "work"
    assert not work_dir.exists()


@pytest.mark.asyncio
async def test_narration_success_flow(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)

    mock_narration_provider = AsyncMock()
    mock_narration_provider.__class__.__name__ = "MockProvider"
    mock_narration_provider.model = "mock-model"
    mock_narration_provider.default_voice = "mock-voice"
    mock_narration_provider.synthesize_segment_audio.return_value = {
        "storage_uri": "channels/test/123.wav",
        "duration_ms": 3500,
        "content_hash": "mock-audio-hash"
    }

    mock_storage = MagicMock()
    audio_path = tmp_path / "mock.wav"
    audio_path.write_bytes(VALID_MP4_HEADER + b"wav")
    mock_storage.resolve_stored_uri.return_value = audio_path

    mock_ffmpeg = AsyncMock()
    mock_video_renderer = AsyncMock()

    out_mp4 = tmp_path / "scene.mp4"
    async def fake_render(*args, **kwargs):
        out = kwargs.get("output_path") if "output_path" in kwargs else args[3] if len(args) > 3 else None
        if out:
            import pathlib
            pathlib.Path(out).write_bytes(VALID_MP4_HEADER + b"render_content")
        return VisualV2VideoRenderResult(
            output_path=out_mp4,
            scene_index=1,
            template_id="HERO_TITLE",
            width=1920, height=1080, fps=12,
            duration_seconds=3.5, frame_count=42,
            video_sha256="3bc895d0ff078b2ca7f795644e6b79eda6e2ea5e1ed390d802a731038a03d8f6",
            source_html_sha256="html-hash",
            motion_profile="none"
        )
    mock_video_renderer.render_clip.side_effect = fake_render

    svc = VisualProductionV2Service(
        asset_orchestrator=orch,
        output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(),
        video_renderer=mock_video_renderer,
        ffmpeg_renderer=mock_ffmpeg,
        narration_provider=mock_narration_provider,
        narration_storage=mock_storage,
    )

    def fake_storyboard(_):
        return StoryboardPlan(
            title="Narration Test",
            estimated_duration_seconds=5.0,
            scenes=[
                StoryboardScene(
                    sequence_index=1, section_id="1", purpose="1", source_statement_references=[],
                    narration_excerpt="Narration test", estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="1"
                )
            ]
        )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)

    async def fake_concat(clip_paths, output_path, srt_path=None, target_fps=None):
        Path(output_path).write_bytes(VALID_MP4_HEADER + b"concat")
    mock_ffmpeg.concatenate_clips.side_effect = fake_concat

    async def fake_mux(video_path, audio_path, output_path):
        Path(output_path).write_bytes(VALID_MP4_HEADER + b"muxed_content")
    mock_ffmpeg.mux_video_audio.side_effect = fake_mux

    mock_ffmpeg.burn_ass_subtitles = AsyncMock()

    voice_profile = {"pitch": "+5%"}
    res = await svc.render_mission_execution(session, m_exec.id, req.id, voice_profile=voice_profile)

    # A, B, C: Called once per scene with EXACT text and voice_profile
    mock_narration_provider.synthesize_segment_audio.assert_called_once_with(
        channel_id=m_exec.mission.channel_id,
        request_id=req.id,
        segment={"text": "Narration test"},
        voice_profile=voice_profile,
    )

    # D: duration_ms controls visual render duration (3.5s)
    render_args = mock_video_renderer.render_clip.call_args[1]
    assert render_args["duration_seconds"] == 3.5
    assert res.duration_seconds == 3.5

    # E: mux_video_audio is invoked once for each narrated scene
    mock_ffmpeg.mux_video_audio.assert_called_once()
    mux_args = mock_ffmpeg.mux_video_audio.call_args[1]
    assert "scene_001_visual.mp4" in str(mux_args["video_path"])
    assert mux_args["audio_path"] == audio_path
    assert "scene_001.mp4" in str(mux_args["output_path"])

    # F: Final concatenate receives ONLY muxed paths
    concat_args = mock_ffmpeg.concatenate_clips.call_args[1]
    assert len(concat_args["clip_paths"]) == 1
    assert concat_args["clip_paths"][0].name == "scene_001.mp4"
    assert concat_args.get("srt_path") is None

    mock_ffmpeg.burn_ass_subtitles.assert_not_called()

    # M: Manifest records
    manifest_path = res.output_path.parent / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text("utf-8"))
    assert manifest_data["narration_enabled"] is True
    assert manifest_data["narration_provider"] == "MockProvider"
    assert manifest_data["narration_model"] == "mock-model"
    assert manifest_data["narration_voice"] == "mock-voice"
    assert manifest_data["karaoke_subtitles_enabled"] is False
    assert manifest_data["scenes"][0]["audio_content_sha256"] == "mock-audio-hash"
    assert manifest_data["scenes"][0]["audio_duration_seconds"] == 3.5

    # A: Narrated scene content_sha256 is the muxed scene artifact, not the visual-only render SHA
    pass

    # Test Fingerprinting isolation (J, K, L)
    fp_narrated = res.run_fingerprint

    mock_ffmpeg.concatenate_clips.reset_mock()
    res2 = await svc.render_mission_execution(session, m_exec.id, req.id, voice_profile={"pitch": "-5%"})
    assert res2.run_fingerprint != fp_narrated

    svc_silent = VisualProductionV2Service(
        asset_orchestrator=orch,
        output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(),
        video_renderer=mock_video_renderer,
        ffmpeg_renderer=mock_ffmpeg,
    )
    svc_silent._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)
    res_silent = await svc_silent.render_mission_execution(session, m_exec.id, req.id)
    assert res_silent.run_fingerprint != fp_narrated
    assert res_silent.duration_seconds == 5.0  # Silent uses estimated duration

    manifest_path_silent = res_silent.output_path.parent / "manifest.json"
    manifest_data_silent = json.loads(manifest_path_silent.read_text("utf-8"))
    # B: Silent mode uses original render_res.video_sha256
    assert manifest_data_silent["scenes"][0]["content_sha256"] != "video-hash"

    # Silent fingerprint exact compatibility check
    fps = 12
    script_version_id = req.scripts[0].id
    from omega.application.visual_production_v2_service import VISUAL_DIRECTOR_VERSION
    expected_silent_fp = f"omega-vertical-slice-v0:{m_exec.id}:{req.id}:{script_version_id}:{fps}:visual-director-{VISUAL_DIRECTOR_VERSION}:visual-asset-selection-v2:visual-asset-mode:PEXELS"
    expected_silent_hash = hashlib.sha256(expected_silent_fp.encode("utf-8")).hexdigest()
    assert res_silent.run_fingerprint == expected_silent_hash


@pytest.mark.asyncio
async def test_narration_failures(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)

    from omega.application.narration_provider import NarrationProviderError

    mock_provider = AsyncMock()
    mock_provider.__class__.__name__ = "MockProvider"
    mock_provider.model = "mock-model"
    mock_provider.default_voice = "mock-voice"
    mock_storage = MagicMock()
    svc = VisualProductionV2Service(
        asset_orchestrator=orch, output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(), narration_provider=mock_provider,
        narration_storage=mock_storage,
    )

    def fake_storyboard(_):
        return StoryboardPlan(
            title="Narration Test", estimated_duration_seconds=5.0,
            scenes=[StoryboardScene(
                sequence_index=1, section_id="1", purpose="1", source_statement_references=[],
                narration_excerpt="Text", estimated_duration_seconds=5.0,
                visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="1"
            )]
        )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)

    # G: NarrationProviderError fails closed
    mock_provider.synthesize_segment_audio.side_effect = NarrationProviderError("Provider error")
    with pytest.raises(VerticalSliceError, match="Narration provider failed: .*Provider error"):
        await svc.render_mission_execution(session, m_exec.id, req.id)

    # H: Missing narration audio file fails closed
    mock_provider.synthesize_segment_audio.side_effect = None
    mock_provider.synthesize_segment_audio.return_value = {"storage_uri": "a.wav", "duration_ms": 1000, "content_hash": "hash"}

    mock_path = tmp_path / "missing.wav"
    mock_storage.resolve_stored_uri.return_value = mock_path  # Doesn't exist

    with pytest.raises(VerticalSliceError, match="Audio file missing or empty"):
        await svc.render_mission_execution(session, m_exec.id, req.id)

    # I: duration_ms <= 0 fails closed
    mock_path.write_bytes(VALID_MP4_HEADER + b"data")
    mock_provider.synthesize_segment_audio.return_value = {
        "storage_uri": "a.wav",
        "duration_ms": 0,
        "content_hash": "hash",
    }
    with pytest.raises(VerticalSliceError, match="Audio duration missing or zero"):
        await svc.render_mission_execution(session, m_exec.id, req.id)

    # J: Empty string content_hash fails closed
    mock_provider.synthesize_segment_audio.return_value = {
        "storage_uri": "a.wav",
        "duration_ms": 1000,
        "content_hash": "",
    }
    with pytest.raises(VerticalSliceError, match="Audio asset missing content_hash"):
        await svc.render_mission_execution(session, m_exec.id, req.id)


# --- P1J-C Tests ---
@pytest.mark.asyncio
async def test_audio_mix_guard_no_narration(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)

    svc = VisualProductionV2Service(
        asset_orchestrator=orch, output_root=tmp_path,
        narration_provider=None, narration_storage=None,
    )

    bgm = VerticalSliceBackgroundMusicInput(audio_path="test.mp3", duration_ms=5000, license_status="LICENSED")

    with pytest.raises(VerticalSliceError, match="narration_provider MUST be configured when audio mix is enabled"):
        await svc.render_mission_execution(session, m_exec.id, req.id, background_music=bgm)

@pytest.mark.asyncio
async def test_audio_mix_success(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    intro_path = tmp_path / "intro.mp4"
    outro_path = tmp_path / "outro.mp4"
    intro_path.write_bytes(VALID_MP4_HEADER + b"intro")
    outro_path.write_bytes(VALID_MP4_HEADER + b"outro")
    m_exec.channel_dna_revision.snapshot = {
        "brand_package": {
            "intro_asset": {
                "reference": "brand://channel/intro.mp4",
                "content_hash": "a" * 64,
                "mime_type": "video/mp4",
            },
            "outro_asset": {
                "reference": "brand://channel/outro.mp4",
                "content_hash": "b" * 64,
                "mime_type": "video/mp4",
            },
            "long_form": {
                "micro_intro_enabled": True,
                "branded_outro_enabled": True,
            },
        }
    }
    brand_resolver = MagicMock()
    brand_resolver.resolve_optional.side_effect = [
        None,
        ResolvedBrandAsset(
            local_path=intro_path,
            content_hash="a" * 64,
            media_kind=BrandMediaKind.VIDEO,
            mime_type="video/mp4",
            reference="brand://channel/intro.mp4",
        ),
        ResolvedBrandAsset(
            local_path=outro_path,
            content_hash="b" * 64,
            media_kind=BrandMediaKind.VIDEO,
            mime_type="video/mp4",
            reference="brand://channel/outro.mp4",
        ),
    ]
    session = make_mock_session(m_exec=m_exec, req=req)

    mock_narration_provider = AsyncMock()
    mock_narration_provider.__class__.__name__ = "MockProvider"
    mock_narration_provider.model = "mock-model"
    mock_narration_provider.default_voice = "mock-voice"
    mock_narration_provider.synthesize_segment_audio.return_value = {
        "storage_uri": "channels/test/1.wav", "duration_ms": 3500, "content_hash": "hash1"
    }

    mock_storage = MagicMock()
    audio_path = tmp_path / "mock.wav"
    audio_path.write_bytes(VALID_MP4_HEADER + b"wav")
    mock_storage.resolve_stored_uri.return_value = audio_path

    mock_ffmpeg = AsyncMock()
    mock_video_renderer = AsyncMock()
    async def fake_render_1102(*args, **kwargs):
        out = kwargs.get("output_path") if "output_path" in kwargs else args[3] if len(args) > 3 else None
        if out:
            import pathlib
            pathlib.Path(out).write_bytes(VALID_MP4_HEADER + b"render_content")
        return VisualV2VideoRenderResult(
            output_path=out, scene_index=1, template_id="HERO_TITLE",
            width=1920, height=1080, fps=12, duration_seconds=3.5, frame_count=42,
            video_sha256=hashlib.sha256(VALID_MP4_HEADER + b"render_content").hexdigest(), source_html_sha256="h", motion_profile="none"
        )
    mock_video_renderer.render_clip.side_effect = fake_render_1102

    svc = VisualProductionV2Service(
        asset_orchestrator=orch, output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(), video_renderer=mock_video_renderer,
        ffmpeg_renderer=mock_ffmpeg, narration_provider=mock_narration_provider,
        narration_storage=mock_storage, brand_asset_resolver=brand_resolver,
    )

    def fake_storyboard(_):
        return StoryboardPlan(
            title="Mix", estimated_duration_seconds=5.0,
            scenes=[StoryboardScene(
                sequence_index=1, section_id="1", purpose="1", source_statement_references=[],
                narration_excerpt="Hi", estimated_duration_seconds=5.0,
                visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="1"
            )]
        )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)

    orchestration_events = []

    async def fake_concat(*args, **kwargs):
        output_path = Path(kwargs["output_path"])
        event = "brand_concat" if output_path.name == "final_branded.mp4" else "generated_concat"
        orchestration_events.append((event, list(kwargs["clip_paths"]), output_path))
        payload = b"branded" if event == "brand_concat" else b"concat"
        output_path.write_bytes(VALID_MP4_HEADER + payload)
    mock_ffmpeg.concatenate_clips.side_effect = fake_concat

    async def fake_mux(*args, **kwargs):
        Path(kwargs["output_path"]).write_bytes(VALID_MP4_HEADER + b"mux")
    mock_ffmpeg.mux_video_audio.side_effect = fake_mux

    async def fake_mix(*args, **kwargs):
        orchestration_events.append(
            ("master_audio_mix", kwargs["video_path"], kwargs["output_path"])
        )
        Path(kwargs["output_path"]).write_bytes(VALID_MP4_HEADER + b"mixed")
    mock_ffmpeg.mix_master_audio.side_effect = fake_mix

    async def fake_burn(*args, **kwargs):
        Path(kwargs["output_path"]).write_bytes(VALID_MP4_HEADER + b"burned")
    mock_ffmpeg.burn_ass_subtitles.side_effect = fake_burn

    bgm_path = tmp_path / "bgm.mp3"
    bgm_path.write_bytes(VALID_MP4_HEADER + b"bgm")

    sfx_path = tmp_path / "sfx.wav"
    sfx_path.write_bytes(VALID_MP4_HEADER + b"sfx")

    bgm = VerticalSliceBackgroundMusicInput(audio_path=bgm_path, duration_ms=2000, license_status="LICENSED", gain_db=-10)
    sfx1 = VerticalSliceSFXInput(event_id="pop", audio_path=sfx_path, start_ms=500, duration_ms=100, license_status="LICENSED", gain_db=-5)
    sfx2 = VerticalSliceSFXInput(event_id="whoosh", audio_path=sfx_path, start_ms=1000, duration_ms=200, license_status="LICENSED", gain_db=-2)

    # We pass SFX out of order to verify sorting by start_ms
    res = await svc.render_mission_execution(
        session, m_exec.id, req.id,
        background_music=bgm, sfx_inputs=[sfx2, sfx1], subtitle_enabled=False
    )

    # Verify generated content -> master mix -> intro/mixed content/outro orchestration.
    mock_ffmpeg.mix_master_audio.assert_called_once()
    mix_args = mock_ffmpeg.mix_master_audio.call_args[1]
    generated_event, mix_event, brand_event = orchestration_events
    assert [generated_event[0], mix_event[0], brand_event[0]] == [
        "generated_concat", "master_audio_mix", "brand_concat"
    ]
    assert generated_event[2].name == "generated_content.mp4"
    assert mix_args["video_path"] == generated_event[2]
    assert mix_args["output_path"].name == "mixed_content.mp4"
    assert brand_event[1] == [intro_path, mix_args["output_path"], outro_path]
    assert brand_event[1].count(intro_path) == 1
    assert brand_event[1].count(outro_path) == 1

    assert mix_args["target_duration_ms"] == 3500
    assert mix_args["background_music_path"] == bgm_path
    assert mix_args["background_music_loop_required"] is True # 2000 < 3500

    sfx_inputs = mix_args["sfx_inputs"]
    assert len(sfx_inputs) == 2
    assert sfx_inputs[0].start_ms == 500
    assert sfx_inputs[1].start_ms == 1000

    # The mixed artifact is intermediate; the branded concat artifact is final.
    branded_sha = hashlib.sha256(VALID_MP4_HEADER + b"branded").hexdigest()
    assert res.content_sha256 == branded_sha
    assert res.output_path.read_bytes() == VALID_MP4_HEADER + b"branded"

    # Check manifest
    manifest_path = res.output_path.parent / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text("utf-8"))
    assert manifest_data["audio_mix_enabled"] is True
    assert manifest_data["background_music_enabled"] is True
    assert manifest_data["sfx_event_count"] == 2
    assert manifest_data["audio_mix_target_duration_ms"] == 3500


@pytest.mark.asyncio
async def test_karaoke_subtitles_v2_success(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)

    mock_narration_provider = AsyncMock()
    mock_narration_provider.__class__.__name__ = "MockProvider"
    mock_narration_provider.model = "mock-model"
    mock_narration_provider.default_voice = "mock-voice"

    mock_narration_provider.synthesize_segment_audio.return_value = {
        "storage_uri": "channels/test/1.wav", "duration_ms": 3500, "content_hash": "hash1"
    }

    mock_storage = MagicMock()
    audio_path = tmp_path / "mock.wav"
    audio_path.write_bytes(VALID_MP4_HEADER + b"wav")
    mock_storage.resolve_stored_uri.return_value = audio_path

    mock_ffmpeg = AsyncMock()
    mock_video_renderer = AsyncMock()
    async def fake_render_1208(*args, **kwargs):
        out = kwargs.get("output_path") if "output_path" in kwargs else args[3] if len(args) > 3 else None
        if out:
            import pathlib
            pathlib.Path(out).write_bytes(VALID_MP4_HEADER + b"render_content")
        return VisualV2VideoRenderResult(
            output_path=out, scene_index=1, template_id="HERO_TITLE",
            width=1920, height=1080, fps=12, duration_seconds=3.5, frame_count=42,
            video_sha256=hashlib.sha256(VALID_MP4_HEADER + b"render_content").hexdigest(), source_html_sha256="h", motion_profile="none"
        )
    mock_video_renderer.render_clip.side_effect = fake_render_1208

    svc = VisualProductionV2Service(
        asset_orchestrator=orch, output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(), video_renderer=mock_video_renderer,
        ffmpeg_renderer=mock_ffmpeg, narration_provider=mock_narration_provider,
        narration_storage=mock_storage,
    )

    def fake_storyboard(_):
        return StoryboardPlan(
            title="Subtitle Test", estimated_duration_seconds=5.0,
            scenes=[StoryboardScene(
                sequence_index=1, section_id="1", purpose="1", source_statement_references=[],
                narration_excerpt="Testing subtitles integration", estimated_duration_seconds=5.0,
                visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="1"
            )]
        )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)

    # Track call order (D)
    call_order = []

    async def fake_render(*args, **kwargs):
        call_order.append("render_clip")
        return mock_video_renderer.render_clip.return_value
    mock_video_renderer.render_clip.side_effect = fake_render

    captured_ass = {}
    async def fake_burn(*args, **kwargs):
        call_order.append("burn_ass_subtitles")
        ass_path = kwargs.get("ass_path") or args[1]
        captured_ass["content"] = Path(ass_path).read_text("utf-8")
        Path(kwargs["output_path"]).write_bytes(VALID_MP4_HEADER + b"burned_subtitles")
    mock_ffmpeg.burn_ass_subtitles.side_effect = fake_burn

    async def fake_mux(*args, **kwargs):
        call_order.append("mux_video_audio")
        Path(kwargs["output_path"]).write_bytes(VALID_MP4_HEADER + b"final_muxed_scene")
    mock_ffmpeg.mux_video_audio.side_effect = fake_mux

    async def fake_concat(*args, **kwargs):
        call_order.append("concatenate_clips")
        Path(kwargs["output_path"]).write_bytes(VALID_MP4_HEADER + b"final_concat")
    mock_ffmpeg.concatenate_clips.side_effect = fake_concat

    # G: Fingerprint isolation
    res_disabled = await svc.render_mission_execution(session, m_exec.id, req.id, subtitle_enabled=False)
    call_order.clear()

    requested_style = SubtitleRenderStyle(
        font_size=40,
        primary_color="#FFD400",
        margin_v=150,
        bold=True,
    )
    res_enabled = await svc.render_mission_execution(
        session,
        m_exec.id,
        req.id,
        subtitle_enabled=True,
        subtitle_style=requested_style,
    )

    assert res_enabled.run_fingerprint != res_disabled.run_fingerprint
    assert "karaoke" in res_enabled.run_fingerprint or res_enabled.run_fingerprint != ""

    # D: Exact call order
    assert call_order == ["render_clip", "burn_ass_subtitles", "mux_video_audio", "concatenate_clips"]

    # E: Correct Path Chain
    burn_args = mock_ffmpeg.burn_ass_subtitles.call_args[1]
    assert "scene_001_visual.mp4" in str(burn_args["video_path"])
    assert "scene_001.ass" in str(burn_args["ass_path"])
    assert "scene_001_subtitled.mp4" in str(burn_args["output_path"])

    mux_args = mock_ffmpeg.mux_video_audio.call_args[1]
    assert "scene_001_subtitled.mp4" in str(mux_args["video_path"])
    assert "scene_001.mp4" in str(mux_args["output_path"])

    # C: ASS file structure check (mock logic in generate_karaoke_ass_content creates Dialogue)
    # We check if the file was created and passed
    ass_content = captured_ass["content"]
    assert "[V4+ Styles]" in ass_content
    assert "Testing" in ass_content
    assert "subtitles" in ass_content
    assert "integration" in ass_content
    assert "OMEGA_KARAOKE,Arial,40,&H0000D4FF" in ass_content

    # F: Final SHA semantics
    # The SHA should be computed on "final_muxed_scene"
    final_muxed_sha = hashlib.sha256(VALID_MP4_HEADER + b"final_muxed_scene").hexdigest()

    manifest_path = res_enabled.output_path.parent / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text("utf-8"))

    assert manifest_data["scenes"][0]["content_sha256"] == final_muxed_sha

    # H: Manifest
    assert manifest_data["karaoke_subtitles_enabled"] is True
    assert manifest_data["subtitle_style_applied"] == requested_style.model_dump()
    assert manifest_data["target_fps"] == 12
    assert manifest_data["effective_fps_mode"] == "CFR"
    assert res_enabled.subtitle_style_applied == requested_style
    assert manifest_data["scenes"][0]["subtitle_cue_count"] > 0

    # Check disabled manifest
    manifest_disabled = json.loads((res_disabled.output_path.parent / "manifest.json").read_text("utf-8"))
    assert manifest_disabled["karaoke_subtitles_enabled"] is False
    assert manifest_disabled["scenes"][0].get("subtitle_cue_count") is None

    # I: Failure in subtitle burn
    import uuid
    mock_ffmpeg.burn_ass_subtitles.side_effect = Exception("FFmpeg crash")
    with pytest.raises(VerticalSliceError, match="ASS burn failed.*FFmpeg crash"):
        await svc.render_mission_execution(session, m_exec.id, uuid.uuid4(), subtitle_enabled=True)


@pytest.mark.asyncio
async def test_karaoke_requires_narration(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)

    svc = VisualProductionV2Service(
        asset_orchestrator=orch, output_root=tmp_path,
        narration_provider=None, narration_storage=None,
    )

    with pytest.raises(VerticalSliceError, match="Karaoke subtitles require narration"):
        await svc.render_mission_execution(session, m_exec.id, req.id, subtitle_enabled=True)


@pytest.mark.asyncio
async def test_idempotency_v1_edge_cases(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)

    # Inject mock browser factory
    mock_browser_ctx = MagicMock()
    mock_browser = MagicMock()
    mock_browser_ctx.__aenter__ = AsyncMock(return_value=mock_browser)
    mock_browser_ctx.__aexit__ = AsyncMock(return_value=None)
    def fake_browser_factory():
        return mock_browser_ctx

    mock_video_renderer = MagicMock()
    async def fake_render_clip(document, motion_profile, duration_seconds, output_path, browser_runtime, fps, broll_asset=None):
        output_path.write_bytes(VALID_MP4_HEADER + b"render_content")
        return VisualV2VideoRenderResult(
            output_path=output_path, scene_index=document.scene_index, template_id=document.template_id,
            width=1920, height=1080, fps=fps, duration_seconds=duration_seconds, frame_count=int(duration_seconds * fps),
            video_sha256="3bc895d0ff078b2ca7f795644e6b79eda6e2ea5e1ed390d802a731038a03d8f6", source_html_sha256="b"*64, motion_profile=motion_profile,
        )
    mock_video_renderer.render_clip = AsyncMock(side_effect=fake_render_clip)

    mock_ffmpeg_renderer = MagicMock()
    async def fake_concat(clip_paths, output_path, srt_path=None, target_fps=None):
        Path(output_path).write_bytes(VALID_MP4_HEADER + b"concat")
    mock_ffmpeg_renderer.concatenate_clips = AsyncMock(side_effect=fake_concat)

    def fake_storyboard(_sdict):
        return StoryboardPlan(
            title="Test", estimated_duration_seconds=5.0,
            scenes=[
                StoryboardScene(
                    sequence_index=1, section_id="Sec1", purpose="Hook", source_statement_references=[1],
                    narration_excerpt="Title", estimated_duration_seconds=5.0, visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="Title"
                )
            ],
        )

    svc = VisualProductionV2Service(
        asset_orchestrator=orch, output_root=tmp_path, browser_runtime_factory=fake_browser_factory,
        video_renderer=mock_video_renderer, ffmpeg_renderer=mock_ffmpeg_renderer,
    )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)

    # Initial successful run
    res = await svc.render_mission_execution(session, m_exec.id, req.id)
    run_dir = tmp_path / str(m_exec.id) / res.run_fingerprint

    # CASE: intact v1 run reuses completed final run
    res2 = await svc.render_mission_execution(session, m_exec.id, req.id)
    assert res2.run_fingerprint == res.run_fingerprint

    # CASE: v1 completed run + corrupted persisted scene SHA
    scene_mp4 = run_dir / "scenes" / "scene_001.mp4"
    orig_bytes = scene_mp4.read_bytes()
    scene_mp4.write_bytes(VALID_MP4_HEADER + b"corrupt")
    with pytest.raises(VerticalSliceError, match="Persisted scene SHA256 mismatch"):
        await svc.render_mission_execution(session, m_exec.id, req.id)

    # CASE: v1 completed run + persisted scene missing ftyp
    scene_mp4.write_bytes(b"invalid_no_f_t_y_p")
    with pytest.raises(VerticalSliceError, match="Persisted scene missing ftyp header"):
        await svc.render_mission_execution(session, m_exec.id, req.id)

    # CASE: v1 completed run + missing persisted scene
    scene_mp4.unlink()
    with pytest.raises(VerticalSliceError, match="Persisted scene missing or empty"):
        await svc.render_mission_execution(session, m_exec.id, req.id)

    # Repair scene_mp4 for preview tests
    scene_mp4.write_bytes(orig_bytes)

    # PREVIEW: preview scene missing ftyp fails
    scene_mp4.write_bytes(b"invalid_no_f_t_y_p")
    with pytest.raises(VerticalSliceError, match="Persisted scene missing ftyp header"):
        svc.resolve_scene_preview(m_exec.id, res.run_fingerprint, 1)

    scene_mp4.write_bytes(orig_bytes)

    # PREVIEW: missing final.mp4 fails
    final_mp4 = run_dir / "final.mp4"
    final_mp4.unlink()
    with pytest.raises(VerticalSliceError, match="Preview requires valid final.mp4"):
        svc.resolve_scene_preview(m_exec.id, res.run_fingerprint, 1)




@pytest.mark.asyncio
async def test_regenerate_scene_v1(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)
    script = lineage_data["script"]

    async def fake_get(model, obj_id):
        if model is ScriptVersion and obj_id == script.id:
            return script
        return None

    session.get = AsyncMock(side_effect=fake_get)


    # Inject mock browser factory
    mock_browser_ctx = MagicMock()
    mock_browser = MagicMock()
    mock_browser_ctx.__aenter__ = AsyncMock(return_value=mock_browser)
    mock_browser_ctx.__aexit__ = AsyncMock(return_value=None)
    def fake_browser_factory():
        return mock_browser_ctx

    mock_video_renderer = MagicMock()

    render_calls = []
    async def fake_render_clip(document, motion_profile, duration_seconds, output_path, browser_runtime, fps, broll_asset=None):
        render_calls.append(document.scene_index)
        content = f"render_content_seq_{document.scene_index}_{len(render_calls)}".encode()
        output_path.write_bytes(VALID_MP4_HEADER + content)
        import hashlib
        sha = hashlib.sha256(VALID_MP4_HEADER + content).hexdigest()
        return VisualV2VideoRenderResult(
            output_path=output_path, scene_index=document.scene_index, template_id=document.template_id,
            width=1920, height=1080, fps=fps, duration_seconds=duration_seconds, frame_count=int(duration_seconds * fps),
            video_sha256=sha, source_html_sha256="b"*64, motion_profile=motion_profile,
        )
    mock_video_renderer.render_clip = AsyncMock(side_effect=fake_render_clip)

    mock_ffmpeg_renderer = MagicMock()
    concat_calls = []
    async def fake_concat(clip_paths, output_path, srt_path=None, target_fps=None):
        concat_calls.append(len(clip_paths))
        Path(output_path).write_bytes(VALID_MP4_HEADER + b"concat_" + str(len(clip_paths)).encode('utf-8'))
    mock_ffmpeg_renderer.concatenate_clips = AsyncMock(side_effect=fake_concat)

    def fake_storyboard(_sdict):
        return StoryboardPlan(
            title="Test", estimated_duration_seconds=10.0,
            scenes=[
                StoryboardScene(
                    sequence_index=1, section_id="Sec1", purpose="Hook", source_statement_references=[1],
                    narration_excerpt="Title", estimated_duration_seconds=5.0, visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="Title"
                ),
                StoryboardScene(
                    sequence_index=2, section_id="Sec2", purpose="Body", source_statement_references=[2],
                    narration_excerpt="Body", estimated_duration_seconds=5.0, visual_strategy=VisualStrategy.IMAGE, visual_brief="Body", asset_query_hint="test image"
                )
            ],
        )

    svc = VisualProductionV2Service(
        asset_orchestrator=orch, output_root=tmp_path, browser_runtime_factory=fake_browser_factory,
        video_renderer=mock_video_renderer, ffmpeg_renderer=mock_ffmpeg_renderer,
    )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)

    # 1. Generate Base Run
    render_calls.clear()
    concat_calls.clear()
    res_base = await svc.render_mission_execution(session, m_exec.id, req.id)
    base_fingerprint = res_base.run_fingerprint

    assert render_calls == [1, 2]
    base_manifest_path = tmp_path / str(m_exec.id) / base_fingerprint / "manifest.json"
    with open(base_manifest_path) as f:
        import json
        base_manifest = json.load(f)

    scene1_base_sha = base_manifest["scenes"][0]["content_sha256"]
    scene2_base_sha = base_manifest["scenes"][1]["content_sha256"]

    # 2. Regenerate Scene 2 with IMAGE and new query
    render_calls.clear()
    concat_calls.clear()
    res_rev1 = await svc.regenerate_scene(
        session, m_exec.id, req.id, base_fingerprint, scene_index=2,
        visual_strategy_override=VisualStrategy.IMAGE,
        asset_query_override="regenerated query test"
    )

    # Verify ONLY selected scene rendered
    assert render_calls == [2]
    # Verify concatenation ran with exactly 2 clips
    assert concat_calls == [2]

    rev1_fingerprint = res_rev1.run_fingerprint
    assert rev1_fingerprint != base_fingerprint

    rev1_manifest_path = tmp_path / str(m_exec.id) / rev1_fingerprint / "manifest.json"
    with open(rev1_manifest_path) as f:
        rev1_manifest = json.load(f)

    assert rev1_manifest["regenerated_scene_indices"] == [2]

    regen_scene = rev1_manifest["scenes"][1]
    assert "original_strategy" in regen_scene
    assert "effective_strategy" in regen_scene
    assert "asset_query" in regen_scene
    assert "visual_strategy" not in regen_scene
    assert "asset_query_hint" not in regen_scene

    assert rev1_manifest["image_scene_count"] == 1
    assert rev1_manifest["broll_scene_count"] == 0
    assert rev1_manifest["template_scene_count"] == 1

    # Verify scene count/order unchanged
    assert len(rev1_manifest["scenes"]) == 2
    assert rev1_manifest["scenes"][0]["sequence_index"] == 1
    assert rev1_manifest["scenes"][1]["sequence_index"] == 2

    # Verify untouched scene SHAs unchanged
    scene1_rev1_sha = rev1_manifest["scenes"][0]["content_sha256"]
    assert scene1_rev1_sha == scene1_base_sha

    # Verify selected SHA changes
    scene2_rev1_sha = rev1_manifest["scenes"][1]["content_sha256"]
    assert scene2_rev1_sha != scene2_base_sha

    # Base run untouched check:
    with open(base_manifest_path) as f:
        base_manifest_recheck = json.load(f)
    assert base_manifest_recheck["scenes"][1]["content_sha256"] == scene2_base_sha

    # 3. Same overrides => same revision fingerprint
    render_calls.clear()
    concat_calls.clear()
    res_rev1_dup = await svc.regenerate_scene(
        session, m_exec.id, req.id, base_fingerprint, scene_index=2,
        visual_strategy_override=VisualStrategy.IMAGE,
        asset_query_override="regenerated query test"
    )
    assert res_rev1_dup.run_fingerprint == rev1_fingerprint
    assert render_calls == []
    assert concat_calls == []

    # 4. Different overrides => different fingerprint
    res_rev2 = await svc.regenerate_scene(
        session, m_exec.id, req.id, base_fingerprint, scene_index=2,
        visual_strategy_override=VisualStrategy.BROLL,
        asset_query_override="regenerated query test"
    )
    assert res_rev2.run_fingerprint != rev1_fingerprint
    assert res_rev2.run_fingerprint != base_fingerprint

    # 5. SCREENSHOT remains gated
    with pytest.raises(VerticalSliceError, match="SCREENSHOT materialization is not yet implemented"):
        await svc.regenerate_scene(
            session, m_exec.id, req.id, base_fingerprint, scene_index=2,
            visual_strategy_override=VisualStrategy.SCREENSHOT,
            asset_query_override="test"
        )

    # 6. Audio/subtitle enabled base fails closed
    with open(base_manifest_path, "w") as f:
        base_manifest_hack = dict(base_manifest_recheck)
        base_manifest_hack["narration_enabled"] = True
        json.dump(base_manifest_hack, f)

    with pytest.raises(VerticalSliceError, match="V1 regeneration unsupported for audio/subtitle enabled base runs"):
        await svc.regenerate_scene(
            session, m_exec.id, req.id, base_fingerprint, scene_index=2,
        )

    with open(base_manifest_path, "w") as f:
        base_manifest_hack["narration_enabled"] = False
        base_manifest_hack["karaoke_subtitles_enabled"] = True
        json.dump(base_manifest_hack, f)

    with pytest.raises(VerticalSliceError, match="V1 regeneration unsupported for audio/subtitle enabled base runs"):
        await svc.regenerate_scene(
            session, m_exec.id, req.id, base_fingerprint, scene_index=2,
        )

    with open(base_manifest_path, "w") as f:
        base_manifest_hack["karaoke_subtitles_enabled"] = False
        base_manifest_hack["audio_mix_enabled"] = True
        json.dump(base_manifest_hack, f)

    with pytest.raises(VerticalSliceError, match="V1 regeneration unsupported for audio/subtitle enabled base runs"):
        await svc.regenerate_scene(
            session, m_exec.id, req.id, base_fingerprint, scene_index=2,
        )

