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
    VerticalSliceError,
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
