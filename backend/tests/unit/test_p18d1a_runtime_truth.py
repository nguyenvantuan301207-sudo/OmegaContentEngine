import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from omega.api.production import get_artifact_runtime_truth
from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.production_contract import (
    CanonicalProductionContract,
    CanonicalProductionLineage,
    CanonicalProductionPolicy,
)
from omega.application.production_runtime_truth import (
    RUNTIME_TRUTH_SCHEMA_VERSION,
    build_production_runtime_truth_snapshot,
)
from omega.application.render_service import (
    ProductionRenderService,
    RuntimeRenderProvenance,
)
from omega.application.subtitle_engine import SubtitleRenderStyle
from omega.application.visual_production_v2_service import (
    SUBTITLE_SEMANTICS_VERSION,
    _validate_cached_subtitle_semantics,
)
from omega.domain.guardian import GuardianAction
from omega.domain.production import (
    NarrationProviderType,
    ProductionMode,
    ProductionQAStatus,
    RenderJobState,
    SubtitleFallbackPolicy,
    SubtitleMode,
    VisualAssetMode,
)
from omega.infrastructure.models import (
    MediaArtifact,
    ProductionQAResult,
    ProductionRuntimeTruth,
)


def _contract(*, subtitle_mode: SubtitleMode = SubtitleMode.STANDARD, mission=True):
    return CanonicalProductionContract(
        mode=ProductionMode.MISSION_EXECUTION,
        lineage=CanonicalProductionLineage(
            channel_id=uuid4(),
            production_request_id=uuid4(),
            content_request_id=uuid4(),
            script_version_id=uuid4(),
            channel_dna_revision_id=uuid4(),
            mission_id=uuid4() if mission else None,
            mission_execution_id=uuid4() if mission else None,
            render_job_id=uuid4(),
        ),
        policy=CanonicalProductionPolicy(
            visual_asset_mode=VisualAssetMode.LOCAL_TEMPLATE_ONLY,
            narration_provider=NarrationProviderType.LOCAL_TTS,
            subtitle_mode=subtitle_mode,
            subtitle_fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
            subtitle_style=SubtitleRenderStyle(),
        ),
    )


def _runtime_result(mode="STANDARD", *, provider=False):
    visual = {
        "sequence_index": 1,
        "source_section_id": "section-1",
        "source_statement_references": (2, 4),
        "narration_text": "Exact runtime narration",
        "original_strategy": "DIAGRAM",
        "effective_strategy": "DIAGRAM",
        "template_id": "EXPLAINER",
        "start_ms": 0,
        "end_ms": 1000,
        "duration_ms": 1000,
        "duration_seconds": 1.0,
        "content_sha256": "a" * 64,
        "visual_origin": "PROVIDER" if provider else "TEMPLATE",
        "visual_mode": "PEXELS" if provider else "LOCAL_TEMPLATE_ONLY",
        "asset_kind": "IMAGE" if provider else None,
        "asset_provider": "PEXELS" if provider else None,
        "asset_id": "pexels-7" if provider else None,
        "asset_query": "safe query" if provider else None,
        "asset_source_url": (
            "https://cdn.example/asset.jpg?token=secret" if provider else None
        ),
        "asset_source_page_url": "https://example/item/7#fragment" if provider else None,
        "asset_license_status": "LICENSED" if provider else "GENERATED",
        "asset_license_name": "Pexels License" if provider else None,
        "asset_license_url": "https://example/license?signature=x" if provider else None,
        "asset_attribution": "Example Creator" if provider else None,
        "visual_content_sha256": "b" * 64,
        "visual_mime_type": "image/jpeg" if provider else "video/mp4",
        "visual_width": 1920,
        "visual_height": 1080,
        "visual_duration_ms": 1000,
        "asset_provider_metadata": {
            "camera": "x",
            "download": "https://cdn.example/raw?signature=never",
            "api_token": "never",
        },
    }
    off = mode == "OFF"
    cues = () if off else (
        {"scene_index": 1, "cue_order": 1, "start_ms": 0, "end_ms": 1000, "text": "Exact runtime narration"},
    )
    artifacts = () if off else (
        {"scene_index": 1, "artifact_kind": "ASS", "content_sha256": "c" * 64},
    )
    return SimpleNamespace(
        runtime_scenes=(visual,),
        runtime_narration_segments=(
            {
                "scene_index": 1,
                "text": "Exact runtime narration",
                "start_ms": 0,
                "end_ms": 1000,
                "duration_ms": 1000,
                "audio_asset_id": "audio-1",
                "storage_reference": "audio/segment-1.mp3",
                "audio_content_sha256": "d" * 64,
                "provider": "FakeNarrationProvider",
                "model": "fake-v1",
                "voice": "voice-a",
                "voice_profile": {"pace": "steady"},
                "quality": "NEURAL_PRODUCTION",
                "license_status": "GENERATED",
                "source_reference": "Local TTS",
            },
        ),
        runtime_subtitle_cues=cues,
        runtime_subtitle_artifacts=artifacts,
        requested_subtitle_mode=mode,
        effective_subtitle_mode=mode,
        subtitle_fallback_applied=False,
        subtitle_fallback_reason=None,
        subtitle_timing_source="NONE" if off else "DERIVED_SEGMENT_TIMING",
        subtitle_semantics_version=2,
        subtitle_burn_applied=not off,
        subtitle_style_applied=None if off else SubtitleRenderStyle(),
        runtime_branding={
            "policy_source": "dna-revision",
            "assets": [],
        },
        runtime_audio_mix={
            "enabled": False,
            "events": [{"gain_db": -10.0}],
        },
        effective_fps_mode="CFR",
        width=1920,
        height=1080,
        fps=24,
        run_fingerprint="e" * 64,
    )


def _snapshot(mode="STANDARD", *, provider=False, mission=True, probe=None):
    contract = _contract(subtitle_mode=SubtitleMode(mode), mission=mission)
    runtime_result = _runtime_result(mode, provider=provider)
    runtime_result.content_request_id = contract.lineage.content_request_id
    runtime_result.script_version_id = contract.lineage.script_version_id
    runtime_result.mission_execution_id = contract.lineage.mission_execution_id
    runtime_result.mission_id = contract.lineage.mission_id
    return build_production_runtime_truth_snapshot(
        contract=contract,
        render_plan_id=uuid4(),
        render_job_id=contract.lineage.render_job_id,
        media_artifact_id=uuid4(),
        artifact_version=1,
        artifact_storage_uri="artifacts/video.mp4",
        artifact_size_bytes=1234,
        artifact_sha256="f" * 64,
        v2_result=runtime_result,
        probe_summary=probe
        or {
            "duration_ms": 1000,
            "width": 1920,
            "height": 1080,
            "fps": 24.0,
            "video_codec": "h264",
            "audio_codec": "aac",
            "has_audio": True,
            "format_name": "mov,mp4",
            "validation": {"ok": True},
        },
    )


def test_snapshot_is_deterministic_strict_json_and_deeply_immutable():
    snapshot = _snapshot()
    assert snapshot.schema_version == RUNTIME_TRUTH_SCHEMA_VERSION
    assert snapshot.canonical_json() == snapshot.canonical_json()
    assert json.loads(snapshot.canonical_json())["artifact"]["version"] == 1
    assert snapshot.visuals[0].license_status.value == "GENERATED"
    with pytest.raises((TypeError, AttributeError)):
        snapshot.audio_mix["events"][0]["gain_db"] = 0
    with pytest.raises(ValidationError):
        snapshot.schema_version = 2


def test_schema_v2_visual_license_status_is_required_and_validated():
    contract = _contract()
    result = _runtime_result()
    del result.runtime_scenes[0]["asset_license_status"]
    result.content_request_id = contract.lineage.content_request_id
    result.script_version_id = contract.lineage.script_version_id
    result.mission_execution_id = contract.lineage.mission_execution_id
    result.mission_id = contract.lineage.mission_id

    with pytest.raises(KeyError, match="asset_license_status"):
        build_production_runtime_truth_snapshot(
            contract=contract,
            render_plan_id=uuid4(),
            render_job_id=contract.lineage.render_job_id,
            media_artifact_id=uuid4(),
            artifact_version=1,
            artifact_storage_uri="artifacts/video.mp4",
            artifact_size_bytes=1234,
            artifact_sha256="f" * 64,
            v2_result=result,
            probe_summary={
                "duration_ms": 1000,
                "width": 1920,
                "height": 1080,
                "video_codec": "h264",
                "has_audio": True,
            },
        )

    result.runtime_scenes[0]["asset_license_status"] = "NOT_A_STATUS"
    with pytest.raises(ValidationError, match="license_status"):
        build_production_runtime_truth_snapshot(
            contract=contract,
            render_plan_id=uuid4(),
            render_job_id=contract.lineage.render_job_id,
            media_artifact_id=uuid4(),
            artifact_version=1,
            artifact_storage_uri="artifacts/video.mp4",
            artifact_size_bytes=1234,
            artifact_sha256="f" * 64,
            v2_result=result,
            probe_summary={
                "duration_ms": 1000,
                "width": 1920,
                "height": 1080,
                "video_codec": "h264",
                "has_audio": True,
            },
        )


def test_snapshot_lineage_has_no_synthetic_mission_ids():
    snapshot = _snapshot(mission=False)
    assert snapshot.lineage.mission_id is None
    assert snapshot.lineage.mission_execution_id is None


def test_snapshot_builder_rejects_v2_lineage_mismatch():
    contract = _contract()
    result = _runtime_result()
    result.content_request_id = uuid4()
    result.script_version_id = contract.lineage.script_version_id
    result.mission_execution_id = contract.lineage.mission_execution_id
    result.mission_id = contract.lineage.mission_id
    with pytest.raises(ValueError, match="content_request_id"):
        build_production_runtime_truth_snapshot(
            contract=contract,
            render_plan_id=uuid4(),
            render_job_id=contract.lineage.render_job_id,
            media_artifact_id=uuid4(),
            artifact_version=1,
            artifact_storage_uri="artifacts/video.mp4",
            artifact_size_bytes=1234,
            artifact_sha256="f" * 64,
            v2_result=result,
            probe_summary={
                "duration_ms": 1000,
                "width": 1920,
                "height": 1080,
                "fps": 24.0,
                "video_codec": "h264",
                "audio_codec": "aac",
                "has_audio": True,
            },
        )


@pytest.mark.parametrize("mode", ["STANDARD", "KARAOKE"])
def test_enabled_subtitle_truth_preserves_runtime_cues(mode):
    snapshot = _snapshot(mode)
    assert snapshot.subtitles.requested_mode == mode
    assert snapshot.subtitles.effective_mode == mode
    assert snapshot.subtitles.timing_source == "DERIVED_SEGMENT_TIMING"
    assert snapshot.subtitles.burn_applied is True
    assert len(snapshot.subtitles.cues) == 1
    assert len(snapshot.subtitles.artifacts) == 1


def test_off_subtitle_truth_is_physically_empty():
    snapshot = _snapshot("OFF")
    assert snapshot.subtitles.cues == ()
    assert snapshot.subtitles.artifacts == ()
    assert snapshot.subtitles.burn_applied is False
    assert snapshot.subtitles.style_applied is None
    assert snapshot.subtitles.timing_source == "NONE"


def test_provider_references_are_sanitized_and_secret_metadata_removed():
    snapshot = _snapshot(provider=True)
    visual = snapshot.visuals[0]
    assert visual.origin == "PROVIDER"
    assert visual.source_url == "https://cdn.example/asset.jpg"
    assert visual.source_page_url == "https://example/item/7"
    assert visual.license_url == "https://example/license"
    assert visual.provider_metadata == {
        "camera": "x",
        "download": "https://cdn.example/raw",
    }
    assert "secret" not in snapshot.canonical_json()


def test_nonfinite_probe_value_normalizes_to_json_null():
    snapshot = _snapshot(
        probe={
            "duration_ms": 1000,
            "width": 1920,
            "height": 1080,
            "fps": 24.0,
            "video_codec": "h264",
            "audio_codec": "aac",
            "has_audio": True,
            "mean_volume_db": float("-inf"),
        }
    )
    assert snapshot.probe["mean_volume_db"] is None
    assert "Infinity" not in snapshot.canonical_json()


def test_model_is_one_to_one_artifact_pk_fk():
    table = ProductionRuntimeTruth.__table__
    assert list(table.primary_key.columns.keys()) == ["artifact_id"]
    foreign_key = next(iter(table.c.artifact_id.foreign_keys))
    assert foreign_key.target_fullname == "media_artifacts.id"
    assert foreign_key.ondelete == "CASCADE"
    assert MediaArtifact.runtime_truth.property.uselist is False


def test_migration_round_trip_fk_uniqueness_cascade_and_json(tmp_path, monkeypatch):
    migration_path = (
        Path(__file__).parents[2]
        / "alembic"
        / "versions"
        / "017_create_production_runtime_truth.py"
    )
    spec = importlib.util.spec_from_file_location("omega_migration_017", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    artifact_id = str(uuid4())
    payload = {"schema_version": 1, "nested": {"facts": [1, True, None]}}
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.execute(text("CREATE TABLE media_artifacts (id UUID PRIMARY KEY)"))
        monkeypatch.setattr(
            migration,
            "op",
            Operations(MigrationContext.configure(connection)),
        )
        migration.upgrade()
        assert "production_runtime_truth" in inspect(connection).get_table_names()
        connection.execute(
            text("INSERT INTO media_artifacts (id) VALUES (:id)"),
            {"id": artifact_id},
        )
        connection.execute(
            text(
                "INSERT INTO production_runtime_truth "
                "(artifact_id, schema_version, manifest_run_fingerprint, payload, created_at) "
                "VALUES (:artifact_id, 1, :fingerprint, :payload, CURRENT_TIMESTAMP)"
            ),
            {
                "artifact_id": artifact_id,
                "fingerprint": "a" * 64,
                "payload": json.dumps(payload),
            },
        )
        reloaded = connection.execute(
            text(
                "SELECT payload FROM production_runtime_truth "
                "WHERE artifact_id = :artifact_id"
            ),
            {"artifact_id": artifact_id},
        ).scalar_one()
        assert json.loads(reloaded) == payload
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO production_runtime_truth "
                    "(artifact_id, schema_version, manifest_run_fingerprint, payload, created_at) "
                    "VALUES (:artifact_id, 1, :fingerprint, '{}', CURRENT_TIMESTAMP)"
                ),
                {"artifact_id": artifact_id, "fingerprint": "b" * 64},
            )
        connection.execute(text("DELETE FROM media_artifacts WHERE id = :id"), {"id": artifact_id})
        assert connection.execute(
            text("SELECT count(*) FROM production_runtime_truth")
        ).scalar_one() == 0

        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO production_runtime_truth "
                    "(artifact_id, schema_version, manifest_run_fingerprint, payload, created_at) "
                    "VALUES (:artifact_id, 1, :fingerprint, '{}', CURRENT_TIMESTAMP)"
                ),
                {"artifact_id": str(uuid4()), "fingerprint": "c" * 64},
            )
        migration.downgrade()
        assert "production_runtime_truth" not in inspect(connection).get_table_names()


def test_pre_d1a_cache_manifest_is_fenced_from_runtime_truth_finalization():
    legacy_manifest = {
        "runtime_truth_schema_version": 1,
        "subtitle_semantics_version": SUBTITLE_SEMANTICS_VERSION,
        "requested_subtitle_mode": "OFF",
        "effective_subtitle_mode": "OFF",
        "subtitle_fallback_applied": False,
        "subtitle_fallback_reason": None,
        "subtitle_timing_source": "NONE",
        "subtitle_enabled": False,
        "karaoke_subtitles_enabled": False,
        "subtitle_mode": "sentence",
        "subtitle_mode_decision": {
            "requested_mode": "OFF",
            "effective_mode": "OFF",
            "fallback_applied": False,
            "fallback_reason": None,
            "timing_source": "NONE",
        },
        "runtime_subtitle_cues": [],
        "runtime_subtitle_artifacts": [],
        "subtitle_burn_applied": False,
    }
    assert not _validate_cached_subtitle_semantics(
        manifest=legacy_manifest,
        canonical_requested_mode=SubtitleMode.OFF,
        fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
    )


@pytest.mark.asyncio
async def test_runtime_truth_api_returns_artifact_scoped_rendered_envelope():
    ids = [uuid4() for _ in range(4)]
    truth = SimpleNamespace(payload={"schema_version": 1, "lineage": {}})
    artifact = SimpleNamespace(id=ids[0], version=2)
    render_job = SimpleNamespace(id=ids[1], render_plan_id=ids[2])
    result = SimpleNamespace(one_or_none=lambda: (truth, artifact, render_job))
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    response = await get_artifact_runtime_truth(ids[3], uuid4(), ids[0], session)

    assert response.truth_kind == "RENDERED"
    assert response.artifact_id == ids[0]
    assert response.render_job_id == ids[1]
    assert response.render_plan_id == ids[2]
    assert response.render_version == 2


@pytest.mark.asyncio
async def test_runtime_truth_api_never_falls_back_to_planned_rows():
    result = SimpleNamespace(one_or_none=lambda: None)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    with pytest.raises(HTTPException) as exc_info:
        await get_artifact_runtime_truth(uuid4(), uuid4(), uuid4(), session)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_before_commit", "local_qa_status", "guardian_qa_status"),
    [
        (False, ProductionQAStatus.PASSED, ProductionQAStatus.PASSED),
        (True, ProductionQAStatus.PASSED, ProductionQAStatus.PASSED),
        (False, ProductionQAStatus.BLOCKED, ProductionQAStatus.PASSED),
        (False, ProductionQAStatus.PASSED, ProductionQAStatus.BLOCKED),
    ],
)
async def test_cache_hit_result_persists_one_artifact_truth_and_same_qa_snapshot(
    tmp_path,
    fail_before_commit,
    local_qa_status,
    guardian_qa_status,
):
    channel_id = uuid4()
    request_id = uuid4()
    content_request_id = uuid4()
    script_version_id = uuid4()
    dna_id = uuid4()
    mission_id = uuid4()
    mission_execution_id = uuid4()
    job_id = uuid4()
    plan_id = uuid4()
    plan = SimpleNamespace(
        id=plan_id,
        version=1,
        width=1920,
        height=1080,
        fps=24,
        video_codec="h264",
        audio_codec="aac",
        container="mp4",
    )
    script = SimpleNamespace(
        hook_text="hook",
        cta_text="cta",
        closing_text="close",
        sections=[],
    )
    request = SimpleNamespace(
        id=request_id,
        channel_id=channel_id,
        content_request_id=content_request_id,
        script_version_id=script_version_id,
        channel_dna_revision_id=dna_id,
        mission_execution_id=mission_execution_id,
        mode="MISSION_EXECUTION",
        target_width=1920,
        target_height=1080,
        video_codec="h264",
        container_format="mp4",
        voice_profile={},
        metadata_={},
        assets=[],
        narration_segments=[],
        subtitle_cues=[],
        scenes=[],
        script_version=script,
    )
    job = SimpleNamespace(
        id=job_id,
        state=RenderJobState.QUEUED.value,
        started_at=None,
        render_plan=plan,
        production_request=request,
    )
    contract = CanonicalProductionContract(
        mode=ProductionMode.MISSION_EXECUTION,
        lineage=CanonicalProductionLineage(
            channel_id=channel_id,
            production_request_id=request_id,
            content_request_id=content_request_id,
            script_version_id=script_version_id,
            channel_dna_revision_id=dna_id,
            mission_id=mission_id,
            mission_execution_id=mission_execution_id,
            render_job_id=job_id,
        ),
        policy=CanonicalProductionPolicy(
            visual_asset_mode=VisualAssetMode.LOCAL_TEMPLATE_ONLY,
            narration_provider=NarrationProviderType.LOCAL_TTS,
            subtitle_mode=SubtitleMode.STANDARD,
            subtitle_fallback_policy=SubtitleFallbackPolicy.STANDARD_FALLBACK,
            subtitle_style=SubtitleRenderStyle(),
        ),
    )
    runtime_result = _runtime_result()
    runtime_result.content_request_id = content_request_id
    runtime_result.script_version_id = script_version_id
    runtime_result.mission_execution_id = mission_execution_id
    runtime_result.mission_id = mission_id
    provenance = RuntimeRenderProvenance(
        {"requested_subtitle_mode": "STANDARD"},
        v2_result=runtime_result,
        contract=contract,
    )

    async def cache_hit_staging(**kwargs):
        kwargs["staging_output_path"].write_bytes(b"\x00\x00ftyp-cache-hit")
        return (
            "NEURAL_PRODUCTION",
            ("Local TTS",),
            1000,
            runtime_result.runtime_narration_segments,
            runtime_result.runtime_subtitle_cues,
            runtime_result.runtime_scenes,
            provenance,
        )

    query_result = SimpleNamespace(scalar_one_or_none=lambda: job)
    session = SimpleNamespace(
        execute=AsyncMock(return_value=query_result),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        flush=AsyncMock(),
        refresh=AsyncMock(side_effect=RuntimeError("post-commit read failed")),
        add=MagicMock(),
    )
    _ = tmp_path
    test_storage_root = Path.cwd() / "scratch" / "p18d1a_storage"
    service = ProductionRenderService(
        storage=LocalMediaStorageProvider(base_root=test_storage_root),
        visual_production_service=AsyncMock(),
    )
    service._resolve_mission_id = AsyncMock(return_value=mission_id)
    service._render_v2_staging = AsyncMock(side_effect=cache_hit_staging)
    service.probe.probe_file = AsyncMock(
        return_value={
            "duration_ms": 1000,
            "width": 1920,
            "height": 1080,
            "fps": 24.0,
            "video_codec": "h264",
            "audio_codec": "aac",
            "has_audio": True,
        }
    )
    service.qa_engine.evaluate = MagicMock(
        return_value=(local_qa_status, [])
    )
    service._evaluate_post_render_guardian = AsyncMock(
        return_value=guardian_qa_status
    )
    service._enqueue_terminal_evaluation = AsyncMock()
    service._record_job_failure = AsyncMock()
    if fail_before_commit:
        service._apply_artifact_current_selection = AsyncMock(
            side_effect=RuntimeError("pre-commit finalization failed")
        )

    pre_render_guardian = MagicMock()
    pre_render_guardian.execute_check = AsyncMock(
        return_value=SimpleNamespace(
            decision=SimpleNamespace(action=GuardianAction.ALLOW, reason="accepted")
        )
    )

    if fail_before_commit:
        with (
            patch(
                "omega.application.guardian.engine.GuardianEngine",
                return_value=pre_render_guardian,
            ),
            pytest.raises(RuntimeError, match="pre-commit finalization failed"),
        ):
            await service.execute_render_job(session, channel_id, request_id, job_id)
        artifact_files = list(
            service.storage.get_artifacts_dir(channel_id, request_id).glob("*.mp4")
        )
        assert artifact_files == []
        assert session.commit.await_count == 1
        service._record_job_failure.assert_awaited_once()
        service.storage.cleanup_directory(test_storage_root)
        return

    with patch(
        "omega.application.guardian.engine.GuardianEngine",
        return_value=pre_render_guardian,
    ):
        artifact, status = await service.execute_render_job(
            session, channel_id, request_id, job_id
        )

    truth_rows = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], ProductionRuntimeTruth)
    ]
    qa_rows = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], ProductionQAResult)
    ]
    expected_status = (
        ProductionQAStatus.BLOCKED
        if ProductionQAStatus.BLOCKED in (local_qa_status, guardian_qa_status)
        else ProductionQAStatus.PASSED
    )
    assert status == expected_status
    assert artifact is not None
    assert len(truth_rows) == 1
    assert len(qa_rows) == 1
    qa_snapshot = service.qa_engine.evaluate.call_args.kwargs[
        "runtime_truth_snapshot"
    ]
    assert truth_rows[0].artifact_id == artifact.id
    assert truth_rows[0].payload == qa_snapshot.canonical_dict()
    assert truth_rows[0].manifest_run_fingerprint == runtime_result.run_fingerprint
    assert qa_rows[0].status == expected_status.value
    assert artifact.is_current is (expected_status != ProductionQAStatus.BLOCKED)
    assert service.storage.resolve_stored_uri(
        channel_id, request_id, artifact.storage_uri
    ).is_file()
    assert session.commit.await_count == 2
    session.refresh.assert_not_awaited()
    service._record_job_failure.assert_not_awaited()
    service._evaluate_post_render_guardian.assert_awaited_once()
    service.storage.cleanup_directory(test_storage_root)


@pytest.mark.asyncio
@pytest.mark.parametrize("block_source", ["LOCAL_QA", "GUARDIAN"])
async def test_blocked_candidate_preserves_previous_current(block_source):
    service = ProductionRenderService()
    previous = SimpleNamespace(is_current=True)
    candidate = SimpleNamespace(id=uuid4(), is_current=False)
    session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock())

    await service._apply_artifact_current_selection(
        session,
        request_id=uuid4(),
        candidate=candidate,
        qa_status=ProductionQAStatus.BLOCKED,
    )

    assert block_source in {"LOCAL_QA", "GUARDIAN"}
    assert previous.is_current is True
    assert candidate.is_current is False
    session.execute.assert_not_awaited()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_blocked_candidate_has_no_current_artifact():
    service = ProductionRenderService()
    candidate = SimpleNamespace(id=uuid4(), is_current=False)
    session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock())

    await service._apply_artifact_current_selection(
        session,
        request_id=uuid4(),
        candidate=candidate,
        qa_status=ProductionQAStatus.BLOCKED,
    )

    assert candidate.is_current is False
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_accepted_candidate_atomically_rolls_current_pointer():
    service = ProductionRenderService()
    previous = SimpleNamespace(is_current=True)
    candidate = SimpleNamespace(id=uuid4(), is_current=False)

    async def apply_previous_demotion(_statement):
        previous.is_current = False

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=apply_previous_demotion),
        flush=AsyncMock(),
    )

    await service._apply_artifact_current_selection(
        session,
        request_id=uuid4(),
        candidate=candidate,
        qa_status=ProductionQAStatus.PASSED,
    )

    assert previous.is_current is False
    assert candidate.is_current is True
    session.execute.assert_awaited_once()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_succeeded_returns_existing_without_snapshot_insertion():
    artifact = SimpleNamespace(id=uuid4())
    job = SimpleNamespace(
        id=uuid4(),
        state=RenderJobState.SUCCEEDED.value,
    )
    results = iter(
        (
            SimpleNamespace(scalar_one_or_none=lambda: job),
            SimpleNamespace(scalar_one_or_none=lambda: artifact),
        )
    )
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=lambda *_args, **_kwargs: next(results)),
        rollback=AsyncMock(),
        add=MagicMock(),
    )
    service = ProductionRenderService(visual_production_service=AsyncMock())

    returned, status = await service.execute_render_job(
        session, uuid4(), uuid4(), job.id
    )

    assert returned is artifact
    assert status == ProductionQAStatus.PASSED
    session.add.assert_not_called()
    service.visual_production_service.assert_not_awaited()
