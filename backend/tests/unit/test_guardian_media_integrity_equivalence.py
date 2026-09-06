import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from omega.application.guardian.detectors.base import GuardianEvaluationContext
from omega.application.guardian.detectors.media_integrity import MediaIntegrityDetector
from omega.domain.guardian import CheckTriggerType, GuardianCheckpoint


async def _capture_direct_adapter_kwargs(diagnostic_context: dict[str, Any]) -> dict[str, Any]:
    detector = MediaIntegrityDetector()
    detector.adapter = MagicMock()
    detector.adapter.evaluate.return_value = []

    req_id = uuid.uuid4()
    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=req_id,
        media_artifact_id=None,
        diagnostic_context=diagnostic_context,
    )

    session_factory = MagicMock()
    await detector.evaluate(context, session_factory)

    assert detector.adapter.evaluate.call_count == 1
    return detector.adapter.evaluate.call_args.kwargs


async def _capture_db_adapter_kwargs(
    mock_prod_req: Any, diagnostic_context: dict[str, Any] | None = None
) -> dict[str, Any]:
    detector = MediaIntegrityDetector()
    detector.adapter = MagicMock()
    detector.adapter.evaluate.return_value = []

    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=mock_prod_req.id,
        media_artifact_id=None,
        diagnostic_context=diagnostic_context or {},
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_prod_req

    mock_session.execute.return_value = mock_result

    mock_session.__aenter__.return_value = mock_session
    mock_session.__aexit__.return_value = False

    def session_factory():
        return mock_session

    await detector.evaluate(context, session_factory)

    assert detector.adapter.evaluate.call_count == 1
    return detector.adapter.evaluate.call_args.kwargs


@pytest.mark.asyncio
async def test_case_1_runtime_narration_provenance_equivalence():
    # Construct DB state with stale AUDIO
    mock_req = MagicMock()
    req_id = uuid.uuid4()
    sv_id = uuid.uuid4()
    cdr_id = uuid.uuid4()
    mock_req.id = req_id
    mock_req.script_version_id = sv_id
    mock_req.channel_dna_revision_id = cdr_id
    mock_req.target_width = 1920
    mock_req.target_height = 1080
    mock_req.video_codec = "h264"
    mock_req.artifacts = []
    mock_req.scenes = []
    mock_req.narration_segments = []
    mock_req.subtitle_cues = []

    mock_audio = MagicMock()
    mock_audio.id = "audio-1"
    mock_audio.asset_type = "AUDIO"
    mock_audio.provider_type = "SYSTEM"
    mock_audio.mime_type = "audio/wav"
    mock_audio.storage_uri = "test.wav"
    mock_audio.license_status = "GENERATED"
    mock_audio.source_ref = "Local TTS"
    mock_audio.asset_requirement_id = None
    mock_req.assets = [mock_audio]

    # Runtime diagnostic context for DB path
    db_diag = {
        "narration_quality": "NEURAL_PRODUCTION",
        "narration_source_refs": ["Gemini TTS (voice: Kore)"],
    }
    db_kwargs = await _capture_db_adapter_kwargs(mock_req, db_diag)

    # Runtime diagnostic context for direct path
    direct_diag = {
        "request_data": {
            "id": str(req_id),
            "script_version_id": str(sv_id),
            "channel_dna_revision_id": str(cdr_id),
            "target_width": 1920,
            "target_height": 1080,
            "video_codec": "h264",
        },
        "script_version_data": {"id": str(sv_id)},
        "assets_data": [
            {
                "id": "audio-1",
                "asset_type": "AUDIO",
                "provider_type": "SYSTEM",
                "mime_type": "audio/wav",
                "storage_uri": "test.wav",
                "license_status": "GENERATED",
                "source_ref": "Gemini TTS (voice: Kore)",
                "asset_requirement_id": None,
                "narration_quality": "NEURAL_PRODUCTION",
                "narration_source_refs": ["Gemini TTS (voice: Kore)"],
            }
        ],
    }
    direct_kwargs = await _capture_direct_adapter_kwargs(direct_diag)

    assert direct_kwargs["assets_data"] == db_kwargs["assets_data"]

    # Explicitly assert DB result
    db_assets = db_kwargs["assets_data"]
    audio_asset = next(a for a in db_assets if a["asset_type"] == "AUDIO")
    assert audio_asset["narration_quality"] == "NEURAL_PRODUCTION"
    assert audio_asset["source_ref"] == "Gemini TTS (voice: Kore)"
    assert audio_asset["narration_source_refs"] == ["Gemini TTS (voice: Kore)"]
    assert "Local TTS" not in str(audio_asset)


@pytest.mark.asyncio
async def test_case_2_runtime_timeline_equivalence():
    mock_req = MagicMock()
    req_id = uuid.uuid4()
    sv_id = uuid.uuid4()
    mock_req.id = req_id
    mock_req.script_version_id = sv_id
    mock_req.channel_dna_revision_id = uuid.uuid4()
    mock_req.target_width = 1920
    mock_req.target_height = 1080
    mock_req.video_codec = "h264"
    mock_req.artifacts = []
    mock_req.scenes = []
    mock_req.assets = []

    n1 = MagicMock(id="n1", start_ms=0, end_ms=5000, scene_id="s1")
    n2 = MagicMock(id="n2", start_ms=5200, end_ms=10200, scene_id="s2")
    mock_req.narration_segments = [n1, n2]

    c1 = MagicMock(cue_order=1, start_ms=0, end_ms=5000, text="cue1")
    c2 = MagicMock(cue_order=2, start_ms=5200, end_ms=10200, text="cue2")
    mock_req.subtitle_cues = [c1, c2]

    db_diag = {
        "runtime_timeline_duration_ms": 5000,
        "runtime_narration_segments": [
            {"scene_index": 1, "start_ms": 0, "end_ms": 2000, "duration_ms": 2000},
            {"scene_index": 2, "start_ms": 2000, "end_ms": 5000, "duration_ms": 3000},
        ],
        "runtime_subtitle_cues": [
            {"scene_index": 1, "cue_order": 1, "start_ms": 0, "end_ms": 1800, "text": "one"},
            {"scene_index": 2, "cue_order": 2, "start_ms": 2000, "end_ms": 4900, "text": "two"},
        ]
    }
    db_kwargs = await _capture_db_adapter_kwargs(mock_req, db_diag)

    direct_diag = {
        "request_data": {"id": str(req_id), "script_version_id": str(sv_id)},
        "script_version_data": {"id": str(sv_id)},
        "narration_segments": [
            {"id": "runtime-narration-1", "scene_index": 1, "start_ms": 0, "end_ms": 2000, "duration_ms": 2000},
            {"id": "runtime-narration-2", "scene_index": 2, "start_ms": 2000, "end_ms": 5000, "duration_ms": 3000},
        ],
        "subtitle_cues": [
            {"cue_order": 1, "scene_index": 1, "start_ms": 0, "end_ms": 1800, "text": "one"},
            {"cue_order": 2, "scene_index": 2, "start_ms": 2000, "end_ms": 4900, "text": "two"},
        ],
    }
    direct_kwargs = await _capture_direct_adapter_kwargs(direct_diag)

    assert direct_kwargs["narration_segments"] == db_kwargs["narration_segments"]
    assert direct_kwargs["subtitle_cues"] == db_kwargs["subtitle_cues"]

    assert not any(n.get("end_ms") == 10200 for n in db_kwargs["narration_segments"])
    assert not any(c.get("end_ms") == 10200 for c in db_kwargs["subtitle_cues"])
    assert db_kwargs["narration_segments"][-1]["end_ms"] == 5000
    assert db_kwargs["subtitle_cues"][-1]["end_ms"] == 4900


@pytest.mark.asyncio
async def test_case_3_explicit_empty_runtime_subtitles_equivalence():
    mock_req = MagicMock()
    req_id = uuid.uuid4()
    sv_id = uuid.uuid4()
    mock_req.id = req_id
    mock_req.script_version_id = sv_id
    mock_req.channel_dna_revision_id = uuid.uuid4()
    mock_req.target_width = 1920
    mock_req.target_height = 1080
    mock_req.video_codec = "h264"
    mock_req.artifacts = []
    mock_req.scenes = []
    mock_req.assets = []
    mock_req.narration_segments = []

    c1 = MagicMock(cue_order=1, start_ms=0, end_ms=5000, text="stale-db-subtitle")
    mock_req.subtitle_cues = [c1]

    db_diag = {
        "runtime_timeline_duration_ms": 5000,
        "runtime_narration_segments": [
            {"scene_index": 1, "start_ms": 0, "end_ms": 5000, "duration_ms": 5000},
        ],
        "runtime_subtitle_cues": [],
    }
    db_kwargs = await _capture_db_adapter_kwargs(mock_req, db_diag)

    direct_diag = {
        "request_data": {"id": str(req_id), "script_version_id": str(sv_id)},
        "script_version_data": {"id": str(sv_id)},
        "narration_segments": [
            {"id": "runtime-narration-1", "scene_index": 1, "start_ms": 0, "end_ms": 5000, "duration_ms": 5000},
        ],
        "subtitle_cues": [],
    }
    direct_kwargs = await _capture_direct_adapter_kwargs(direct_diag)

    assert db_kwargs["subtitle_cues"] == []
    assert direct_kwargs["subtitle_cues"] == []
    assert direct_kwargs["subtitle_cues"] == db_kwargs["subtitle_cues"]

    # Assert stale DB subtitle text is absent
    assert not any("stale-db-subtitle" in str(v) for v in db_kwargs.values())
