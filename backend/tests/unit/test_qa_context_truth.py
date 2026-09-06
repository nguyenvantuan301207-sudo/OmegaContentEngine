import uuid
from typing import Any
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from omega.application.production_qa import ProductionQAEngine
from omega.application.guardian.adapters.production_qa_adapter import ProductionQAAdapter
from omega.application.guardian.detectors.media_integrity import MediaIntegrityDetector
from omega.application.render_service import ProductionRenderService
from omega.application.guardian.detectors.base import GuardianEvaluationContext
from omega.infrastructure.models import (
    ProductionRequest,
    ProductionScene,
    ScriptVersion,
    ScriptSection,
    MediaArtifact,
)


@pytest.fixture
def qa_engine():
    return ProductionQAEngine()


def test_production_qa_engine_accepts_scenes_data(qa_engine):
    # G. No-rule activation
    status, findings = qa_engine.evaluate(
        request_data={"script_version_id": "sv1"},
        script_version_data={
            "id": "sv1",
            "hook_text": "hook",
            "cta_text": "cta",
            "closing_text": "closing",
            "sections": [
                {"section_order": 1, "heading": "h1", "narration_text": "n1"}
            ]
        },
        content_request_data={},
        assets_data=[{"asset_type": "IMAGE", "id": "img1", "provider_type": "UNSPLASH"}],
        requirements_data=[],
        narration_segments=[{"start_ms": 0, "end_ms": 1000}],
        subtitle_cues=[],
        media_probe_summary={"duration_ms": 1000, "width": 1920, "height": 1080, "has_audio": True, "video_codec": "h264"},
        artifact_file_path=None,
        expected_hash=None,
        scenes_data=[
            {
                "sequence_index": 1,
                "original_strategy": "IMAGE",
                "effective_strategy": "IMAGE",
                "duration_seconds": 1.0,
                "asset_kind": "IMAGE",
                "asset_provider": "UNSPLASH",
                "asset_id": "id1",
            }
        ]
    )
    # Basic rule passes, should not trigger any long-form rule.
    assert status.value == "PASSED"


def test_guardian_adapter_propagates_scenes_data():
    # F. Guardian propagation
    adapter = ProductionQAAdapter()
    scenes_data = [{"sequence_index": 1, "effective_strategy": "BROLL"}]
    with patch.object(adapter.engine, "evaluate", return_value=(MagicMock(), [])) as mock_eval:
        adapter.evaluate(
            request_data={},
            script_version_data={},
            content_request_data={},
            assets_data=[],
            requirements_data=[],
            narration_segments=[],
            subtitle_cues=[],
            media_probe_summary=None,
            artifact_file_path=None,
            expected_hash=None,
            scenes_data=scenes_data,
        )
        mock_eval.assert_called_once()
        assert mock_eval.call_args[1]["scenes_data"] == scenes_data


@pytest.mark.asyncio
async def test_media_integrity_detector_db_resolution():
    # E. Script snapshot
    # D. Legacy path (no runtime_scenes)
    detector = MediaIntegrityDetector()

    mock_session = AsyncMock()
    mock_session_factory = MagicMock(return_value=mock_session)
    mock_session.__aenter__.return_value = mock_session

    prod_req = ProductionRequest(
        id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        target_width=1920,
        target_height=1080,
        video_codec="h264",
    )
    sv = ScriptVersion(id=prod_req.script_version_id, hook_text="h", cta_text="c", closing_text="cl")
    sec = ScriptSection(section_order=1, heading="head", narration_text="narr")
    sv.sections = [sec]
    prod_req.script_version = sv

    ps = ProductionScene(scene_order=1, scene_type="TITLE", estimated_duration_ms=2000)
    ps.asset_requirements = []
    prod_req.scenes = [ps]
    prod_req.assets = []
    prod_req.narration_segments = []
    prod_req.subtitle_cues = []
    prod_req.artifacts = []

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = prod_req
    mock_session.execute.return_value = mock_res

    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint="POST_RENDER",
        trigger_type="POST_RENDER",
        production_request_id=prod_req.id,
        diagnostic_context={"media_probe_summary": {}}
    )

    with patch.object(detector.adapter, "evaluate", return_value=[]) as mock_eval:
        await detector.evaluate(context, mock_session_factory)

        mock_eval.assert_called_once()
        args = mock_eval.call_args[1]

        # Check script snapshot
        assert args["script_version_data"]["hook_text"] == "h"
        assert len(args["script_version_data"]["sections"]) == 1

        # Check legacy scenes fallback
        scenes_data = args["scenes_data"]
        assert len(scenes_data) == 1
        assert scenes_data[0]["sequence_index"] == 1
        assert scenes_data[0]["effective_strategy"] == "TITLE"
        assert scenes_data[0]["duration_seconds"] == 2.0


@pytest.mark.asyncio
async def test_media_integrity_detector_db_resolution_v2_override():
    # C. V2 override
    detector = MediaIntegrityDetector()

    mock_session = AsyncMock()
    mock_session_factory = MagicMock(return_value=mock_session)
    mock_session.__aenter__.return_value = mock_session

    prod_req = ProductionRequest(
        id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        target_width=1920,
        target_height=1080,
        video_codec="h264",
    )
    sv = ScriptVersion(id=prod_req.script_version_id)
    sv.sections = []
    prod_req.script_version = sv

    # Legacy scenes
    ps = ProductionScene(scene_order=1, scene_type="TITLE", estimated_duration_ms=2000)
    ps.asset_requirements = []
    prod_req.scenes = [ps]
    prod_req.assets = []
    prod_req.narration_segments = []
    prod_req.subtitle_cues = []
    prod_req.artifacts = []

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = prod_req
    mock_session.execute.return_value = mock_res

    context = GuardianEvaluationContext(
        mission_id=uuid.uuid4(),
        checkpoint="POST_RENDER",
        trigger_type="POST_RENDER",
        production_request_id=prod_req.id,
        diagnostic_context={
            "media_probe_summary": {},
            "runtime_scenes": [
                {
                    "sequence_index": 2,
                    "original_strategy": "BROLL",
                    "effective_strategy": "BROLL",
                    "duration_seconds": 3.5,
                    "asset_kind": "BROLL",
                    "asset_provider": "PEXELS",
                    "asset_id": "vid1",
                }
            ]
        }
    )

    with patch.object(detector.adapter, "evaluate", return_value=[]) as mock_eval:
        await detector.evaluate(context, mock_session_factory)

        mock_eval.assert_called_once()
        args = mock_eval.call_args[1]

        # Check V2 override (should ignore ProductionScene entirely)
        scenes_data = args["scenes_data"]
        assert len(scenes_data) == 1
        assert scenes_data[0]["sequence_index"] == 2
        assert scenes_data[0]["effective_strategy"] == "BROLL"
        assert scenes_data[0]["duration_seconds"] == 3.5
        assert scenes_data[0]["asset_kind"] == "BROLL"
