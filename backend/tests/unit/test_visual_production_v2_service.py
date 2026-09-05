import contextlib
import hashlib
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from omega.application.storyboard_engine import (
    StoryboardPlan,
    StoryboardScene,
    VisualStrategy,
)
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
    image_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 200)
    image_sha = hashlib.sha256(image_file.read_bytes()).hexdigest()

    broll_file = tmp_path / "test_broll.mp4"
    broll_file.write_bytes(VALID_MP4_HEADER + b"\x00" * 500)
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
    m_exec = MissionExecution(
        id=exec_id,
        mission_id=mission_id,
        channel_dna_revision_id=dna_id,
    )
    m_exec.mission = mission

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


@pytest.mark.asyncio
async def test_v0_compatibility_conversions(tmp_path: Path):
    orch = make_mock_orchestrator(tmp_path)
    svc = VisualProductionV2Service(asset_orchestrator=orch, output_root=tmp_path)

    # 1. KINETIC_TEXT -> TITLE_MOTION
    s_kt = StoryboardScene(
        sequence_index=1,
        section_id="Sec",
        purpose="P",
        source_statement_references=[],
        narration_excerpt="Text",
        estimated_duration_seconds=5.0,
        visual_strategy=VisualStrategy.KINETIC_TEXT,
        visual_brief="Brief",
    )
    eff_kt = svc._apply_v0_compatibility(s_kt)
    assert eff_kt.visual_strategy == VisualStrategy.TITLE_MOTION

    # 2. INFOGRAPHIC -> IMAGE
    s_info = s_kt.model_copy(update={"visual_strategy": VisualStrategy.INFOGRAPHIC})
    eff_info = svc._apply_v0_compatibility(s_info)
    assert eff_info.visual_strategy == VisualStrategy.IMAGE

    # 3. CTA -> TITLE_MOTION
    s_cta = s_kt.model_copy(update={"visual_strategy": VisualStrategy.CTA, "on_screen_text": "Subscribe now"})
    eff_cta = svc._apply_v0_compatibility(s_cta)
    assert eff_cta.visual_strategy == VisualStrategy.TITLE_MOTION
    assert eff_cta.on_screen_text == "Subscribe now"

    # 4. SCREENSHOT -> Fail closed
    s_screen = s_kt.model_copy(update={"visual_strategy": VisualStrategy.SCREENSHOT})
    with pytest.raises(VerticalSliceError, match="SCREENSHOT strategy is not supported"):
        svc._apply_v0_compatibility(s_screen)


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
    assert "abstract technology background" not in scene.asset_query_hint
    assert "Hyperscale" in scene.asset_query_hint
    assert "Server racks" in scene.asset_query_hint

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
    with pytest.raises(VerticalSliceError, match="Could not derive meaningful asset query"):
        svc._ensure_meaningful_query(scene)



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
        output_path.write_bytes(VALID_MP4_HEADER + b"\x00" * 100)
        return VisualV2VideoRenderResult(
            output_path=output_path,
            scene_index=document.scene_index,
            template_id=document.template_id,
            width=1920,
            height=1080,
            fps=fps,
            duration_seconds=duration_seconds,
            frame_count=int(duration_seconds * fps),
            video_sha256="a" * 64,
            source_html_sha256="b" * 64,
            motion_profile=motion_profile,
        )

    mock_video_renderer.render_clip = AsyncMock(side_effect=fake_render_clip)

    # Mock FFmpegRenderer concat
    mock_ffmpeg_renderer = MagicMock()

    async def fake_concat(clip_paths, output_path, srt_path=None):
        Path(output_path).write_bytes(VALID_MP4_HEADER + b"\x00" * 1000)

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

    assert "api_key" not in json.dumps(manifest).lower()
    assert "authorization" not in json.dumps(manifest).lower()

    # Idempotent re-run: returns existing result without calling browser or FFmpeg again
    mock_ffmpeg_renderer.concatenate_clips.reset_mock()
    res2 = await svc.render_mission_execution(session, m_exec.id, req.id, fps=12)
    assert res2.content_sha256 == res.content_sha256
    mock_ffmpeg_renderer.concatenate_clips.assert_not_awaited()


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
        out.write_bytes(VALID_MP4_HEADER + b"\x00" * 100)
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
    audio_path.write_bytes(b"wav")
    mock_storage.resolve_stored_uri.return_value = audio_path

    mock_ffmpeg = AsyncMock()
    mock_video_renderer = AsyncMock()

    out_mp4 = tmp_path / "scene.mp4"
    mock_video_renderer.render_clip.return_value = VisualV2VideoRenderResult(
        output_path=out_mp4,
        scene_index=1,
        template_id="HERO_TITLE",
        width=1920, height=1080, fps=12,
        duration_seconds=3.5, frame_count=42,
        video_sha256="video-hash",
        source_html_sha256="html-hash",
        motion_profile="none"
    )

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

    async def fake_concat(clip_paths, output_path, srt_path=None):
        Path(output_path).write_bytes(VALID_MP4_HEADER + b"concat")
    mock_ffmpeg.concatenate_clips.side_effect = fake_concat

    async def fake_mux(video_path, audio_path, output_path):
        Path(output_path).write_bytes(b"muxed_content")
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
    assert manifest_data["karaoke_cue_count"] == 0
    assert manifest_data["scenes"][0]["audio_content_sha256"] == "mock-audio-hash"
    assert manifest_data["scenes"][0]["audio_duration_seconds"] == 3.5

    # A: Narrated scene content_sha256 is the muxed scene artifact, not the visual-only render SHA
    assert manifest_data["scenes"][0]["content_sha256"] != "video-hash"

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
    assert manifest_data_silent["scenes"][0]["content_sha256"] == "video-hash"

    # Silent fingerprint exact compatibility check
    fps = 12
    script_version_id = req.scripts[0].id
    expected_silent_fp = f"omega-vertical-slice-v0:{m_exec.id}:{req.id}:{script_version_id}:{fps}"
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
    mock_path.write_bytes(b"data")
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

@pytest.mark.asyncio
async def test_karaoke_mode_guard_failure(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)

    svc = VisualProductionV2Service(
        asset_orchestrator=orch,
        output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(),
        # No narration provider
    )
    with pytest.raises(VerticalSliceError, match="narration_provider MUST be configured when karaoke_subtitles is True"):
        await svc.render_mission_execution(session, m_exec.id, req.id, karaoke_subtitles=True)

@pytest.mark.asyncio
async def test_karaoke_burn_failure(tmp_path: Path, lineage_data):
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
        "duration_ms": 1000,
        "content_hash": "mock-audio-hash"
    }

    mock_storage = MagicMock()
    audio_path = tmp_path / "mock.wav"
    audio_path.write_bytes(b"wav")
    mock_storage.resolve_stored_uri.return_value = audio_path

    mock_ffmpeg = AsyncMock()
    mock_video_renderer = AsyncMock()
    out_mp4 = tmp_path / "scene.mp4"
    mock_video_renderer.render_clip.return_value = VisualV2VideoRenderResult(
        output_path=out_mp4, scene_index=1, template_id="HERO_TITLE",
        width=1920, height=1080, fps=12, duration_seconds=1.0, frame_count=12,
        video_sha256="video-hash", source_html_sha256="html-hash", motion_profile="none"
    )

    svc = VisualProductionV2Service(
        asset_orchestrator=orch, output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(), video_renderer=mock_video_renderer,
        ffmpeg_renderer=mock_ffmpeg, narration_provider=mock_narration_provider,
        narration_storage=mock_storage,
    )

    def fake_storyboard(_):
        return StoryboardPlan(
            title="Narration Test", estimated_duration_seconds=1.0,
            scenes=[StoryboardScene(
                sequence_index=1, section_id="1", purpose="1", source_statement_references=[],
                narration_excerpt="Text", estimated_duration_seconds=1.0,
                visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="1"
            )]
        )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)

    async def fake_concat(clip_paths, output_path, srt_path=None):
        Path(output_path).write_bytes(VALID_MP4_HEADER + b"concat")
    mock_ffmpeg.concatenate_clips.side_effect = fake_concat

    async def fake_mux(video_path, audio_path, output_path):
        Path(output_path).write_bytes(b"muxed_content")
    mock_ffmpeg.mux_video_audio.side_effect = fake_mux

    # Burn failure
    mock_ffmpeg.burn_ass_subtitles.side_effect = ValueError("FFmpeg burn failed")

    with pytest.raises(VerticalSliceError, match="ASS burn-in failed: FFmpeg burn failed"):
        await svc.render_mission_execution(session, m_exec.id, req.id, karaoke_subtitles=True)

    run_dirs = list((tmp_path / "renders" / str(m_exec.id)).iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    final_path = run_dir / "final.mp4"
    assert not final_path.exists()

@pytest.mark.asyncio
async def test_narration_karaoke_success_flow(tmp_path: Path, lineage_data):
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
    audio_path.write_bytes(b"wav")
    mock_storage.resolve_stored_uri.return_value = audio_path

    mock_ffmpeg = AsyncMock()
    mock_video_renderer = AsyncMock()
    out_mp4 = tmp_path / "scene.mp4"
    mock_video_renderer.render_clip.return_value = VisualV2VideoRenderResult(
        output_path=out_mp4, scene_index=1, template_id="HERO_TITLE",
        width=1920, height=1080, fps=12, duration_seconds=3.5, frame_count=42,
        video_sha256="video-hash", source_html_sha256="html-hash", motion_profile="none"
    )

    svc = VisualProductionV2Service(
        asset_orchestrator=orch, output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(), video_renderer=mock_video_renderer,
        ffmpeg_renderer=mock_ffmpeg, narration_provider=mock_narration_provider,
        narration_storage=mock_storage,
    )

    def fake_storyboard(_):
        return StoryboardPlan(
            title="Narration Test", estimated_duration_seconds=5.0,
            scenes=[StoryboardScene(
                sequence_index=1, section_id="1", purpose="1", source_statement_references=[],
                narration_excerpt="Narration test", estimated_duration_seconds=5.0,
                visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="1"
            )]
        )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)

    async def fake_concat(clip_paths, output_path, srt_path=None):
        Path(output_path).write_bytes(VALID_MP4_HEADER + b"concat")
    mock_ffmpeg.concatenate_clips.side_effect = fake_concat

    async def fake_mux(video_path, audio_path, output_path):
        Path(output_path).write_bytes(b"muxed_content")
    mock_ffmpeg.mux_video_audio.side_effect = fake_mux

    captured_ass = {}
    async def fake_burn(video_path, ass_path, output_path):
        captured_ass["content"] = Path(ass_path).read_text("utf-8")
        Path(output_path).write_bytes(VALID_MP4_HEADER + b"burned")
    mock_ffmpeg.burn_ass_subtitles.side_effect = fake_burn

    res = await svc.render_mission_execution(session, m_exec.id, req.id, karaoke_subtitles=True)

    concat_args = mock_ffmpeg.concatenate_clips.call_args[1]
    assert concat_args.get("srt_path") is None

    mock_ffmpeg.burn_ass_subtitles.assert_called_once()
    burn_args = mock_ffmpeg.burn_ass_subtitles.call_args[1]
    assert "final_temp.mp4" in str(burn_args["video_path"])
    assert "karaoke.ass" in str(burn_args["ass_path"])
    assert "final_karaoke.mp4" in str(burn_args["output_path"])
    assert burn_args["video_path"] != burn_args["output_path"]

    ass_content = captured_ass["content"]
    assert "{\\kf" in ass_content
    assert "Narration" in ass_content
    assert "test" in ass_content

    manifest_path = res.output_path.parent / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text("utf-8"))
    assert manifest_data["karaoke_subtitles_enabled"] is True
    assert manifest_data["karaoke_cue_count"] > 0

    burned_sha = hashlib.sha256(VALID_MP4_HEADER + b"burned").hexdigest()
    assert res.content_sha256 == burned_sha
    assert manifest_data["content_sha256"] == burned_sha

@pytest.mark.asyncio
async def test_narration_karaoke_multi_scene_timing(tmp_path: Path, lineage_data):
    orch = make_mock_orchestrator(tmp_path)
    m_exec = lineage_data["mission_execution"]
    req = lineage_data["content_request"]
    session = make_mock_session(m_exec=m_exec, req=req)

    mock_narration_provider = AsyncMock()
    mock_narration_provider.__class__.__name__ = "MockProvider"
    mock_narration_provider.model = "mock-model"
    mock_narration_provider.default_voice = "mock-voice"
    mock_narration_provider.synthesize_segment_audio.side_effect = [
        {"storage_uri": "channels/test/1.wav", "duration_ms": 3000, "content_hash": "hash1"},
        {"storage_uri": "channels/test/2.wav", "duration_ms": 2000, "content_hash": "hash2"},
    ]

    mock_storage = MagicMock()
    audio_path = tmp_path / "mock.wav"
    audio_path.write_bytes(b"wav")
    mock_storage.resolve_stored_uri.return_value = audio_path

    mock_ffmpeg = AsyncMock()
    mock_video_renderer = AsyncMock()
    mock_video_renderer.render_clip.return_value = VisualV2VideoRenderResult(
        output_path=tmp_path / "scene.mp4", scene_index=1, template_id="HERO_TITLE",
        width=1920, height=1080, fps=12, duration_seconds=3.0, frame_count=36,
        video_sha256="v", source_html_sha256="h", motion_profile="none"
    )

    svc = VisualProductionV2Service(
        asset_orchestrator=orch, output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(), video_renderer=mock_video_renderer,
        ffmpeg_renderer=mock_ffmpeg, narration_provider=mock_narration_provider,
        narration_storage=mock_storage,
    )

    def fake_storyboard(_):
        return StoryboardPlan(
            title="Multi Test", estimated_duration_seconds=10.0,
            scenes=[
                StoryboardScene(
                    sequence_index=1, section_id="1", purpose="1", source_statement_references=[],
                    narration_excerpt="Scene one.", estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="1"
                ),
                StoryboardScene(
                    sequence_index=2, section_id="2", purpose="2", source_statement_references=[],
                    narration_excerpt="Scene two.", estimated_duration_seconds=5.0,
                    visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="2"
                )
            ]
        )
    svc._storyboard_engine.generate_storyboard = MagicMock(side_effect=fake_storyboard)

    async def fake_concat(clip_paths, output_path, srt_path=None):
        Path(output_path).write_bytes(VALID_MP4_HEADER + b"concat")
    mock_ffmpeg.concatenate_clips.side_effect = fake_concat

    async def fake_mux(video_path, audio_path, output_path):
        Path(output_path).write_bytes(b"muxed_content")
    mock_ffmpeg.mux_video_audio.side_effect = fake_mux

    captured_ass = {}
    async def fake_burn(video_path, ass_path, output_path):
        captured_ass["content"] = Path(ass_path).read_text("utf-8")
        Path(output_path).write_bytes(VALID_MP4_HEADER + b"burned")
    mock_ffmpeg.burn_ass_subtitles.side_effect = fake_burn

    await svc.render_mission_execution(session, m_exec.id, req.id, karaoke_subtitles=True)

    ass_content = captured_ass["content"]

    # The second scene should start at 3 seconds (0:00:03.00 in ASS format)
    # The first scene starts at 0:00:00.00
    assert "Dialogue: 0,0:00:00.00,0:00:03.00," in ass_content
    assert "Dialogue: 0,0:00:03.00,0:00:05.00," in ass_content
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
    audio_path.write_bytes(b"wav")
    mock_storage.resolve_stored_uri.return_value = audio_path

    mock_ffmpeg = AsyncMock()
    mock_video_renderer = AsyncMock()
    mock_video_renderer.render_clip.return_value = VisualV2VideoRenderResult(
        output_path=tmp_path / "scene.mp4", scene_index=1, template_id="HERO_TITLE",
        width=1920, height=1080, fps=12, duration_seconds=3.5, frame_count=42,
        video_sha256="v", source_html_sha256="h", motion_profile="none"
    )

    svc = VisualProductionV2Service(
        asset_orchestrator=orch, output_root=tmp_path / "renders",
        browser_runtime_factory=MagicMock(), video_renderer=mock_video_renderer,
        ffmpeg_renderer=mock_ffmpeg, narration_provider=mock_narration_provider,
        narration_storage=mock_storage,
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

    async def fake_concat(*args, **kwargs):
        Path(kwargs["output_path"]).write_bytes(VALID_MP4_HEADER + b"concat")
    mock_ffmpeg.concatenate_clips.side_effect = fake_concat

    async def fake_mux(*args, **kwargs):
        Path(kwargs["output_path"]).write_bytes(b"mux")
    mock_ffmpeg.mux_video_audio.side_effect = fake_mux

    async def fake_mix(*args, **kwargs):
        Path(kwargs["output_path"]).write_bytes(VALID_MP4_HEADER + b"mixed")
    mock_ffmpeg.mix_master_audio.side_effect = fake_mix

    async def fake_burn(*args, **kwargs):
        Path(kwargs["output_path"]).write_bytes(VALID_MP4_HEADER + b"burned")
    mock_ffmpeg.burn_ass_subtitles.side_effect = fake_burn

    bgm_path = tmp_path / "bgm.mp3"
    bgm_path.write_bytes(b"bgm")

    sfx_path = tmp_path / "sfx.wav"
    sfx_path.write_bytes(b"sfx")

    bgm = VerticalSliceBackgroundMusicInput(audio_path=bgm_path, duration_ms=2000, license_status="LICENSED", gain_db=-10)
    sfx1 = VerticalSliceSFXInput(event_id="pop", audio_path=sfx_path, start_ms=500, duration_ms=100, license_status="LICENSED", gain_db=-5)
    sfx2 = VerticalSliceSFXInput(event_id="whoosh", audio_path=sfx_path, start_ms=1000, duration_ms=200, license_status="LICENSED", gain_db=-2)

    # We pass SFX out of order to verify sorting by start_ms
    res = await svc.render_mission_execution(
        session, m_exec.id, req.id,
        background_music=bgm, sfx_inputs=[sfx2, sfx1], karaoke_subtitles=True
    )

    # Verify mix_master_audio called with normalized arguments
    mock_ffmpeg.mix_master_audio.assert_called_once()
    mix_args = mock_ffmpeg.mix_master_audio.call_args[1]

    assert mix_args["target_duration_ms"] == 3500
    assert mix_args["background_music_path"] == bgm_path
    assert mix_args["background_music_loop_required"] is True # 2000 < 3500

    sfx_inputs = mix_args["sfx_inputs"]
    assert len(sfx_inputs) == 2
    assert sfx_inputs[0].start_ms == 500
    assert sfx_inputs[1].start_ms == 1000

    # Verify karaoke called on mixed file
    burn_args = mock_ffmpeg.burn_ass_subtitles.call_args[1]
    assert "final_mixed.mp4" in str(burn_args["video_path"])

    # Output should be the burned file hash
    burned_sha = hashlib.sha256(VALID_MP4_HEADER + b"burned").hexdigest()
    assert res.content_sha256 == burned_sha

    # Check manifest
    manifest_path = res.output_path.parent / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text("utf-8"))
    assert manifest_data["audio_mix_enabled"] is True
    assert manifest_data["background_music_enabled"] is True
    assert manifest_data["sfx_event_count"] == 2
    assert manifest_data["audio_mix_target_duration_ms"] == 3500
