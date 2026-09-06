import hashlib
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.render_service import ProductionRenderService
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
        narration_quality = "NEURAL_PRODUCTION"
        narration_source_refs = ("Gemini TTS (voice: Kore)",)

    mock_v2_service.render_mission_execution.return_value = FakeResult()
    session = AsyncMock(spec=AsyncSession)

    result = await render_service._render_v2_staging(
        session, req, 30, 1920, 1080, "mp4", "h264", staging_out
    )

    assert result == (
        "NEURAL_PRODUCTION",
        ("Gemini TTS (voice: Kore)",),
    )


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

    assert result == (None, ())


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
