from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from omega.application.guardian.adapters.production_qa_adapter import ProductionQAAdapter
from omega.application.guardian.detectors.base import GuardianEvaluationContext
from omega.application.guardian.detectors.media_integrity import (
    MediaIntegrityDetector,
    _runtime_truth_snapshot,
)
from omega.application.production_qa import ProductionQAEngine
from omega.application.production_runtime_truth import ProductionRuntimeTruthSnapshot
from omega.domain.guardian import CheckTriggerType, GuardianCheckpoint
from omega.domain.production import LicenseStatus, ProductionQARuleCode


def _snapshot(
    *,
    license_status: LicenseStatus = LicenseStatus.LICENSED,
    attribution: str | None = None,
    contentful: bool = True,
    subtitle_rendered: bool = True,
):
    scenes = (
        SimpleNamespace(
            sequence_index=1,
            duration_ms=1000,
            scene_content_sha256="a" * 64,
            template_id=None,
        ),
    ) if contentful else ()
    visuals = (
        SimpleNamespace(
            scene_index=1,
            origin="PROVIDER",
            template_id=None,
            provider="pexels",
            provider_asset_id="runtime-visual",
            content_sha256="b" * 64,
            license_status=license_status,
            attribution=attribution,
        ),
    ) if contentful else ()
    return SimpleNamespace(
        scenes=scenes,
        visuals=visuals,
        subtitles=SimpleNamespace(
            effective_mode="STANDARD",
            burn_applied=subtitle_rendered,
            cues=(object(),) if subtitle_rendered else (),
            artifacts=(object(),) if subtitle_rendered else (),
        ),
    )


def _qa_inputs(*, snapshot, prepared_status=LicenseStatus.LICENSED, placeholder=False):
    return {
        "request_data": {
            "id": "request-1",
            "script_version_id": "script",
            "channel_dna_revision_id": "dna",
            "target_width": 1920,
            "target_height": 1080,
            "video_codec": "h264",
        },
        "script_version_data": {"id": "script", "sections": []},
        "content_request_data": {"channel_dna_revision_id": "dna"},
        "assets_data": [
            {
                "id": "prepared-visual",
                "asset_type": "IMAGE",
                "provider_type": "PLACEHOLDER" if placeholder else "PEXELS",
                "source_ref": "PLACEHOLDER" if placeholder else "prepared-contentful",
                "asset_requirement_id": "requirement-1",
                "license_status": prepared_status.value,
                "attribution": "Prepared attribution",
            }
        ],
        "requirements_data": [
            {"id": "requirement-1", "scene_index": 1, "required": True}
        ],
        "narration_segments": [{"start_ms": 0, "end_ms": 1000}],
        "subtitle_cues": [{"cue_order": 1, "start_ms": 0, "end_ms": 900, "text": "cue"}],
        "media_probe_summary": {
            "duration_ms": 1000,
            "width": 1920,
            "height": 1080,
            "video_codec": "h264",
            "has_audio": True,
        },
        "artifact_file_path": None,
        "expected_hash": None,
        "scenes_data": [],
        "runtime_truth_snapshot": snapshot,
    }


def _local_and_guardian_rules(**kwargs):
    _, local_findings = ProductionQAEngine().evaluate(**kwargs)
    guardian_findings = ProductionQAAdapter().evaluate(**kwargs)
    return (
        {finding.rule_code for finding in local_findings},
        {ProductionQARuleCode(finding.rule_id) for finding in guardian_findings},
    )


def test_runtime_contentful_visual_overrides_prepared_placeholder_for_both_paths():
    local, guardian = _local_and_guardian_rules(
        **_qa_inputs(snapshot=_snapshot(), placeholder=True)
    )

    assert ProductionQARuleCode.PLACEHOLDER_ONLY_VISUALS not in local
    assert local == guardian


def test_legacy_prepared_placeholder_fallback_is_preserved_for_both_paths():
    local, guardian = _local_and_guardian_rules(
        **_qa_inputs(snapshot=None, placeholder=True)
    )

    assert ProductionQARuleCode.PLACEHOLDER_ONLY_VISUALS in local
    assert local == guardian


@pytest.mark.parametrize(
    ("runtime_status", "prepared_status", "blocked"),
    [
        (LicenseStatus.LICENSED, LicenseStatus.BLOCKED, False),
        (LicenseStatus.BLOCKED, LicenseStatus.LICENSED, True),
    ],
)
def test_runtime_rights_have_local_and_guardian_authority(
    runtime_status, prepared_status, blocked
):
    local, guardian = _local_and_guardian_rules(
        **_qa_inputs(
            snapshot=_snapshot(license_status=runtime_status),
            prepared_status=prepared_status,
        )
    )

    assert (ProductionQARuleCode.BLOCKED_ASSET_RIGHTS in local) is blocked
    assert local == guardian


@pytest.mark.parametrize(
    ("attribution", "missing"),
    [("Runtime attribution", False), (None, True)],
)
def test_runtime_attribution_has_local_and_guardian_authority(attribution, missing):
    local, guardian = _local_and_guardian_rules(
        **_qa_inputs(
            snapshot=_snapshot(
                license_status=LicenseStatus.ATTRIBUTION_REQUIRED,
                attribution=attribution,
            )
        )
    )

    rule = ProductionQARuleCode.MISSING_REQUIRED_VISUAL_ATTRIBUTION
    assert (rule in local) is missing
    assert local == guardian


def test_runtime_invalid_visual_and_valid_subtitle_have_guardian_parity():
    local, guardian = _local_and_guardian_rules(
        **_qa_inputs(snapshot=_snapshot(contentful=False, subtitle_rendered=True))
    )

    assert ProductionQARuleCode.NO_CONTENTFUL_VISUAL_ASSET in local
    assert ProductionQARuleCode.MISSING_SUBTITLE_RENDER not in local
    assert local == guardian


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _production_request_with_artifacts(artifacts):
    return SimpleNamespace(
        id=uuid4(),
        channel_id=uuid4(),
        script_version_id=uuid4(),
        channel_dna_revision_id=uuid4(),
        target_width=1920,
        target_height=1080,
        video_codec="h264",
        script_version=None,
        scenes=[],
        assets=[],
        narration_segments=[],
        subtitle_cues=[],
        artifacts=artifacts,
    )


@pytest.mark.asyncio
async def test_guardian_exact_historical_artifact_never_switches_to_current():
    v1_id = uuid4()
    v2_id = uuid4()
    v1_snapshot = object()
    v2_snapshot = object()
    v1 = SimpleNamespace(
        id=v1_id,
        is_current=False,
        width=1280,
        height=720,
        duration_ms=1000,
        storage_uri="v1.mp4",
        content_hash="v1-hash",
        runtime_truth=SimpleNamespace(payload={"version": 1}),
    )
    v2 = SimpleNamespace(
        id=v2_id,
        is_current=True,
        width=1920,
        height=1080,
        duration_ms=2000,
        storage_uri="v2.mp4",
        content_hash="v2-hash",
        runtime_truth=SimpleNamespace(payload={"version": 2}),
    )
    prod_req = _production_request_with_artifacts([v2, v1])
    result = MagicMock()
    result.scalar_one_or_none.return_value = prod_req
    session = AsyncMock()
    session.execute.return_value = result
    detector = MediaIntegrityDetector()
    detector.adapter = MagicMock()
    detector.adapter.evaluate.return_value = []

    def parse(payload, **_kwargs):
        return v1_snapshot if payload["version"] == 1 else v2_snapshot

    with patch(
        "omega.application.guardian.detectors.media_integrity._runtime_truth_snapshot",
        side_effect=parse,
    ):
        for artifact_id, expected_snapshot, expected_path in (
            (v1_id, v1_snapshot, "v1.mp4"),
            (v2_id, v2_snapshot, "v2.mp4"),
        ):
            context = GuardianEvaluationContext(
                mission_id=uuid4(),
                checkpoint=GuardianCheckpoint.POST_RENDER,
                trigger_type=CheckTriggerType.POST_RENDER,
                production_request_id=prod_req.id,
                media_artifact_id=artifact_id,
            )
            await detector.evaluate(context, lambda: _SessionContext(session))
            kwargs = detector.adapter.evaluate.call_args.kwargs
            assert kwargs["runtime_truth_snapshot"] is expected_snapshot
            assert kwargs["artifact_file_path"] == expected_path


@pytest.mark.asyncio
async def test_guardian_exact_artifact_resolution_fails_closed():
    prod_req = _production_request_with_artifacts([])
    result = MagicMock()
    result.scalar_one_or_none.return_value = prod_req
    session = AsyncMock()
    session.execute.return_value = result
    detector = MediaIntegrityDetector()

    context = GuardianEvaluationContext(
        mission_id=uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=prod_req.id,
        media_artifact_id=uuid4(),
    )
    with pytest.raises(ValueError, match="Exact media artifact"):
        await detector.evaluate(context, lambda: _SessionContext(session))


def test_guardian_rejects_cross_artifact_runtime_truth_lineage():
    production_request_id = uuid4()
    channel_id = uuid4()
    snapshot = ProductionRuntimeTruthSnapshot.model_construct(
        lineage=SimpleNamespace(
            media_artifact_id=uuid4(),
            production_request_id=production_request_id,
            channel_id=channel_id,
        ),
        scenes=(),
        narration=(),
        subtitles=SimpleNamespace(
            cues=(),
            artifacts=(),
            requested_mode="OFF",
            effective_mode="OFF",
            fallback_applied=False,
            fallback_reason=None,
        ),
        visuals=(),
        render_target=SimpleNamespace(content_sha256="f" * 64),
    )

    with pytest.raises(ValueError, match="requested media artifact"):
        _runtime_truth_snapshot(
            snapshot,
            artifact_id=uuid4(),
            production_request_id=production_request_id,
            channel_id=channel_id,
        )


@pytest.mark.asyncio
async def test_guardian_accepts_validated_inflight_runtime_truth_for_exact_artifact():
    artifact_id = uuid4()
    prod_req = _production_request_with_artifacts([])
    result = MagicMock()
    result.scalar_one_or_none.return_value = prod_req
    session = AsyncMock()
    session.execute.return_value = result
    detector = MediaIntegrityDetector()
    detector.adapter = MagicMock()
    detector.adapter.evaluate.return_value = []
    runtime_snapshot = object()
    context = GuardianEvaluationContext(
        mission_id=uuid4(),
        checkpoint=GuardianCheckpoint.POST_RENDER,
        trigger_type=CheckTriggerType.POST_RENDER,
        production_request_id=prod_req.id,
        media_artifact_id=artifact_id,
        diagnostic_context={
            "runtime_truth_snapshot": {"schema_version": 3},
            "artifact_file_path": "inflight.mp4",
        },
    )

    with patch(
        "omega.application.guardian.detectors.media_integrity._runtime_truth_snapshot",
        return_value=runtime_snapshot,
    ) as parse:
        await detector.evaluate(context, lambda: _SessionContext(session))

    parse.assert_called_once_with(
        {"schema_version": 3},
        artifact_id=artifact_id,
        production_request_id=prod_req.id,
        channel_id=prod_req.channel_id,
    )
    assert (
        detector.adapter.evaluate.call_args.kwargs["runtime_truth_snapshot"]
        is runtime_snapshot
    )
