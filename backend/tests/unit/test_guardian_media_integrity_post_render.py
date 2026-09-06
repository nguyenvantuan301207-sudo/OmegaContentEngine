import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omega.application.guardian.detectors.base import GuardianEvaluationContext
from omega.application.guardian.detectors.media_integrity import MediaIntegrityDetector
from omega.domain.guardian import CheckTriggerType, GuardianCheckpoint
from omega.infrastructure.models import (
    MediaArtifact,
    ProductionAsset,
    ProductionRequest,
)


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = False
    return session


@pytest.fixture
def mock_adapter():
    with patch("omega.application.guardian.detectors.media_integrity.ProductionQAAdapter") as mock_cls:
        mock_inst = MagicMock()
        mock_inst.evaluate.return_value = []
        mock_cls.return_value = mock_inst
        yield mock_inst


@pytest.mark.asyncio
async def test_db_fallback_preserves_assets(mock_session, mock_adapter):
    # 1. DB fallback assets preserve: asset_type, provider_type, mime_type, storage_uri
    # 2. visual asset not lost
    # 3. subtitle asset not lost
    detector = MediaIntegrityDetector()

    req_id = str(uuid.uuid4())
    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=req_id,
    )

    prod_req = ProductionRequest(
        id=uuid.UUID(req_id),
        script_version_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        target_width=1920,
        target_height=1080,
        video_codec="h264"
    )
    prod_req.scenes = []
    prod_req.narration_segments = []
    prod_req.subtitle_cues = []
    prod_req.artifacts = []

    visual_asset = ProductionAsset(
        id=uuid.uuid4(),
        asset_type="IMAGE",
        provider_type="PEXELS",
        mime_type="image/jpeg",
        storage_uri="s3://b/img.jpg",
        license_status="CLEARED",
        source_ref="pexels-123",
        asset_requirement_id=uuid.uuid4()
    )
    subtitle_asset = ProductionAsset(
        id=uuid.uuid4(),
        asset_type="SUBTITLE",
        provider_type="LOCAL",
        mime_type="application/x-subrip",
        storage_uri="s3://b/sub.srt",
        license_status="CLEARED",
        source_ref="local",
        asset_requirement_id=uuid.uuid4()
    )
    prod_req.assets = [visual_asset, subtitle_asset]

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = prod_req
    mock_session.execute.return_value = mock_res

    await detector.evaluate(context, lambda: mock_session)

    mock_adapter.evaluate.assert_called_once()
    kwargs = mock_adapter.evaluate.call_args[1]

    assets_data = kwargs["assets_data"]
    assert len(assets_data) == 2

    img_data = assets_data[0]
    assert img_data["asset_type"] == "IMAGE"
    assert img_data["provider_type"] == "PEXELS"
    assert img_data["mime_type"] == "image/jpeg"
    assert img_data["storage_uri"] == "s3://b/img.jpg"

    sub_data = assets_data[1]
    assert sub_data["asset_type"] == "SUBTITLE"
    assert sub_data["provider_type"] == "LOCAL"
    assert sub_data["mime_type"] == "application/x-subrip"
    assert sub_data["storage_uri"] == "s3://b/sub.srt"


@pytest.mark.asyncio
async def test_diagnostic_values_forwarded_no_artifact(mock_session, mock_adapter):
    # 4. With NO committed MediaArtifact visible, diagnostic values are forwarded unchanged
    detector = MediaIntegrityDetector()

    req_id = str(uuid.uuid4())
    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=req_id,
        diagnostic_context={
            "media_probe_summary": {"has_audio": True},
            "expected_hash": "hash123",
            "artifact_file_path": "/tmp/video.mp4"
        }
    )

    prod_req = ProductionRequest(
        id=uuid.UUID(req_id),
        script_version_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        target_width=1920,
        target_height=1080,
        video_codec="h264"
    )
    prod_req.scenes = []
    prod_req.assets = []
    prod_req.narration_segments = []
    prod_req.subtitle_cues = []
    prod_req.artifacts = [] # NO committed artifact

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = prod_req
    mock_session.execute.return_value = mock_res

    await detector.evaluate(context, lambda: mock_session)

    kwargs = mock_adapter.evaluate.call_args[1]
    assert kwargs["media_probe_summary"] == {"has_audio": True}
    assert kwargs["expected_hash"] == "hash123"
    assert kwargs["artifact_file_path"] == "/tmp/video.mp4"


@pytest.mark.asyncio
async def test_committed_artifact_as_fallback(mock_session, mock_adapter):
    # 5. A committed MediaArtifact is only fallback when those diagnostic values are absent
    detector = MediaIntegrityDetector()

    req_id = str(uuid.uuid4())
    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=req_id,
        diagnostic_context={
            # partially populated
            "media_probe_summary": {"has_audio": False},
        }
    )

    prod_req = ProductionRequest(
        id=uuid.UUID(req_id),
        script_version_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        target_width=1920,
        target_height=1080,
        video_codec="h264"
    )
    prod_req.scenes = []
    prod_req.assets = []
    prod_req.narration_segments = []
    prod_req.subtitle_cues = []

    art = MediaArtifact(
        id=uuid.uuid4(),
        is_current=True,
        width=1280,
        height=720,
        duration_ms=5000,
        storage_uri="s3://artifacts/1",
        content_hash="art-hash",
    )
    prod_req.artifacts = [art]

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = prod_req
    mock_session.execute.return_value = mock_res

    await detector.evaluate(context, lambda: mock_session)

    kwargs = mock_adapter.evaluate.call_args[1]

    # media_probe_summary from diagnostic_context takes precedence
    assert kwargs["media_probe_summary"] == {"has_audio": False}

    # The others fallback to artifact
    assert kwargs["expected_hash"] == "art-hash"
    assert kwargs["artifact_file_path"] == "s3://artifacts/1"


@pytest.mark.asyncio
async def test_diagnostic_takes_precedence_over_artifact(mock_session, mock_adapter):
    # 6. Diagnostic media values take precedence over committed artifact values when both exist
    detector = MediaIntegrityDetector()

    req_id = str(uuid.uuid4())
    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=req_id,
        diagnostic_context={
            "media_probe_summary": {"has_audio": True},
            "expected_hash": "diag-hash",
            "artifact_file_path": "diag-path"
        }
    )

    prod_req = ProductionRequest(
        id=uuid.UUID(req_id),
        script_version_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        target_width=1920,
        target_height=1080,
        video_codec="h264"
    )
    prod_req.scenes = []
    prod_req.assets = []
    prod_req.narration_segments = []
    prod_req.subtitle_cues = []

    art = MediaArtifact(
        id=uuid.uuid4(),
        is_current=True,
        width=1280,
        height=720,
        duration_ms=5000,
        storage_uri="art-path",
        content_hash="art-hash",
    )
    prod_req.artifacts = [art]

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = prod_req
    mock_session.execute.return_value = mock_res

    await detector.evaluate(context, lambda: mock_session)

    kwargs = mock_adapter.evaluate.call_args[1]

    # All diagnostic values take precedence
    assert kwargs["media_probe_summary"] == {"has_audio": True}
    assert kwargs["expected_hash"] == "diag-hash"
    assert kwargs["artifact_file_path"] == "diag-path"


@pytest.mark.asyncio
async def test_db_fallback_audio_asset_update(mock_session, mock_adapter):
    # 1. Prepared AUDIO DB asset
    detector = MediaIntegrityDetector()

    req_id = str(uuid.uuid4())
    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=req_id,
        diagnostic_context={
            "narration_quality": "NEURAL_PRODUCTION",
            "narration_source_refs": ["Gemini TTS (voice: Kore)"]
        }
    )

    prod_req = ProductionRequest(
        id=uuid.UUID(req_id),
        script_version_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        target_width=1920,
        target_height=1080,
        video_codec="h264"
    )
    prod_req.scenes = []
    prod_req.narration_segments = []
    prod_req.subtitle_cues = []
    prod_req.artifacts = []

    visual_asset = ProductionAsset(
        id=uuid.uuid4(),
        asset_type="IMAGE",
        provider_type="PEXELS",
        mime_type="image/jpeg",
        storage_uri="s3://b/img.jpg",
        license_status="CLEARED",
        source_ref="pexels-123",
        asset_requirement_id=uuid.uuid4()
    )
    audio_asset = ProductionAsset(
        id=uuid.uuid4(),
        asset_type="AUDIO",
        provider_type="LOCAL",
        mime_type="audio/mp3",
        storage_uri="s3://b/aud.mp3",
        license_status="CLEARED",
        source_ref="Local TTS",
        asset_requirement_id=uuid.uuid4()
    )
    prod_req.assets = [visual_asset, audio_asset]

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = prod_req
    mock_session.execute.return_value = mock_res

    await detector.evaluate(context, lambda: mock_session)

    kwargs = mock_adapter.evaluate.call_args[1]
    assets_data = kwargs["assets_data"]

    # 1. Assert ProductionQAAdapter receives AUDIO:
    audio_data = next(a for a in assets_data if a["asset_type"] == "AUDIO")
    assert audio_data["narration_quality"] == "NEURAL_PRODUCTION"
    assert audio_data["source_ref"] == "Gemini TTS (voice: Kore)"
    assert audio_data["narration_source_refs"] == ["Gemini TTS (voice: Kore)"]
    assert "Local TTS" not in audio_data["source_ref"]

    # 2. Non-AUDIO DB asset remains semantically unchanged.
    img_data = next(a for a in assets_data if a["asset_type"] == "IMAGE")
    assert img_data["source_ref"] == "pexels-123"


@pytest.mark.asyncio
async def test_db_fallback_neural_empty_refs(mock_session, mock_adapter):
    # 3. Runtime NEURAL + empty refs
    detector = MediaIntegrityDetector()

    req_id = str(uuid.uuid4())
    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=req_id,
        diagnostic_context={
            "narration_quality": "NEURAL_PRODUCTION",
            "narration_source_refs": []
        }
    )

    prod_req = ProductionRequest(
        id=uuid.UUID(req_id),
        script_version_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        target_width=1920,
        target_height=1080,
        video_codec="h264"
    )
    prod_req.scenes = []
    prod_req.narration_segments = []
    prod_req.subtitle_cues = []
    prod_req.artifacts = []

    audio_asset = ProductionAsset(
        id=uuid.uuid4(),
        asset_type="AUDIO",
        source_ref="Local TTS",
    )
    prod_req.assets = [audio_asset]

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = prod_req
    mock_session.execute.return_value = mock_res

    await detector.evaluate(context, lambda: mock_session)

    kwargs = mock_adapter.evaluate.call_args[1]
    assets_data = kwargs["assets_data"]

    audio_data = next(a for a in assets_data if a["asset_type"] == "AUDIO")
    assert audio_data["source_ref"] is None
    assert audio_data["narration_quality"] == "NEURAL_PRODUCTION"


@pytest.mark.asyncio
async def test_db_fallback_no_diagnostic(mock_session, mock_adapter):
    # 4. NO runtime diagnostic provenance
    detector = MediaIntegrityDetector()

    req_id = str(uuid.uuid4())
    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=req_id,
        diagnostic_context={}
    )

    prod_req = ProductionRequest(
        id=uuid.UUID(req_id),
        script_version_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        target_width=1920,
        target_height=1080,
        video_codec="h264"
    )
    prod_req.scenes = []
    prod_req.narration_segments = []
    prod_req.subtitle_cues = []
    prod_req.artifacts = []

    audio_asset = ProductionAsset(
        id=uuid.uuid4(),
        asset_type="AUDIO",
        source_ref="Local TTS",
    )
    prod_req.assets = [audio_asset]

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = prod_req
    mock_session.execute.return_value = mock_res

    await detector.evaluate(context, lambda: mock_session)

    kwargs = mock_adapter.evaluate.call_args[1]
    assets_data = kwargs["assets_data"]

    audio_data = next(a for a in assets_data if a["asset_type"] == "AUDIO")
    assert audio_data["source_ref"] == "Local TTS"


@pytest.mark.asyncio
async def test_db_fallback_provenance_no_audio(mock_session, mock_adapter):
    # 5. Runtime provenance but NO DB AUDIO
    detector = MediaIntegrityDetector()

    req_id = str(uuid.uuid4())
    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=req_id,
        diagnostic_context={
            "narration_quality": "NEURAL_PRODUCTION",
            "narration_source_refs": ["Gemini TTS (voice: Kore)"]
        }
    )

    prod_req = ProductionRequest(
        id=uuid.UUID(req_id),
        script_version_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        target_width=1920,
        target_height=1080,
        video_codec="h264"
    )
    prod_req.scenes = []
    prod_req.narration_segments = []
    prod_req.subtitle_cues = []
    prod_req.artifacts = []
    prod_req.assets = []

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = prod_req
    mock_session.execute.return_value = mock_res

    await detector.evaluate(context, lambda: mock_session)

    kwargs = mock_adapter.evaluate.call_args[1]
    assets_data = kwargs["assets_data"]

    audio_data = next(a for a in assets_data if a["asset_type"] == "AUDIO")
    assert audio_data["id"] == "runtime-narration"
    assert audio_data["asset_type"] == "AUDIO"
    assert audio_data["narration_quality"] == "NEURAL_PRODUCTION"


@pytest.mark.asyncio
async def test_db_fallback_normalization(mock_session, mock_adapter):
    # 6. Normalization
    detector = MediaIntegrityDetector()

    req_id = str(uuid.uuid4())
    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=req_id,
        diagnostic_context={
            "narration_source_refs": ["Gemini TTS", "Gemini TTS", 123, "", None]
        }
    )

    prod_req = ProductionRequest(
        id=uuid.UUID(req_id),
        script_version_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        target_width=1920,
        target_height=1080,
        video_codec="h264"
    )
    prod_req.scenes = []
    prod_req.narration_segments = []
    prod_req.subtitle_cues = []
    prod_req.artifacts = []

    audio_asset = ProductionAsset(
        id=uuid.uuid4(),
        asset_type="AUDIO",
        source_ref="Local TTS",
    )
    prod_req.assets = [audio_asset]

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = prod_req
    mock_session.execute.return_value = mock_res

    await detector.evaluate(context, lambda: mock_session)

    kwargs = mock_adapter.evaluate.call_args[1]
    assets_data = kwargs["assets_data"]

    audio_data = next(a for a in assets_data if a["asset_type"] == "AUDIO")
    assert audio_data["narration_source_refs"] == ["Gemini TTS", "123"]
    assert audio_data["source_ref"] == "Gemini TTS | 123"
