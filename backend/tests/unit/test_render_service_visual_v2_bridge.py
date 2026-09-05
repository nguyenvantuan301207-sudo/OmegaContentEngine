import hashlib
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

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
