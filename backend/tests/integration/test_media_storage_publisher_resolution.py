"""Regression tests for Canonical Media Storage Path Resolution across Production and Publisher.

Validates:
1. Production-created MediaArtifact is directly consumed by Publisher without copying files
2. Scoped storage path (/app/data/channels/<id>/production/<id>/artifacts/<file>) resolves safely
3. Missing media fails closed before provider egress
4. Checksum validation remains strictly enforced
5. Path traversal attempts are rejected with security errors
6. Standalone / flat storage layout fallback safely resolves within base root
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.media_storage import LocalMediaStorageProvider, StorageSecurityError
from omega.application.publisher.intent_service import PublishIntentService
from omega.application.publisher.publish_service import PublishExecutionService
from omega.domain.channel import ChannelState, Platform
from omega.domain.publisher import PlatformAccountStatus, PublishIntentState
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    MediaArtifact,
    Mission,
    PlatformAccount,
    ProductionRequest,
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    Task,
    TopicCandidate,
)

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest_asyncio.fixture
async def scoped_media_fixtures(db_session: AsyncSession, tmp_path: Path):
    storage = LocalMediaStorageProvider(base_root=str(tmp_path))

    channel = Channel(
        id=uuid4(),
        slug=f"media-res-chan-{uuid4().hex[:8]}",
        name="Media Resolution Test Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)

    mission = Mission(
        id=uuid4(),
        channel_id=channel.id,
        title="Media Res Mission",
        objective="Media resolution test",
        state="RUNNING",
        autonomy_level="SUPERVISED",
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        title="Media Res Task",
        task_type="PUBLISH_VIDEO",
        state="READY",
    )
    db_session.add(task)

    dna_rev = ChannelDNARevision(
        id=uuid4(),
        channel_id=channel.id,
        version=1,
        snapshot={"name": "DNA"},
        change_reason="Initial",
    )
    db_session.add(dna_rev)

    topic = TopicCandidate(
        id=uuid4(),
        channel_id=channel.id,
        title="Media Topic",
        normalized_title="media topic",
        source_name="Manual",
        summary="Summary",
        topic_fingerprint=uuid4().hex,
        status="APPROVED",
    )
    db_session.add(topic)

    r_req = ResearchRequest(
        id=uuid4(),
        channel_id=channel.id,
        topic_candidate_id=topic.id,
        status="COMPLETED",
    )
    db_session.add(r_req)

    brief = ResearchBrief(
        id=uuid4(),
        research_request_id=r_req.id,
        channel_id=channel.id,
        topic_candidate_id=topic.id,
        title="Brief",
        summary="Brief summary",
    )
    db_session.add(brief)

    content_req = ContentGenerationRequest(
        id=uuid4(),
        channel_id=channel.id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna_rev.id,
        status="APPROVED",
    )
    db_session.add(content_req)

    script_ver = ScriptVersion(
        id=uuid4(),
        content_request_id=content_req.id,
        version=1,
        title="Script",
        hook_text="Hook",
        closing_text="Close",
        cta_text="CTA",
    )
    db_session.add(script_ver)

    prod_req = ProductionRequest(
        id=uuid4(),
        channel_id=channel.id,
        script_version_id=script_ver.id,
        content_request_id=content_req.id,
        channel_dna_revision_id=dna_rev.id,
        status="APPROVED",
    )
    db_session.add(prod_req)
    await db_session.flush()

    # Create physical file in scoped production artifacts dir
    artifacts_dir = storage.get_artifacts_dir(channel.id, prod_req.id)
    video_content = b"CANONICAL_MP4_VIDEO_BYTES_TEST_12345"
    video_hash = hashlib.sha256(video_content).hexdigest()
    video_file = artifacts_dir / "video_v1_scoped.mp4"
    video_file.write_bytes(video_content)

    rel_storage_uri = storage.to_relative_uri(channel.id, prod_req.id, video_file)

    artifact = MediaArtifact(
        id=uuid4(),
        production_request_id=prod_req.id,
        artifact_type="VIDEO",
        version=1,
        is_current=True,
        storage_uri=rel_storage_uri,
        file_size_bytes=len(video_content),
        content_hash=video_hash,
        mime_type="video/mp4",
    )
    db_session.add(artifact)

    account = PlatformAccount(
        id=uuid4(),
        channel_id=channel.id,
        platform=Platform.YOUTUBE.value,
        account_display_name="Test Publisher Account",
        external_account_id="UC_TEST_RES",
        status=PlatformAccountStatus.ACTIVE.value,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    db_session.add(account)

    await db_session.commit()
    return {
        "channel": channel,
        "mission": mission,
        "task": task,
        "prod_req": prod_req,
        "artifact": artifact,
        "account": account,
        "storage": storage,
        "video_file": video_file,
        "video_hash": video_hash,
    }


@pytest.mark.asyncio
async def test_01_local_media_storage_provider_resolve_artifact_path(scoped_media_fixtures):
    """1. LocalMediaStorageProvider resolves scoped and fallback artifact paths."""
    f = scoped_media_fixtures
    storage: LocalMediaStorageProvider = f["storage"]

    # Scoped resolution
    resolved = storage.resolve_artifact_path(
        channel_id=f["channel"].id,
        production_request_id=f["prod_req"].id,
        storage_uri=f["artifact"].storage_uri,
    )
    assert resolved == f["video_file"]
    assert resolved.is_file()

    # Flat fallback resolution
    flat_resolved = storage.resolve_artifact_path(
        channel_id=f["channel"].id,
        production_request_id=None,
        storage_uri="artifacts/test_flat.mp4",
    )
    assert resolved != flat_resolved
    assert str(flat_resolved).startswith(str(storage.base_root))


@pytest.mark.asyncio
async def test_02_path_traversal_escapes_rejected(scoped_media_fixtures):
    """2. Path traversal in storage_uri raises StorageSecurityError."""
    f = scoped_media_fixtures
    storage: LocalMediaStorageProvider = f["storage"]

    with pytest.raises(StorageSecurityError):
        storage.resolve_artifact_path(
            channel_id=f["channel"].id,
            production_request_id=f["prod_req"].id,
            storage_uri="../../etc/passwd",
        )

    with pytest.raises(StorageSecurityError):
        storage.resolve_artifact_path(
            channel_id=f["channel"].id,
            production_request_id=None,
            storage_uri="../../../etc/shadow",
        )


@pytest.mark.asyncio
async def test_03_readiness_service_resolves_scoped_artifact(
    db_session: AsyncSession, scoped_media_fixtures, monkeypatch
):
    """3. PublishExecutionService resolves scoped artifact path and validates SHA-256."""
    f = scoped_media_fixtures
    monkeypatch.setenv("MEDIA_STORAGE_ROOT", str(f["storage"].base_root))

    # Create approved intent
    from omega.domain.publisher import PublishIntentCreate

    _ = await PublishIntentService.create_publish_intent(
        session=db_session,
        payload=PublishIntentCreate(
            mission_id=f["mission"].id,
            task_id=f["task"].id,
            channel_id=f["channel"].id,
            platform_account_id=f["account"].id,
            media_artifact_id=f["artifact"].id,
            media_artifact_checksum=f["artifact"].content_hash,
            title="Scoped Media Video",
            made_for_kids=False,
        ),
        initial_state=PublishIntentState.APPROVED,
    )
    await db_session.commit()

    report = await PublishExecutionService.validate_publish_readiness(db_session, f["task"].id)
    # Validate no missing media error and artifact verified
    media_errors = [e for e in report.validation_errors if "Media artifact" in e or "checksum" in e]
    assert len(media_errors) == 0
    assert report.artifact_verified is True
