import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.ffmpeg_renderer import FFmpegExecutionError
from omega.application.render_service import ProductionRenderService, _classify_phase2_error
from omega.application.subtitle_engine import SubtitleRenderStyle
from omega.application.template_payload_resolver import TemplatePayloadError
from omega.domain.production import RenderErrorCode, RenderJobState
from omega.infrastructure.models import ProductionRequest


def create_fake_mp4(path: Path, content: bytes = b"ftyp...mp4data") -> str:
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def mock_v2_service():
    return AsyncMock()


@pytest.fixture
def render_service(tmp_path, mock_v2_service):
    from omega.application.media_storage import LocalMediaStorageProvider
    storage = LocalMediaStorageProvider(base_root=str(tmp_path))
    return ProductionRenderService(storage=storage, visual_production_service=mock_v2_service)


@pytest.mark.asyncio
async def test_resolve_mission_id_direct_fk(render_service):
    execution_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    req = ProductionRequest(
        mission_execution_id=execution_id,
        content_request_id=uuid.uuid4(),
    )

    result = MagicMock()
    result.scalar_one_or_none.return_value = mission_id
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    resolved = await render_service._resolve_mission_id(session, req)

    assert resolved == mission_id
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_mission_id_content_request_fallback(render_service):
    content_request_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    req = ProductionRequest(
        mission_execution_id=None,
        content_request_id=content_request_id,
    )

    execution_result = MagicMock()
    execution_result.scalar_one_or_none.return_value = execution_id
    mission_result = MagicMock()
    mission_result.scalar_one_or_none.return_value = mission_id

    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [execution_result, mission_result]

    resolved = await render_service._resolve_mission_id(session, req)

    assert resolved == mission_id
    assert session.execute.await_count == 2


def test_selection_interactive_no_v2():
    req = ProductionRequest(mode="INTERACTIVE")
    svc = ProductionRenderService(visual_production_service=AsyncMock())
    assert svc._should_use_v2(req) is False


def test_selection_mission_no_v2():
    req = ProductionRequest(mode="MISSION_EXECUTION")
    svc = ProductionRenderService(visual_production_service=None)
    assert svc._should_use_v2(req) is False


def test_selection_mission_with_v2():
    req = ProductionRequest(mode="MISSION_EXECUTION")
    svc = ProductionRenderService(visual_production_service=AsyncMock())
    assert svc._should_use_v2(req) is True


def test_phase2_template_payload_error_is_input_invalid():
    error = TemplatePayloadError("Could not extract a trustworthy metric.")

    assert _classify_phase2_error(error) == RenderErrorCode.INPUT_INVALID


def test_phase2_ffmpeg_error_remains_ffmpeg_failed():
    error = FFmpegExecutionError("FFmpeg scene render failed")

    assert _classify_phase2_error(error) == RenderErrorCode.FFMPEG_FAILED


class _FailureResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


@pytest.mark.asyncio
async def test_record_job_failure_sets_terminal_timestamp_once(render_service):
    request_id = uuid.uuid4()
    job_id = uuid.uuid4()
    job = SimpleNamespace(
        id=job_id,
        production_request_id=request_id,
        state=RenderJobState.RUNNING.value,
        error_code=None,
        sanitized_error=None,
        completed_at=None,
    )
    request = SimpleNamespace(id=request_id)
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = _FailureResult(job)
    session.get.return_value = request
    render_service._enqueue_terminal_evaluation = AsyncMock()

    await render_service._record_job_failure(
        session,
        job_id,
        RenderErrorCode.INPUT_INVALID,
        "Could not extract a trustworthy metric.",
    )

    assert job.state == RenderJobState.FAILED.value
    assert job.error_code == RenderErrorCode.INPUT_INVALID.value
    assert job.sanitized_error == "Could not extract a trustworthy metric."
    assert isinstance(job.completed_at, datetime)
    completed_at = job.completed_at
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    render_service._enqueue_terminal_evaluation.assert_awaited_once_with(
        session, request, job_id
    )

    session.reset_mock()
    render_service._enqueue_terminal_evaluation.reset_mock()
    await render_service._record_job_failure(
        session,
        job_id,
        RenderErrorCode.FFMPEG_FAILED,
        "replayed failure",
    )

    assert job.completed_at is completed_at
    assert job.error_code == RenderErrorCode.INPUT_INVALID.value
    assert job.sanitized_error == "Could not extract a trustworthy metric."
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    render_service._enqueue_terminal_evaluation.assert_not_awaited()


@pytest.mark.asyncio
async def test_v2_helper_success(render_service, tmp_path, mock_v2_service):
    req = ProductionRequest(
        mission_execution_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        voice_profile={"provider": "test"},
    )
    staging_out = tmp_path / "staging.mp4"

    v2_out = tmp_path / "v2_final.mp4"
    sha = create_fake_mp4(v2_out)

    class FakeResult:
        mission_execution_id = req.mission_execution_id
        content_request_id = req.content_request_id
        width = 1920
        height = 1080
        fps = 30
        output_path = str(v2_out)
        content_sha256 = sha

    mock_v2_service.render_mission_execution.return_value = FakeResult()

    session = AsyncMock(spec=AsyncSession)
    await render_service._render_v2_staging(
        session, req, 30, 1920, 1080, "mp4", "h264", staging_out
    )

    mock_v2_service.render_mission_execution.assert_called_once_with(
        session,
        req.mission_execution_id,
        req.content_request_id,
        fps=30,
        voice_profile=req.voice_profile,
        subtitle_enabled=True,
        subtitle_style=SubtitleRenderStyle(),
        style_profile=None,
    )

    assert staging_out.exists()
    assert v2_out.exists()

    from omega.application.media_storage import compute_sha256
    assert compute_sha256(staging_out) == sha
    assert compute_sha256(v2_out) == sha


@pytest.mark.asyncio
async def test_v2_helper_sha_mismatch(render_service, tmp_path, mock_v2_service):
    req = ProductionRequest(
        mission_execution_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
    )
    staging_out = tmp_path / "staging.mp4"
    v2_out = tmp_path / "v2_final.mp4"
    create_fake_mp4(v2_out)

    class FakeResult:
        mission_execution_id = req.mission_execution_id
        content_request_id = req.content_request_id
        width = 1920
        height = 1080
        fps = 30
        output_path = str(v2_out)
        content_sha256 = "bad_sha"

    mock_v2_service.render_mission_execution.return_value = FakeResult()

    with pytest.raises(ValueError, match="V2 output SHA mismatch"):
        await render_service._render_v2_staging(
            AsyncMock(), req, 30, 1920, 1080, "mp4", "h264", staging_out
        )


@pytest.mark.asyncio
async def test_v2_helper_lineage_mismatch(render_service, tmp_path, mock_v2_service):
    req = ProductionRequest(
        mission_execution_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
    )
    staging_out = tmp_path / "staging.mp4"

    class FakeResult:
        mission_execution_id = uuid.uuid4()
        content_request_id = req.content_request_id

    mock_v2_service.render_mission_execution.return_value = FakeResult()

    with pytest.raises(ValueError, match="V2 result mission_execution_id mismatch"):
        await render_service._render_v2_staging(
            AsyncMock(), req, 30, 1920, 1080, "mp4", "h264", staging_out
        )


@pytest.mark.asyncio
async def test_v2_helper_missing_output(render_service, tmp_path, mock_v2_service):
    req = ProductionRequest(
        mission_execution_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
    )
    staging_out = tmp_path / "staging.mp4"

    class FakeResult:
        mission_execution_id = req.mission_execution_id
        content_request_id = req.content_request_id
        width = 1920
        height = 1080
        fps = 30
        output_path = str(tmp_path / "nonexistent.mp4")

    mock_v2_service.render_mission_execution.return_value = FakeResult()

    with pytest.raises(ValueError, match="V2 output artifact missing"):
        await render_service._render_v2_staging(
            AsyncMock(), req, 30, 1920, 1080, "mp4", "h264", staging_out
        )


@pytest.mark.asyncio
async def test_v2_helper_unsupported_target(render_service, tmp_path):
    req = ProductionRequest(
        mission_execution_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
    )
    staging_out = tmp_path / "staging.mp4"

    with pytest.raises(ValueError, match="V2 unsupported resolution"):
        await render_service._render_v2_staging(
            AsyncMock(), req, 30, 1280, 720, "mp4", "h264", staging_out
        )

    with pytest.raises(ValueError, match="V2 unsupported container"):
        await render_service._render_v2_staging(
            AsyncMock(), req, 30, 1920, 1080, "mov", "h264", staging_out
        )


@pytest.mark.asyncio
async def test_v2_helper_exception_propagates(render_service, tmp_path, mock_v2_service):
    req = ProductionRequest(
        mission_execution_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
    )
    mock_v2_service.render_mission_execution.side_effect = RuntimeError("V2 Failed")

    with pytest.raises(RuntimeError, match="V2 Failed"):
        await render_service._render_v2_staging(
            AsyncMock(), req, 30, 1920, 1080, "mp4", "h264", tmp_path / "staging.mp4"
        )


@pytest.mark.asyncio
async def test_v2_helper_dimension_fps_mismatch(render_service, tmp_path, mock_v2_service):
    req = ProductionRequest(
        mission_execution_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
    )
    staging_out = tmp_path / "staging.mp4"
    v2_out = tmp_path / "v2_final.mp4"
    create_fake_mp4(v2_out)

    class FakeResult:
        mission_execution_id = req.mission_execution_id
        content_request_id = req.content_request_id
        width = 1280
        height = 1080
        fps = 30
        output_path = str(v2_out)
        content_sha256 = "123"

    mock_v2_service.render_mission_execution.return_value = FakeResult()

    with pytest.raises(ValueError, match="V2 result dimension mismatch"):
        await render_service._render_v2_staging(
            AsyncMock(), req, 30, 1920, 1080, "mp4", "h264", staging_out
        )


@pytest.mark.asyncio
async def test_v2_helper_returns_runtime_provenance(render_service, tmp_path, mock_v2_service):
    selected_style = SubtitleRenderStyle(
        font_size=54,
        primary_color="#FFD400",
        outline_width=3,
        margin_v=135,
    )
    req = ProductionRequest(
        mission_execution_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        metadata_={"render_settings": {"subtitle_style": selected_style.model_dump()}},
    )
    staging_out = tmp_path / "staging.mp4"
    v2_out = tmp_path / "v2_final.mp4"
    sha = create_fake_mp4(v2_out)

    class FakeResult:
        mission_execution_id = req.mission_execution_id
        content_request_id = req.content_request_id
        width = 1920
        height = 1080
        fps = 30
        output_path = str(v2_out)
        content_sha256 = sha
        narration_quality = "NEURAL_PRODUCTION"
        narration_source_refs = ("Gemini TTS",)
        runtime_timeline_duration_ms = 5000
        runtime_narration_segments = (
            {
                "scene_index": 1,
                "start_ms": 0,
                "end_ms": 2000,
                "duration_ms": 2000,
            },
            {
                "scene_index": 2,
                "start_ms": 2000,
                "end_ms": 5000,
                "duration_ms": 3000,
            },
        )
        runtime_subtitle_cues = (
            {
                "scene_index": 1,
                "cue_order": 1,
                "start_ms": 0,
                "end_ms": 1800,
                "text": "one",
            },
            {
                "scene_index": 2,
                "cue_order": 2,
                "start_ms": 2000,
                "end_ms": 4900,
                "text": "two",
            },
        )
        runtime_scenes = (
            {
                "sequence_index": 1,
                "original_strategy": "TITLE_MOTION",
                "effective_strategy": "TITLE_MOTION",
                "duration_seconds": 2.0,
                "text_truncated": True,
            },
        )
        subtitle_style_applied = selected_style
        effective_fps_mode = "CFR"

    mock_v2_service.render_mission_execution.return_value = FakeResult()
    session = AsyncMock(spec=AsyncSession)

    result = await render_service._render_v2_staging(
        session, req, 30, 1920, 1080, "mp4", "h264", staging_out
    )

    assert result == (
        "NEURAL_PRODUCTION",
        ("Gemini TTS",),
        5000,
        (
            {
                "scene_index": 1,
                "start_ms": 0,
                "end_ms": 2000,
                "duration_ms": 2000,
            },
            {
                "scene_index": 2,
                "start_ms": 2000,
                "end_ms": 5000,
                "duration_ms": 3000,
            },
        ),
        (
            {
                "scene_index": 1,
                "cue_order": 1,
                "start_ms": 0,
                "end_ms": 1800,
                "text": "one",
            },
            {
                "scene_index": 2,
                "cue_order": 2,
                "start_ms": 2000,
                "end_ms": 4900,
                "text": "two",
            },
        ),
        (
            {
                "sequence_index": 1,
                "original_strategy": "TITLE_MOTION",
                "effective_strategy": "TITLE_MOTION",
                "duration_seconds": 2.0,
                "text_truncated": True,
            },
        ),
        {
            "subtitle_style_applied": selected_style.model_dump(),
            "target_fps": 30,
            "effective_fps_mode": "CFR",
            "text_truncated": True,
            "scenes": [{"sequence_index": 1, "text_truncated": True}],
        },
    )

    call = mock_v2_service.render_mission_execution.call_args.kwargs
    assert call["subtitle_style"] == selected_style


@pytest.mark.asyncio
async def test_v2_helper_backward_compatibility(render_service, tmp_path, mock_v2_service):
    req = ProductionRequest(
        mission_execution_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
    )
    staging_out = tmp_path / "staging.mp4"
    v2_out = tmp_path / "v2_final.mp4"
    sha = create_fake_mp4(v2_out)

    class FakeResult:
        mission_execution_id = req.mission_execution_id
        content_request_id = req.content_request_id
        width = 1920
        height = 1080
        fps = 30
        output_path = str(v2_out)
        content_sha256 = sha

    mock_v2_service.render_mission_execution.return_value = FakeResult()
    session = AsyncMock(spec=AsyncSession)

    result = await render_service._render_v2_staging(
        session, req, 30, 1920, 1080, "mp4", "h264", staging_out
    )

    assert result == (
        None,
        (),
        None,
        (),
        (),
        (),
        {
            "subtitle_style_applied": None,
            "target_fps": 30,
            "effective_fps_mode": None,
            "text_truncated": False,
            "scenes": [],
        },
    )


def test_overlay_runtime_timeline_truth_overrides_prepared(render_service):
    prepared_narr = [
        {"id": "n1", "start_ms": 0, "end_ms": 5000},
        {"id": "n2", "start_ms": 5200, "end_ms": 10200},
    ]
    prepared_subs = [
        {"cue_order": 1, "start_ms": 0, "end_ms": 5000, "text": "one"},
        {"cue_order": 2, "start_ms": 5200, "end_ms": 10200, "text": "two"},
    ]
    runtime_duration = 5000
    runtime_narr = [
        {"scene_index": 1, "start_ms": 0, "end_ms": 2000, "duration_ms": 2000},
        {"scene_index": 2, "start_ms": 2000, "end_ms": 5000, "duration_ms": 3000},
    ]
    runtime_subs = [
        {"scene_index": 1, "cue_order": 1, "start_ms": 0, "end_ms": 1800, "text": "one"},
        {"scene_index": 2, "cue_order": 2, "start_ms": 2000, "end_ms": 4900, "text": "two"},
    ]

    qa_narr, qa_subs = render_service._overlay_runtime_timeline_truth(
        prepared_narr, prepared_subs, runtime_duration, runtime_narr, runtime_subs
    )

    assert len(qa_narr) == 2
    assert qa_narr[0]["start_ms"] == 0 and qa_narr[0]["end_ms"] == 2000
    assert qa_narr[1]["start_ms"] == 2000 and qa_narr[1]["end_ms"] == 5000
    assert not any(n["end_ms"] == 10200 for n in qa_narr)

    assert len(qa_subs) == 2
    assert qa_subs[0]["start_ms"] == 0 and qa_subs[0]["end_ms"] == 1800
    assert qa_subs[1]["start_ms"] == 2000 and qa_subs[1]["end_ms"] == 4900
    assert not any(s["end_ms"] == 10200 for s in qa_subs)


def test_overlay_runtime_timeline_truth_no_mutation(render_service):
    prepared_narr = [{"id": "n1", "start_ms": 0, "end_ms": 5000}]
    prepared_subs = [{"cue_order": 1, "start_ms": 0, "end_ms": 5000, "text": "one"}]

    qa_narr, qa_subs = render_service._overlay_runtime_timeline_truth(
        prepared_narr, prepared_subs, None, (), ()
    )

    qa_narr[0]["start_ms"] = 999
    qa_subs[0]["start_ms"] = 999

    assert prepared_narr[0]["start_ms"] == 0
    assert prepared_subs[0]["start_ms"] == 0


def test_overlay_runtime_timeline_truth_duration_none(render_service):
    prepared_narr = [{"id": "n1", "start_ms": 0, "end_ms": 5000}]
    prepared_subs = [{"cue_order": 1, "start_ms": 0, "end_ms": 5000, "text": "one"}]

    qa_narr, qa_subs = render_service._overlay_runtime_timeline_truth(
        prepared_narr, prepared_subs, None, (), ()
    )

    assert qa_narr == prepared_narr
    assert qa_subs == prepared_subs


def test_overlay_runtime_timeline_truth_empty_subs(render_service):
    prepared_narr = [{"id": "n1", "start_ms": 0, "end_ms": 5000}]
    prepared_subs = [{"cue_order": 1, "start_ms": 0, "end_ms": 5000, "text": "one"}]

    qa_narr, qa_subs = render_service._overlay_runtime_timeline_truth(
        prepared_narr, prepared_subs, 5000, [{"scene_index": 1, "start_ms": 0, "end_ms": 5000, "duration_ms": 5000}], []
    )

    assert qa_subs == []


def test_overlay_runtime_timeline_truth_empty_narr(render_service):
    prepared_narr = [{"id": "n1", "start_ms": 0, "end_ms": 5000}]
    prepared_subs = [{"cue_order": 1, "start_ms": 0, "end_ms": 5000, "text": "one"}]

    qa_narr, qa_subs = render_service._overlay_runtime_timeline_truth(
        prepared_narr, prepared_subs, 5000, [], []
    )

    assert qa_narr == []


def test_overlay_runtime_narration_provenance(render_service):
    assets_data = [
        {"asset_type": "AUDIO", "source_ref": "Local TTS", "id": "1"},
        {"asset_type": "IMAGE", "source_ref": "Pexels", "id": "2"},
        {"asset_type": "SUBTITLE", "source_ref": "Local", "id": "3"},
    ]

    result = render_service._overlay_runtime_narration_provenance(
        assets_data,
        narration_quality="NEURAL_PRODUCTION",
        narration_source_refs=("Gemini TTS (voice: Kore)",),
    )

    # 3. _overlay_runtime_narration_provenance
    audio_asset = next(a for a in result if a["asset_type"] == "AUDIO")
    assert audio_asset["narration_quality"] == "NEURAL_PRODUCTION"
    assert audio_asset["source_ref"] == "Gemini TTS (voice: Kore)"
    assert audio_asset["narration_source_refs"] == ["Gemini TTS (voice: Kore)"]
    assert "Local TTS" not in audio_asset["source_ref"]

    # 4. Input non-mutation
    assert assets_data[0]["source_ref"] == "Local TTS"

    # 5. Non-AUDIO preservation
    image_asset = next(a for a in result if a["asset_type"] == "IMAGE")
    subtitle_asset = next(a for a in result if a["asset_type"] == "SUBTITLE")
    assert image_asset == {"asset_type": "IMAGE", "source_ref": "Pexels", "id": "2"}
    assert subtitle_asset == {"asset_type": "SUBTITLE", "source_ref": "Local", "id": "3"}


def test_overlay_runtime_narration_quality_empty_refs(render_service):
    assets_data = [
        {"asset_type": "AUDIO", "source_ref": "Local TTS"},
    ]

    result = render_service._overlay_runtime_narration_provenance(
        assets_data,
        narration_quality="NEURAL_PRODUCTION",
        narration_source_refs=(),
    )

    # 6. Runtime quality with empty refs
    audio_asset = result[0]
    assert audio_asset["source_ref"] is None
    assert audio_asset["narration_quality"] == "NEURAL_PRODUCTION"


def test_overlay_runtime_narration_no_audio(render_service):
    assets_data = [
        {"asset_type": "IMAGE", "source_ref": "Pexels", "id": "2"},
    ]

    result = render_service._overlay_runtime_narration_provenance(
        assets_data,
        narration_quality="NEURAL_PRODUCTION",
        narration_source_refs=("Gemini TTS (voice: Kore)",),
    )

    # 7. Runtime provenance with no AUDIO
    assert len(result) == 2
    audio_asset = result[-1]
    assert audio_asset["id"] == "runtime-narration"
    assert audio_asset["asset_type"] == "AUDIO"
    assert audio_asset["narration_quality"] == "NEURAL_PRODUCTION"
