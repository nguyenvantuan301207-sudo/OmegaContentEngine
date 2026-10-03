"""Integration tests for P22-D Canonical Post-Render Visual QA Production Gate.

Verifies the single unambiguous canonical authority chain:
Pre-render P22-D QA
    ↓
Render
    ↓
Post-render P22-D PhysicalArtifact QA
    ↓
ProductionQAEngine
    ↓
Guardian
    ↓
RuntimeTruth / final accepted artifact

Cases:
Case A — valid artifact:
    pre-render Visual QA PASS
    → render artifact
    → post-render Visual QA PASS
    → ProductionQA allowed
    → final acceptance path allowed (media_artifact.is_current = True, qa_status = PASSED)

Case B — blank/invalid visual artifact:
    render artifact physically exists on disk
    → PhysicalArtifactQAEvaluator returns BLANK_FRAME_DETECTED (BLOCKER/FAIL)
    → downstream final acceptance blocked (media_artifact.is_current = False, qa_status = BLOCKED)
    → artifact remains on disk for post-mortem diagnosis

Case C — corrupt/invalid artifact:
    render artifact physically exists with corrupted header/payload
    → PhysicalArtifactQAEvaluator returns CORRUPT_ARTIFACT (BLOCKER/FAIL)
    → downstream final acceptance blocked (media_artifact.is_current = False, qa_status = BLOCKED)
    → no successful RuntimeTruth / publish-ready accepted state
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.media_storage import LocalMediaStorageProvider, compute_sha256
from omega.application.production_service import ProductionService
from omega.application.render_service import ProductionRenderService
from omega.application.subtitle_engine import SubtitleRenderStyle
from omega.domain.guardian import GuardianAction
from omega.domain.production import ProductionQAStatus, ProductionRequestCreate
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    MediaArtifact,
    Mission,
    MissionExecution,
    ProductionQAResult,
    ProductionRequest,
    ProductionRuntimeTruth,
    ResearchBrief,
    ResearchRequest,
    ScriptSection,
    ScriptStatement,
    ScriptVersion,
    TopicCandidate,
)


def _generate_mp4_bytes(video_source: str, duration_sec: float = 0.2) -> bytes:
    """Generate a test MP4 using host ffmpeg or isolated container."""
    if "=" in video_source:
        input_filter = f"{video_source}:duration={duration_sec}:size=1920x1080:rate=30"
    else:
        input_filter = f"{video_source}=duration={duration_sec}:size=1920x1080:rate=30"
    args = [
        "-f", "lavfi",
        "-i", input_filter,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(["ffmpeg"] + args, capture_output=True, timeout=20)
        if proc.returncode == 0 and len(proc.stdout) > 100:
            return proc.stdout
    except (FileNotFoundError, OSError):
        pass

    proc = subprocess.run(
        ["docker", "exec", "-i", "p20c-api", "ffmpeg"] + args,
        capture_output=True,
        timeout=25,
        check=True,
    )
    return proc.stdout


class FakeV2Result:
    """Mock result from visual_production_service.render_canonical_production."""

    def __init__(
        self,
        req: ProductionRequest,
        output_path: Path,
        sha: str,
        mission_id: Any = None,
    ) -> None:
        self.production_request_id = req.id
        self.mission_execution_id = req.mission_execution_id
        self.content_request_id = req.content_request_id
        self.script_version_id = req.script_version_id
        self.mission_id = mission_id
        self.width = 1920
        self.height = 1080
        self.fps = 30
        self.output_path = str(output_path)
        self.content_sha256 = sha
        self.narration_quality = "NEURAL_PRODUCTION"
        self.narration_source_refs = ("Gemini TTS",)
        self.runtime_timeline_duration_ms = 200
        self.runtime_narration_segments = (
            {
                "scene_index": 1,
                "text": "Welcome to the post-render gate verification.",
                "start_ms": 0,
                "end_ms": 200,
                "duration_ms": 200,
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
        )
        self.runtime_subtitle_cues = (
            {"scene_index": 1, "cue_order": 1, "start_ms": 0, "end_ms": 190, "text": "test"},
        )
        self.runtime_subtitle_artifacts = (
            {"scene_index": 1, "artifact_kind": "ASS", "content_sha256": "c" * 64},
        )
        self.runtime_branding = {
            "policy_source": "dna-revision",
            "assets": [],
        }
        self.runtime_audio_mix = {
            "enabled": False,
            "events": [{"gain_db": -10.0}],
        }
        self.runtime_scenes = (
            {
                "sequence_index": 1,
                "source_section_id": "section-1",
                "source_statement_references": (1,),
                "narration_text": "Welcome to the post-render gate verification.",
                "original_strategy": "TITLE_MOTION",
                "effective_strategy": "TITLE_MOTION",
                "template_id": "TITLE_MOTION",
                "start_ms": 0,
                "end_ms": 200,
                "duration_ms": 200,
                "duration_seconds": 0.2,
                "content_sha256": sha,
                "visual_origin": "TEMPLATE",
                "visual_mode": "LOCAL_TEMPLATE_ONLY",
                "text_truncated": False,
            },
        )
        self.runtime_visual_beats = (
            {
                "parent_scene_index": 1,
                "materialized_beat_index": 0,
                "source_editorial_beat_index": 0,
                "source_statement_references": (1,),
                "semantic_role": "LEGACY_PARENT_SCENE",
                "start_offset_ms": 0,
                "end_offset_ms": 200,
                "duration_ms": 200,
                "template_id": "TITLE_MOTION",
                "camera_motion_intent": "STATIC",
                "transition_intent": "HARD_CUT",
                "asset_action": "LOCAL_TEMPLATE",
                "reuse_from_beat_index": None,
                "visual_origin": "TEMPLATE",
                "asset_kind": "VIDEO",
                "provider": None,
                "provider_asset_id": None,
                "source_url": None,
                "source_page_url": None,
                "license_status": "GENERATED",
                "license_name": None,
                "license_url": None,
                "attribution": None,
                "allowed_attribution_channels": (),
                "provider_metadata": {},
                "provider_asset_content_sha256": None,
                "rendered_beat_clip_sha256": sha,
            },
        )
        self.subtitle_style_applied = SubtitleRenderStyle()
        self.subtitle_burn_applied = True
        self.effective_fps_mode = "CFR"
        self.requested_subtitle_mode = "STANDARD"
        self.effective_subtitle_mode = "STANDARD"
        self.subtitle_fallback_applied = False
        self.subtitle_fallback_reason = None
        self.subtitle_timing_source = "DERIVED_SEGMENT_TIMING"
        self.subtitle_semantics_version = 2
        self.subtitle_mode_decision = None
        self.run_fingerprint = hashlib.sha256(b"p22d-canonical-fingerprint").hexdigest()


async def _seed_canonical_pipeline_data(db_session: AsyncSession) -> dict[str, Any]:
    """Seed minimal canonical models for a render job."""
    channel = Channel(
        id=uuid4(),
        slug=f"p22d-test-{uuid4().hex[:8]}",
        name="P22-D Canonical Post-Render Gate Channel",
    )
    dna = ChannelDNARevision(
        id=uuid4(),
        channel_id=channel.id,
        version=1,
        snapshot={
            "tone": "direct",
            "visual_style": "editorial",
            "target_audience": "qa-engineers",
        },
        change_reason="initial",
        actor="SYSTEM",
    )
    topic = TopicCandidate(
        id=uuid4(),
        channel_id=channel.id,
        title="P22-D Gate Testing",
        normalized_title="p22-d gate testing",
        summary="Deterministic post-render visual gate verification",
        source_type="MANUAL",
        source_name="p22d",
        topic_fingerprint=hashlib.sha256(b"p22d-topic").hexdigest(),
        status="SELECTED",
    )
    mission = Mission(
        id=uuid4(),
        channel_id=channel.id,
        title="P22-D Mission",
        objective="Verify post-render Visual QA authority chain",
        state="RUNNING",
        autonomy_level="SUPERVISED",
        guardian_epoch=1,
    )
    mission_execution = MissionExecution(
        id=uuid4(),
        mission_id=mission.id,
        channel_dna_revision_id=dna.id,
        state="RUNNING",
        trigger_type="MANUAL",
    )
    db_session.add_all([channel, dna, topic, mission, mission_execution])
    await db_session.flush()

    research_request = ResearchRequest(
        id=uuid4(),
        channel_id=channel.id,
        topic_candidate_id=topic.id,
        mode="INTERACTIVE",
        status="COMPLETED",
        outcome="SUFFICIENT",
    )
    brief = ResearchBrief(
        id=uuid4(),
        research_request_id=research_request.id,
        channel_id=channel.id,
        topic_candidate_id=topic.id,
        version=1,
        is_current=True,
        outcome="SUFFICIENT",
        overall_confidence=100.0,
        title="P22-D Brief",
        summary="Deterministic post-render gate verification",
    )
    db_session.add_all([research_request, brief])
    await db_session.flush()

    content_request = ContentGenerationRequest(
        id=uuid4(),
        channel_id=channel.id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna.id,
        mission_execution_id=mission_execution.id,
        idempotency_key=f"p22d-content-{uuid4().hex}",
        mode="MISSION_EXECUTION",
        status="SUCCEEDED",
        outcome="GENERATED",
        content_type="YOUTUBE_LONGFORM",
        target_duration_seconds=3,
        target_word_count=50,
        language="en",
        region="US",
    )
    script = ScriptVersion(
        id=uuid4(),
        content_request_id=content_request.id,
        version=1,
        is_current=True,
        title="P22-D Canonical Post-Render Gate Script",
        hook_text="Welcome to the post-render gate verification.",
        closing_text="Visual QA authority is enforced.",
        cta_text="Verify fail-closed acceptance.",
        estimated_word_count=30,
        estimated_duration_seconds=3,
        qa_status="PASSED",
        style_snapshot={},
    )
    db_session.add_all([content_request, script])
    await db_session.flush()

    section = ScriptSection(
        id=uuid4(),
        script_version_id=script.id,
        section_order=1,
        heading="Introduction",
        narration_text="Welcome to the post-render gate verification.",
        estimated_duration_seconds=3,
    )
    db_session.add(section)
    await db_session.flush()

    stmt = ScriptStatement(
        id=uuid4(),
        script_section_id=section.id,
        statement_order=1,
        statement_text="Welcome to the post-render gate verification.",
        statement_type="NARRATIVE",
    )
    db_session.add(stmt)
    await db_session.commit()

    return {
        "channel": channel,
        "dna": dna,
        "mission": mission,
        "mission_execution": mission_execution,
        "script": script,
    }


@pytest.fixture
def valid_mp4_bytes() -> bytes:
    """Non-blank patterned 1920x1080 MP4."""
    return _generate_mp4_bytes("testsrc", duration_sec=0.2)


@pytest.fixture
def blank_mp4_bytes() -> bytes:
    """Uniform black 1920x1080 MP4."""
    return _generate_mp4_bytes("color=c=black", duration_sec=0.2)


@pytest.fixture
def corrupt_artifact_bytes() -> bytes:
    """Corrupted artifact with valid box prefix but corrupt inner content."""
    return b"\x00\x00\x00\x18ftypisom" + b"\xff\xff\x00\x00CORRUPT_NOT_BOXES" * 20


@pytest.mark.asyncio
async def test_case_a_valid_artifact_passes_post_render_gate(
    db_session: AsyncSession,
    valid_mp4_bytes: bytes,
) -> None:
    """Case A — Valid artifact:

    pre-render Visual QA PASS
    → render artifact
    → post-render Visual QA PASS
    → ProductionQA allowed
    → final acceptance path allowed (media_artifact.is_current = True, qa_status = PASSED).
    """
    seeds = await _seed_canonical_pipeline_data(db_session)
    channel = seeds["channel"]
    script = seeds["script"]
    mission = seeds["mission"]
    mission_execution = seeds["mission_execution"]

    storage_root = Path.cwd() / "scratch" / f"p22d_a_{uuid4().hex[:6]}"
    storage = LocalMediaStorageProvider(base_root=str(storage_root))
    mock_v2_service = AsyncMock()

    render_service = ProductionRenderService(
        storage=storage,
        visual_production_service=mock_v2_service,
    )
    render_service.probe.probe_file = AsyncMock(
        return_value={
            "duration_ms": 200,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "video_codec": "h264",
            "audio_codec": "aac",
            "has_video": True,
            "has_audio": True,
        }
    )

    try:
        production_service = ProductionService()
        req = await production_service.create_production_request(
            db_session,
            channel.id,
            ProductionRequestCreate(
                script_version_id=script.id,
                target_width=1920,
                target_height=1080,
                fps=30,
                video_codec="h264",
                audio_codec="aac",
                container_format="mp4",
                mission_execution_id=mission_execution.id,
                metadata={
                    "render_settings": {
                        "visual_asset_mode": "PEXELS",
                        "narration_provider": "NEURAL",
                        "subtitle_mode": "STANDARD",
                    }
                },
            ),
            idempotency_key=f"p22d-case-a-{uuid4().hex}",
        )
        prepared_req = await production_service.prepare_production(
            db_session, channel.id, req.id
        )

        job, plan, is_new = await production_service.allocate_render_job(
            db_session,
            channel.id,
            prepared_req.id,
            idempotency_key=f"p22d-job-a-{uuid4().hex}",
        )
        assert is_new is True

        # Prepare V2 output file
        storage_root.mkdir(parents=True, exist_ok=True)
        v2_output = storage_root / "v2_valid.mp4"
        v2_output.write_bytes(valid_mp4_bytes)
        sha = compute_sha256(v2_output)
        fake_result = FakeV2Result(prepared_req, v2_output, sha, mission_id=mission.id)
        mock_v2_service.render_canonical_production.return_value = fake_result

        guardian_engine = MagicMock()
        guardian_engine.execute_check = AsyncMock(
            return_value=MagicMock(
                decision=MagicMock(action=GuardianAction.ALLOW, reason="acceptance passed")
            )
        )

        with patch("omega.application.guardian.engine.GuardianEngine", return_value=guardian_engine):
            artifact, qa_status = await render_service.execute_render_job(
                db_session,
                channel.id,
                prepared_req.id,
                job.id,
            )

        # QA Result row
        qa_result = await db_session.execute(
            select(ProductionQAResult).where(ProductionQAResult.artifact_id == artifact.id)
        )
        qa_row = qa_result.scalar_one_or_none()

        # Assertions for Case A
        assert artifact is not None
        assert qa_status == ProductionQAStatus.PASSED, f"QA Status was {qa_status}, findings: {getattr(qa_row, 'findings', None)}"

        # DB persistence assertions
        db_artifact = await db_session.get(MediaArtifact, artifact.id)
        assert db_artifact is not None
        assert db_artifact.is_current is True  # Final acceptance allowed
        assert db_artifact.file_size_bytes == len(valid_mp4_bytes)

        # RuntimeTruth row created and marked PASSED
        truth_result = await db_session.execute(
            select(ProductionRuntimeTruth).where(ProductionRuntimeTruth.artifact_id == artifact.id)
        )
        truth_row = truth_result.scalar_one_or_none()
        assert truth_row is not None
        assert truth_row.payload is not None

        assert qa_row is not None
        assert qa_row.status == ProductionQAStatus.PASSED.value

        # Physical file exists
        physical_path = storage.resolve_artifact_path(channel.id, prepared_req.id, artifact.storage_uri)
        assert physical_path.is_file()
    finally:
        import shutil
        shutil.rmtree(storage_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_case_b_blank_visual_artifact_fails_closed(
    db_session: AsyncSession,
    blank_mp4_bytes: bytes,
) -> None:
    """Case B — Blank/invalid visual artifact:

    render artifact physically exists on disk
    → PhysicalArtifactQAEvaluator returns BLANK_FRAME_DETECTED (BLOCKER/FAIL)
    → downstream final acceptance blocked (media_artifact.is_current = False, qa_status = BLOCKED)
    → artifact remains physically on disk for post-mortem diagnosis.
    """
    seeds = await _seed_canonical_pipeline_data(db_session)
    channel = seeds["channel"]
    script = seeds["script"]
    mission = seeds["mission"]
    mission_execution = seeds["mission_execution"]

    storage_root = Path.cwd() / "scratch" / f"p22d_b_{uuid4().hex[:6]}"
    storage = LocalMediaStorageProvider(base_root=str(storage_root))
    mock_v2_service = AsyncMock()

    render_service = ProductionRenderService(
        storage=storage,
        visual_production_service=mock_v2_service,
    )
    render_service.probe.probe_file = AsyncMock(
        return_value={
            "duration_ms": 200,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "video_codec": "h264",
            "audio_codec": "aac",
            "has_video": True,
        }
    )

    try:
        production_service = ProductionService()
        req = await production_service.create_production_request(
            db_session,
            channel.id,
            ProductionRequestCreate(
                script_version_id=script.id,
                target_width=1920,
                target_height=1080,
                fps=30,
                video_codec="h264",
                audio_codec="aac",
                container_format="mp4",
                mission_execution_id=mission_execution.id,
                metadata={
                    "render_settings": {
                        "visual_asset_mode": "PEXELS",
                        "narration_provider": "NEURAL",
                        "subtitle_mode": "STANDARD",
                    }
                },
            ),
            idempotency_key=f"p22d-case-b-{uuid4().hex}",
        )
        prepared_req = await production_service.prepare_production(
            db_session, channel.id, req.id
        )

        job, plan, is_new = await production_service.allocate_render_job(
            db_session,
            channel.id,
            prepared_req.id,
            idempotency_key=f"p22d-job-b-{uuid4().hex}",
        )
        assert is_new is True

        # Prepare V2 output file with blank solid black frames
        storage_root.mkdir(parents=True, exist_ok=True)
        v2_output = storage_root / "v2_blank.mp4"
        v2_output.write_bytes(blank_mp4_bytes)
        sha = compute_sha256(v2_output)
        fake_result = FakeV2Result(prepared_req, v2_output, sha, mission_id=mission.id)
        mock_v2_service.render_canonical_production.return_value = fake_result

        guardian_engine = MagicMock()
        guardian_engine.execute_check = AsyncMock(
            return_value=MagicMock(
                decision=MagicMock(action=GuardianAction.ALLOW, reason="acceptance passed")
            )
        )

        with patch("omega.application.guardian.engine.GuardianEngine", return_value=guardian_engine):
            artifact, qa_status = await render_service.execute_render_job(
                db_session,
                channel.id,
                prepared_req.id,
                job.id,
            )

        # Assertions for Case B
        assert artifact is not None
        assert qa_status == ProductionQAStatus.BLOCKED

        # DB persistence assertions: artifact physically exists on disk for diagnosis, but NOT accepted
        db_artifact = await db_session.get(MediaArtifact, artifact.id)
        assert db_artifact is not None
        assert db_artifact.is_current is False  # Final acceptance blocked!
        assert db_artifact.file_size_bytes == len(blank_mp4_bytes)

        # Physical file still exists on disk for diagnosis
        physical_path = storage.resolve_artifact_path(channel.id, prepared_req.id, artifact.storage_uri)
        assert physical_path.is_file()
        assert physical_path.stat().st_size == len(blank_mp4_bytes)

        # QA Result row records BLOCKED
        qa_result = await db_session.execute(
            select(ProductionQAResult).where(ProductionQAResult.artifact_id == artifact.id)
        )
        qa_row = qa_result.scalar_one_or_none()
        assert qa_row is not None
        assert qa_row.status == ProductionQAStatus.BLOCKED.value

        # Verify that finding contains BLANK_FRAME_DETECTED
        findings = qa_row.findings or []
        has_blank_finding = any(
            f.get("details", {}).get("visual_qa_code") == "BLANK_FRAME_DETECTED"
            or "blank" in f.get("message", "").lower()
            for f in findings
        )
        assert has_blank_finding is True
    finally:
        import shutil
        shutil.rmtree(storage_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_case_c_corrupt_artifact_fails_closed(
    db_session: AsyncSession,
    corrupt_artifact_bytes: bytes,
) -> None:
    """Case C — Corrupt/invalid artifact:

    render artifact physically exists with corrupted header/payload
    → PhysicalArtifactQAEvaluator returns CORRUPT_ARTIFACT (BLOCKER/FAIL)
    → downstream final acceptance blocked (media_artifact.is_current = False, qa_status = BLOCKED)
    → no successful RuntimeTruth / publish-ready accepted state.
    """
    seeds = await _seed_canonical_pipeline_data(db_session)
    channel = seeds["channel"]
    script = seeds["script"]
    mission = seeds["mission"]
    mission_execution = seeds["mission_execution"]

    storage_root = Path.cwd() / "scratch" / f"p22d_c_{uuid4().hex[:6]}"
    storage = LocalMediaStorageProvider(base_root=str(storage_root))
    mock_v2_service = AsyncMock()

    render_service = ProductionRenderService(
        storage=storage,
        visual_production_service=mock_v2_service,
    )

    try:
        production_service = ProductionService()
        req = await production_service.create_production_request(
            db_session,
            channel.id,
            ProductionRequestCreate(
                script_version_id=script.id,
                target_width=1920,
                target_height=1080,
                fps=30,
                video_codec="h264",
                audio_codec="aac",
                container_format="mp4",
                mission_execution_id=mission_execution.id,
                metadata={
                    "render_settings": {
                        "visual_asset_mode": "PEXELS",
                        "narration_provider": "NEURAL",
                        "subtitle_mode": "STANDARD",
                    }
                },
            ),
            idempotency_key=f"p22d-case-c-{uuid4().hex}",
        )
        prepared_req = await production_service.prepare_production(
            db_session, channel.id, req.id
        )

        job, plan, is_new = await production_service.allocate_render_job(
            db_session,
            channel.id,
            prepared_req.id,
            idempotency_key=f"p22d-job-c-{uuid4().hex}",
        )
        assert is_new is True

        # Prepare V2 output file with corrupted box payload
        storage_root.mkdir(parents=True, exist_ok=True)
        v2_output = storage_root / "v2_corrupt.mp4"
        v2_output.write_bytes(corrupt_artifact_bytes)
        sha = compute_sha256(v2_output)
        fake_result = FakeV2Result(prepared_req, v2_output, sha, mission_id=mission.id)
        mock_v2_service.render_canonical_production.return_value = fake_result

        # Media probe will report no valid video stream for this corrupt file
        render_service.probe.probe_file = AsyncMock(
            return_value={
                "duration_ms": 200,
                "width": 1920,
                "height": 1080,
                "fps": 30.0,
                "video_codec": "h264",
                "audio_codec": "aac",
                "has_video": False,
            }
        )

        guardian_engine = MagicMock()
        guardian_engine.execute_check = AsyncMock(
            return_value=MagicMock(
                decision=MagicMock(action=GuardianAction.ALLOW, reason="acceptance passed")
            )
        )

        with patch("omega.application.guardian.engine.GuardianEngine", return_value=guardian_engine):
            artifact, qa_status = await render_service.execute_render_job(
                db_session,
                channel.id,
                prepared_req.id,
                job.id,
            )

        # Assertions for Case C
        assert artifact is not None
        assert qa_status == ProductionQAStatus.BLOCKED

        # DB persistence assertions: artifact physically exists on disk for diagnosis, but NOT accepted
        db_artifact = await db_session.get(MediaArtifact, artifact.id)
        assert db_artifact is not None
        assert db_artifact.is_current is False  # Final acceptance blocked!
        assert db_artifact.file_size_bytes == len(corrupt_artifact_bytes)

        # Physical file still exists on disk for diagnosis
        physical_path = storage.resolve_artifact_path(channel.id, prepared_req.id, artifact.storage_uri)
        assert physical_path.is_file()
        assert physical_path.stat().st_size == len(corrupt_artifact_bytes)

        # QA Result row records BLOCKED
        qa_result = await db_session.execute(
            select(ProductionQAResult).where(ProductionQAResult.artifact_id == artifact.id)
        )
        qa_row = qa_result.scalar_one_or_none()
        assert qa_row is not None
        assert qa_row.status == ProductionQAStatus.BLOCKED.value

        # Verify that finding contains CORRUPT_ARTIFACT
        findings = qa_row.findings or []
        has_corrupt_finding = any(
            f.get("details", {}).get("visual_qa_code") == "CORRUPT_ARTIFACT"
            or "corrupt" in f.get("message", "").lower()
            for f in findings
        )
        assert has_corrupt_finding is True
    finally:
        import shutil
        shutil.rmtree(storage_root, ignore_errors=True)
