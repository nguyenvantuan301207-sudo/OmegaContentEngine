import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.storyboard_engine import (
    StoryboardScene,
    VisualStrategy,
)
from omega.application.visual_production_v2_service import (
    VisualProductionV2Service,
)
from omega.infrastructure.models import (
    ContentGenerationRequest,
    Mission,
    MissionExecution,
    ScriptSection,
    ScriptStatement,
    ScriptVersion,
)


@pytest.fixture
def mock_session():
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def mock_storage(tmp_path):
    storage = MagicMock()
    # Ensure resolve_stored_uri returns a valid file path
    dummy_audio = tmp_path / "dummy.wav"
    dummy_audio.write_bytes(b"dummy audio")
    storage.resolve_stored_uri.return_value = dummy_audio
    return storage


@pytest.fixture
def base_service_kwargs(tmp_path, mock_storage):
    ffmpeg_renderer = AsyncMock()

    async def mock_mux_video_audio(video_path, audio_path, output_path):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(
            b"\x00\x00\x00\x18ftypmp42"
            b"\x00\x00\x00\x00mp42isom"
            b"muxed"
        )

    ffmpeg_renderer.mux_video_audio.side_effect = mock_mux_video_audio

    return {
        "asset_orchestrator": AsyncMock(),
        "output_root": tmp_path,
        "browser_runtime_factory": MagicMock(),
        "video_renderer": AsyncMock(),
        "ffmpeg_renderer": ffmpeg_renderer,
        "narration_provider": MagicMock(model="mock-model", default_voice="mock-voice", synthesize_segment_audio=AsyncMock()),
        "narration_storage": mock_storage,
    }


def _setup_mock_db(mock_session, execution_id, request_id):
    mission = Mission(id=uuid.uuid4(), channel_id="test-chan")
    exec_record = MissionExecution(
        id=execution_id,
        mission_id=mission.id,
        channel_dna_revision_id=uuid.uuid4(),
    )
    exec_record.mission = mission

    mock_exec_res = MagicMock()
    mock_exec_res.scalar_one_or_none.return_value = exec_record

    content_req = ContentGenerationRequest(
        id=request_id,
        mission_execution_id=execution_id,
        channel_id=mission.channel_id,
        channel_dna_revision_id=exec_record.channel_dna_revision_id,
    )

    script_version = ScriptVersion(
        id=uuid.uuid4(),
        content_request_id=request_id,
        version=1,
        title="Test Script",
        estimated_duration_seconds=10,
        hook_text="hook",
        closing_text="close",
        cta_text="cta",
    )

    section = ScriptSection(
        id=uuid.uuid4(),
        script_version_id=script_version.id,
        section_order=1,
        heading="heading",
        narration_text="hello world",
        estimated_duration_seconds=10,
    )

    statement = ScriptStatement(
        id=uuid.uuid4(),
        script_section_id=section.id,
        statement_order=1,
        statement_text="hello world",
        statement_type="FACT",
    )
    statement.citations = []
    section.statements = [statement]
    script_version.sections = [section]

    content_req.scripts = [script_version]

    mock_req_res = MagicMock()
    mock_req_res.scalar_one_or_none.return_value = content_req

    mock_session.execute.side_effect = [mock_exec_res, mock_req_res]

    return script_version


@pytest.mark.asyncio
async def test_provenance_neural_production(mock_session, base_service_kwargs):
    service = VisualProductionV2Service(**base_service_kwargs)

    # Mock video and ffmpeg
    base_service_kwargs["video_renderer"].render_clip.return_value = MagicMock(
        output_path=base_service_kwargs["output_root"] / "clip.mp4",
        video_sha256="abc",
        template_id="test",
        duration_seconds=2.0
    )
    # Ensure clip.mp4 exists
    (base_service_kwargs["output_root"] / "clip.mp4").write_bytes(b"ftyp dummy video")

    # Ensure final temp exists
    # Wait, the code creates it inside the run dir work dir, we'll mock the concatenate
    async def mock_concat(clip_paths, output_path, srt_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"ftyp dummy final")
    base_service_kwargs["ffmpeg_renderer"].concatenate_clips.side_effect = mock_concat

    # Mock narration
    base_service_kwargs["narration_provider"].synthesize_segment_audio.return_value = {
        "storage_uri": "s3://audio/1",
        "duration_ms": 2000,
        "content_hash": "hash123",
        "source_ref": "Gemini TTS (voice: Kore)",
        "narration_quality": "NEURAL_PRODUCTION"
    }

    exec_id = uuid.uuid4()
    req_id = uuid.uuid4()
    _setup_mock_db(mock_session, exec_id, req_id)

    with patch("omega.application.visual_production_v2_service.StoryboardEngine.generate_storyboard") as mock_gen:
        mock_plan = MagicMock()
        scene = StoryboardScene(
            sequence_index=1,
            section_id="test-section-1",
            purpose="Narration provenance test",
            source_statement_references=[],
            narration_excerpt="hello",
            estimated_duration_seconds=2.0,
            visual_strategy=VisualStrategy.TITLE_MOTION,
            visual_brief="Narration provenance test",
            asset_query_hint=None,
        )
        mock_plan.scenes = [scene]
        mock_gen.return_value = mock_plan

        with patch.object(service, "_ensure_meaningful_query"):
            res = await service.render_mission_execution(mock_session, exec_id, req_id)

            assert res.narration_quality == "NEURAL_PRODUCTION"
            assert res.narration_source_refs == ("Gemini TTS (voice: Kore)",)

            # Check manifest
            manifest_path = res.output_path.parent / "manifest.json"
            assert manifest_path.exists()
            data = json.loads(manifest_path.read_text())
            assert data["narration_quality"] == "NEURAL_PRODUCTION"
            assert data["narration_source_refs"] == ["Gemini TTS (voice: Kore)"]


@pytest.mark.asyncio
async def test_provenance_development_fallback_dominates(mock_session, base_service_kwargs):
    service = VisualProductionV2Service(**base_service_kwargs)

    base_service_kwargs["video_renderer"].render_clip.return_value = MagicMock(
        output_path=base_service_kwargs["output_root"] / "clip.mp4",
        video_sha256="abc",
        template_id="test",
        duration_seconds=2.0
    )
    (base_service_kwargs["output_root"] / "clip.mp4").write_bytes(b"ftyp dummy video")

    async def mock_concat(clip_paths, output_path, srt_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"ftyp dummy final")
    base_service_kwargs["ffmpeg_renderer"].concatenate_clips.side_effect = mock_concat

    # 2 segments: one neural, one fallback
    base_service_kwargs["narration_provider"].synthesize_segment_audio.side_effect = [
        {
            "storage_uri": "s3://audio/1",
            "duration_ms": 2000,
            "content_hash": "hash123",
            "source_ref": "Gemini TTS (voice: Kore)",
            "narration_quality": "NEURAL_PRODUCTION"
        },
        {
            "storage_uri": "s3://audio/2",
            "duration_ms": 2000,
            "content_hash": "hash456",
            "source_ref": "Local TTS",
            "narration_quality": "DEVELOPMENT_FALLBACK"
        }
    ]

    exec_id = uuid.uuid4()
    req_id = uuid.uuid4()
    _setup_mock_db(mock_session, exec_id, req_id)

    with patch("omega.application.visual_production_v2_service.StoryboardEngine.generate_storyboard") as mock_gen:
        mock_plan = MagicMock()

        scene1 = StoryboardScene(
            sequence_index=1,
            section_id="test-section-1",
            purpose="Narration provenance test",
            source_statement_references=[],
            narration_excerpt="hello",
            estimated_duration_seconds=2.0,
            visual_strategy=VisualStrategy.TITLE_MOTION,
            visual_brief="Narration provenance test",
            asset_query_hint=None,
        )

        scene2 = StoryboardScene(
            sequence_index=2,
            section_id="test-section-2",
            purpose="Narration provenance test",
            source_statement_references=[],
            narration_excerpt="world",
            estimated_duration_seconds=2.0,
            visual_strategy=VisualStrategy.TITLE_MOTION,
            visual_brief="Narration provenance test",
            asset_query_hint=None,
        )

        mock_plan.scenes = [scene1, scene2]
        mock_gen.return_value = mock_plan

        with patch.object(service, "_ensure_meaningful_query"):
            res = await service.render_mission_execution(mock_session, exec_id, req_id)

            assert res.narration_quality == "DEVELOPMENT_FALLBACK"
            # 3. Deterministic source-ref dedupe
            assert res.narration_source_refs == ("Gemini TTS (voice: Kore)", "Local TTS")


@pytest.mark.asyncio
async def test_provenance_deduplication(mock_session, base_service_kwargs):
    service = VisualProductionV2Service(**base_service_kwargs)

    base_service_kwargs["video_renderer"].render_clip.return_value = MagicMock(
        output_path=base_service_kwargs["output_root"] / "clip.mp4",
        video_sha256="abc",
        template_id="test",
        duration_seconds=2.0
    )
    (base_service_kwargs["output_root"] / "clip.mp4").write_bytes(b"ftyp dummy video")

    async def mock_concat(clip_paths, output_path, srt_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"ftyp dummy final")
    base_service_kwargs["ffmpeg_renderer"].concatenate_clips.side_effect = mock_concat

    # 3 segments: Same source ref
    base_service_kwargs["narration_provider"].synthesize_segment_audio.side_effect = [
        {
            "storage_uri": "s3://audio/1",
            "duration_ms": 2000,
            "content_hash": "hash123",
            "source_ref": "Gemini TTS (voice: Kore)",
            "narration_quality": "NEURAL_PRODUCTION"
        },
        {
            "storage_uri": "s3://audio/2",
            "duration_ms": 2000,
            "content_hash": "hash456",
            "source_ref": "Gemini TTS (voice: Aoede)",
            "narration_quality": "NEURAL_PRODUCTION"
        },
        {
            "storage_uri": "s3://audio/3",
            "duration_ms": 2000,
            "content_hash": "hash789",
            "source_ref": "Gemini TTS (voice: Kore)",
            "narration_quality": "NEURAL_PRODUCTION"
        }
    ]

    exec_id = uuid.uuid4()
    req_id = uuid.uuid4()
    _setup_mock_db(mock_session, exec_id, req_id)

    with patch("omega.application.visual_production_v2_service.StoryboardEngine.generate_storyboard") as mock_gen:
        mock_plan = MagicMock()

        s1 = StoryboardScene(
            sequence_index=1,
            section_id="test-section-1",
            purpose="Narration provenance test",
            source_statement_references=[],
            narration_excerpt="1",
            estimated_duration_seconds=2.0,
            visual_strategy=VisualStrategy.TITLE_MOTION,
            visual_brief="Narration provenance test",
            asset_query_hint=None,
        )

        s2 = StoryboardScene(
            sequence_index=2,
            section_id="test-section-2",
            purpose="Narration provenance test",
            source_statement_references=[],
            narration_excerpt="2",
            estimated_duration_seconds=2.0,
            visual_strategy=VisualStrategy.TITLE_MOTION,
            visual_brief="Narration provenance test",
            asset_query_hint=None,
        )

        s3 = StoryboardScene(
            sequence_index=3,
            section_id="test-section-3",
            purpose="Narration provenance test",
            source_statement_references=[],
            narration_excerpt="3",
            estimated_duration_seconds=2.0,
            visual_strategy=VisualStrategy.TITLE_MOTION,
            visual_brief="Narration provenance test",
            asset_query_hint=None,
        )

        mock_plan.scenes = [s1, s2, s3]
        mock_gen.return_value = mock_plan

        with patch.object(service, "_ensure_meaningful_query"):
            res = await service.render_mission_execution(mock_session, exec_id, req_id)

            assert res.narration_quality == "NEURAL_PRODUCTION"
            # order preserved, deduped
            assert res.narration_source_refs == ("Gemini TTS (voice: Kore)", "Gemini TTS (voice: Aoede)")


@pytest.mark.asyncio
async def test_provenance_backward_compatibility(mock_session, base_service_kwargs):
    service = VisualProductionV2Service(**base_service_kwargs)

    exec_id = uuid.uuid4()
    req_id = uuid.uuid4()
    _setup_mock_db(mock_session, exec_id, req_id)

    # Fake a previous run fingerprint

    # We actually need the REAL run fingerprint the method computes
    # Wait, because we mocked narration provider, the fingerprint includes narrated...
    # We need to construct the exact fingerprint.
    # We can just let the method fail with "Incomplete run" and see what fingerprint it wanted, OR we mock the compute_streaming_sha.
    pass

    # A better way: Use patch on _compute_streaming_sha and just provide the manifest.
    with patch("omega.application.visual_production_v2_service.hashlib.sha256") as mock_sha:
        mock_hash = MagicMock()
        mock_hash.hexdigest.return_value = "fake_fingerprint"
        mock_sha.return_value = mock_hash

        run_dir = base_service_kwargs["output_root"] / str(exec_id) / "fake_fingerprint"
        run_dir.mkdir(parents=True, exist_ok=True)

        final_mp4 = run_dir / "final.mp4"
        final_mp4.write_bytes(b"ftyp dummy")

        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "run_fingerprint": "fake_fingerprint",
            "content_sha256": "fake_sha",
            "scene_count": 1,
            "template_scene_count": 1,
            "image_scene_count": 0,
            "broll_scene_count": 0,
            "duration_seconds": 2.0,
            "width": 1920,
            "height": 1080,
            "fps": 12
            # Notice NO narration_quality or narration_source_refs
        }))

        with patch.object(service, "_compute_streaming_sha", return_value="fake_sha"):
            res = await service.render_mission_execution(mock_session, exec_id, req_id)

            # 4. Backward compatibility
            assert res.narration_quality is None
            assert res.narration_source_refs == ()


@pytest.mark.asyncio
async def test_provenance_actual_duration_overrides_estimates(mock_session, base_service_kwargs):
    service = VisualProductionV2Service(**base_service_kwargs)

    base_service_kwargs["video_renderer"].render_clip.return_value = MagicMock(
        output_path=base_service_kwargs["output_root"] / "clip.mp4",
        video_sha256="abc",
        template_id="test",
        duration_seconds=2.0
    )
    (base_service_kwargs["output_root"] / "clip.mp4").write_bytes(b"ftyp dummy video")

    async def mock_concat(clip_paths, output_path, srt_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"ftyp dummy final")
    base_service_kwargs["ffmpeg_renderer"].concatenate_clips.side_effect = mock_concat

    base_service_kwargs["narration_provider"].synthesize_segment_audio.side_effect = [
        {"storage_uri": "s3://audio/1", "duration_ms": 2000, "content_hash": "h1"},
        {"storage_uri": "s3://audio/2", "duration_ms": 3000, "content_hash": "h2"}
    ]

    exec_id = uuid.uuid4()
    req_id = uuid.uuid4()
    _setup_mock_db(mock_session, exec_id, req_id)

    with patch("omega.application.visual_production_v2_service.StoryboardEngine.generate_storyboard") as mock_gen:
        mock_plan = MagicMock()
        mock_plan.scenes = [
            StoryboardScene(sequence_index=1, section_id="sec1", purpose="test", source_statement_references=[], narration_excerpt="hello", estimated_duration_seconds=5.0, visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="test", asset_query_hint=None),
            StoryboardScene(sequence_index=2, section_id="sec2", purpose="test", source_statement_references=[], narration_excerpt="world", estimated_duration_seconds=5.0, visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="test", asset_query_hint=None)
        ]
        mock_gen.return_value = mock_plan

        with patch.object(service, "_ensure_meaningful_query"):
            res = await service.render_mission_execution(mock_session, exec_id, req_id)

            assert res.runtime_timeline_duration_ms == 5000
            assert len(res.runtime_narration_segments) == 2

            seg1, seg2 = res.runtime_narration_segments
            assert seg1.scene_index == 1
            assert seg1.start_ms == 0
            assert seg1.end_ms == 2000
            assert seg1.duration_ms == 2000

            assert seg2.scene_index == 2
            assert seg2.start_ms == 2000
            assert seg2.end_ms == 5000
            assert seg2.duration_ms == 3000


@pytest.mark.asyncio
async def test_provenance_global_runtime_karaoke_timeline(mock_session, base_service_kwargs):
    service = VisualProductionV2Service(**base_service_kwargs)

    base_service_kwargs["video_renderer"].render_clip.return_value = MagicMock(
        output_path=base_service_kwargs["output_root"] / "clip.mp4",
        video_sha256="abc",
        template_id="test",
        duration_seconds=2.0
    )
    (base_service_kwargs["output_root"] / "clip.mp4").write_bytes(b"ftyp dummy video")

    async def mock_concat(clip_paths, output_path, srt_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"ftyp dummy final")
    base_service_kwargs["ffmpeg_renderer"].concatenate_clips.side_effect = mock_concat
    base_service_kwargs["ffmpeg_renderer"].burn_ass_subtitles = AsyncMock()

    base_service_kwargs["ffmpeg_renderer"].burn_ass_subtitles.side_effect = lambda video_path, ass_path, output_path: Path(output_path).write_bytes(b"fake video content")

    base_service_kwargs["narration_provider"].synthesize_segment_audio.side_effect = [
        {"storage_uri": "s3://audio/1", "duration_ms": 2000, "content_hash": "h1"},
        {"storage_uri": "s3://audio/2", "duration_ms": 3000, "content_hash": "h2"}
    ]

    exec_id = uuid.uuid4()
    req_id = uuid.uuid4()
    _setup_mock_db(mock_session, exec_id, req_id)

    with patch("omega.application.visual_production_v2_service.StoryboardEngine.generate_storyboard") as mock_gen, \
         patch("omega.application.visual_production_v2_service.generate_karaoke_cues") as mock_cues:

        mock_plan = MagicMock()
        mock_plan.scenes = [
            StoryboardScene(sequence_index=1, section_id="sec1", purpose="test", source_statement_references=[], narration_excerpt="hello", estimated_duration_seconds=2.0, visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="test", asset_query_hint=None),
            StoryboardScene(sequence_index=2, section_id="sec2", purpose="test", source_statement_references=[], narration_excerpt="world", estimated_duration_seconds=3.0, visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="test", asset_query_hint=None)
        ]
        mock_gen.return_value = mock_plan

        mock_cues.side_effect = [
            [{
                "cue_order": 1,
                "text": "hello",
                "start_ms": 0,
                "end_ms": 1500,
                "words": [{"text": "hello", "duration_ms": 1500}]
            }],
            [{
                "cue_order": 1,
                "text": "world",
                "start_ms": 500,
                "end_ms": 2500,
                "words": [{"text": "world", "duration_ms": 2000}]
            }]
        ]

        with patch.object(service, "_ensure_meaningful_query"):
            res = await service.render_mission_execution(mock_session, exec_id, req_id, subtitle_enabled=True)

            assert res.runtime_timeline_duration_ms == 5000
            assert len(res.runtime_subtitle_cues) == 2

            cue1, cue2 = res.runtime_subtitle_cues

            assert cue1.cue_order == 1
            assert cue1.start_ms == 0
            assert cue1.end_ms == 1500
            assert cue1.text == "hello"

            assert cue2.cue_order == 2
            assert cue2.start_ms == 2500
            assert cue2.end_ms == 4500
            assert cue2.text == "world"


@pytest.mark.asyncio
async def test_provenance_fallback_mixed_runtime_durations(mock_session, base_service_kwargs):
    service = VisualProductionV2Service(**base_service_kwargs)

    base_service_kwargs["video_renderer"].render_clip.return_value = MagicMock(
        output_path=base_service_kwargs["output_root"] / "clip.mp4",
        video_sha256="abc",
        template_id="test",
        duration_seconds=2.0
    )
    (base_service_kwargs["output_root"] / "clip.mp4").write_bytes(b"ftyp dummy video")

    async def mock_concat(clip_paths, output_path, srt_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"ftyp dummy final")
    base_service_kwargs["ffmpeg_renderer"].concatenate_clips.side_effect = mock_concat

    base_service_kwargs["narration_provider"].synthesize_segment_audio.side_effect = [
        {"storage_uri": "s3://audio/1", "duration_ms": 1500, "content_hash": "h1", "narration_quality": "DEVELOPMENT_FALLBACK"},
        {"storage_uri": "s3://audio/2", "duration_ms": 4000, "content_hash": "h2", "narration_quality": "NEURAL_PRODUCTION"},
        {"storage_uri": "s3://audio/3", "duration_ms": 1000, "content_hash": "h3", "narration_quality": "NEURAL_PRODUCTION"}
    ]

    exec_id = uuid.uuid4()
    req_id = uuid.uuid4()
    _setup_mock_db(mock_session, exec_id, req_id)

    with patch("omega.application.visual_production_v2_service.StoryboardEngine.generate_storyboard") as mock_gen:
        mock_plan = MagicMock()
        mock_plan.scenes = [
            StoryboardScene(sequence_index=1, section_id="sec1", purpose="test", source_statement_references=[], narration_excerpt="hello", estimated_duration_seconds=2.0, visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="test", asset_query_hint=None),
            StoryboardScene(sequence_index=2, section_id="sec2", purpose="test", source_statement_references=[], narration_excerpt="world", estimated_duration_seconds=2.0, visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="test", asset_query_hint=None),
            StoryboardScene(sequence_index=3, section_id="sec3", purpose="test", source_statement_references=[], narration_excerpt="foo", estimated_duration_seconds=2.0, visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="test", asset_query_hint=None)
        ]
        mock_gen.return_value = mock_plan

        with patch.object(service, "_ensure_meaningful_query"):
            res = await service.render_mission_execution(mock_session, exec_id, req_id)

            assert res.runtime_timeline_duration_ms == 6500
            assert len(res.runtime_narration_segments) == 3

            s1, s2, s3 = res.runtime_narration_segments
            assert s1.start_ms == 0 and s1.end_ms == 1500
            assert s2.start_ms == 1500 and s2.end_ms == 5500
            assert s3.start_ms == 5500 and s3.end_ms == 6500


@pytest.mark.asyncio
async def test_provenance_subtitles_disabled_narration_present(mock_session, base_service_kwargs):
    service = VisualProductionV2Service(**base_service_kwargs)

    base_service_kwargs["video_renderer"].render_clip.return_value = MagicMock(
        output_path=base_service_kwargs["output_root"] / "clip.mp4",
        video_sha256="abc",
        template_id="test",
        duration_seconds=2.0
    )
    (base_service_kwargs["output_root"] / "clip.mp4").write_bytes(b"ftyp dummy video")

    async def mock_concat(clip_paths, output_path, srt_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"ftyp dummy final")
    base_service_kwargs["ffmpeg_renderer"].concatenate_clips.side_effect = mock_concat

    base_service_kwargs["narration_provider"].synthesize_segment_audio.side_effect = [
        {"storage_uri": "s3://audio/1", "duration_ms": 2000, "content_hash": "h1"}
    ]

    exec_id = uuid.uuid4()
    req_id = uuid.uuid4()
    _setup_mock_db(mock_session, exec_id, req_id)

    with patch("omega.application.visual_production_v2_service.StoryboardEngine.generate_storyboard") as mock_gen:
        mock_plan = MagicMock()
        mock_plan.scenes = [
            StoryboardScene(sequence_index=1, section_id="sec1", purpose="test", source_statement_references=[], narration_excerpt="hello", estimated_duration_seconds=2.0, visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="test", asset_query_hint=None)
        ]
        mock_gen.return_value = mock_plan

        with patch.object(service, "_ensure_meaningful_query"):
            res = await service.render_mission_execution(mock_session, exec_id, req_id, subtitle_enabled=False)

            assert res.runtime_timeline_duration_ms == 2000
            assert len(res.runtime_narration_segments) == 1
            assert res.runtime_subtitle_cues == ()


@pytest.mark.asyncio
async def test_provenance_old_manifest_backward_compatibility(mock_session, base_service_kwargs):
    service = VisualProductionV2Service(**base_service_kwargs)
    exec_id = uuid.uuid4()
    req_id = uuid.uuid4()
    _setup_mock_db(mock_session, exec_id, req_id)

    with patch("omega.application.visual_production_v2_service.hashlib.sha256") as mock_sha:
        mock_hash = MagicMock()
        mock_hash.hexdigest.return_value = "fake_fingerprint_2"
        mock_sha.return_value = mock_hash

        run_dir = base_service_kwargs["output_root"] / str(exec_id) / "fake_fingerprint_2"
        run_dir.mkdir(parents=True, exist_ok=True)

        final_mp4 = run_dir / "final.mp4"
        final_mp4.write_bytes(b"ftyp dummy")

        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "run_fingerprint": "fake_fingerprint_2",
            "content_sha256": "fake_sha",
            "scene_count": 1,
            "template_scene_count": 1,
            "image_scene_count": 0,
            "broll_scene_count": 0,
            "duration_seconds": 2.0,
            "width": 1920,
            "height": 1080,
            "fps": 12
        }))

        with patch.object(service, "_compute_streaming_sha", return_value="fake_sha"):
            res = await service.render_mission_execution(mock_session, exec_id, req_id)

            assert res.runtime_timeline_duration_ms is None
            assert res.runtime_narration_segments == ()
            assert res.runtime_subtitle_cues == ()


@pytest.mark.asyncio
async def test_provenance_fresh_manifest_persistence(mock_session, base_service_kwargs):
    service = VisualProductionV2Service(**base_service_kwargs)

    base_service_kwargs["video_renderer"].render_clip.return_value = MagicMock(
        output_path=base_service_kwargs["output_root"] / "clip.mp4",
        video_sha256="abc",
        template_id="test",
        duration_seconds=2.0
    )
    (base_service_kwargs["output_root"] / "clip.mp4").write_bytes(b"ftyp dummy video")

    async def mock_concat(clip_paths, output_path, srt_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"ftyp dummy final")
    base_service_kwargs["ffmpeg_renderer"].concatenate_clips.side_effect = mock_concat

    base_service_kwargs["narration_provider"].synthesize_segment_audio.side_effect = [
        {"storage_uri": "s3://audio/1", "duration_ms": 2000, "content_hash": "h1"}
    ]

    exec_id = uuid.uuid4()
    req_id = uuid.uuid4()
    _setup_mock_db(mock_session, exec_id, req_id)

    with patch("omega.application.visual_production_v2_service.StoryboardEngine.generate_storyboard") as mock_gen:
        mock_plan = MagicMock()
        mock_plan.scenes = [
            StoryboardScene(sequence_index=1, section_id="sec1", purpose="test", source_statement_references=[], narration_excerpt="hello", estimated_duration_seconds=2.0, visual_strategy=VisualStrategy.TITLE_MOTION, visual_brief="test", asset_query_hint=None)
        ]
        mock_gen.return_value = mock_plan

        with patch.object(service, "_ensure_meaningful_query"):
            res = await service.render_mission_execution(mock_session, exec_id, req_id)

            manifest_path = res.output_path.parent / "manifest.json"
            assert manifest_path.exists()
            data = json.loads(manifest_path.read_text())

            assert data["runtime_timeline_duration_ms"] == 2000
            assert len(data["runtime_narration_segments"]) == 1
            assert data["runtime_narration_segments"][0]["duration_ms"] == 2000
            assert data["runtime_subtitle_cues"] == []
