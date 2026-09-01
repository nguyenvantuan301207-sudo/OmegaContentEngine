"""Regression tests for PublishIntent monotonic revision numbering across all lifecycle states.

Validates:
1. First intent for task -> revision_number = 1
2. New intent after FAILED revision #1 -> revision_number = 2
3. New intent after PUBLISHED revision -> next monotonic revision
4. Multiple historical revisions -> max + 1
5. Active DRAFT/APPROVED duplicate protection remains unchanged
6. Active intent metadata modification supersedes and increments revision
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.media_storage import LocalMediaStorageProvider
from omega.domain.channel import ChannelState, Platform
from omega.domain.publisher import (
    PlatformAccountStatus,
    PublishIntentState,
)
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    MediaArtifact,
    Mission,
    PlatformAccount,
    ProductionRequest,
    PublishIntent,
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    Task,
    TopicCandidate,
)
from omega.main import app

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest_asyncio.fixture
async def api_client(db_session: AsyncSession):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def monotonic_rev_fixtures(db_session: AsyncSession, tmp_path):
    storage = LocalMediaStorageProvider(base_root=str(tmp_path))

    channel = Channel(
        id=uuid4(),
        slug=f"monotonic-chan-{uuid4().hex[:8]}",
        name="Monotonic Revision Test Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)

    mission = Mission(
        id=uuid4(),
        channel_id=channel.id,
        title="Monotonic Mission",
        objective="Validate monotonic revision numbers",
        state="RUNNING",
        autonomy_level="SUPERVISED",
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        title="Monotonic Publish Task",
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
        title="Monotonic Topic",
        normalized_title="monotonic topic",
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
    video_content = b"MONOTONIC_TEST_VIDEO_CONTENT_BYTES"
    video_hash = hashlib.sha256(video_content).hexdigest()
    video_file = artifacts_dir / "video_v1.mp4"
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
        account_display_name="Test Monotonic Account",
        external_account_id="UC_TEST_MONO",
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
    }


@pytest.mark.asyncio
async def test_01_first_intent_for_task_is_revision_1(api_client: AsyncClient, monotonic_rev_fixtures):
    """1. The first PublishIntent created for a task has revision_number = 1."""
    f = monotonic_rev_fixtures
    payload = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "First Revision Intent Video",
        "made_for_kids": False,
    }

    resp = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["revision_number"] == 1
    assert data["state"] == "DRAFT"


@pytest.mark.asyncio
async def test_02_new_intent_after_failed_revision_is_revision_2(
    db_session: AsyncSession, api_client: AsyncClient, monotonic_rev_fixtures
):
    """2. A new intent created after a prior intent enters FAILED state has revision_number = 2."""
    f = monotonic_rev_fixtures
    payload = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Failed Revision Video",
        "made_for_kids": False,
    }

    resp1 = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert resp1.status_code == 201
    intent1_id = resp1.json()["id"]

    # Transition revision 1 to FAILED
    intent1 = await db_session.get(PublishIntent, intent1_id)
    assert intent1 is not None
    intent1.state = PublishIntentState.FAILED.value
    await db_session.commit()

    # Create new intent for same task
    resp2 = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert resp2.status_code == 201
    data2 = resp2.json()
    assert data2["id"] != intent1_id
    assert data2["revision_number"] == 2
    assert data2["state"] == "DRAFT"


@pytest.mark.asyncio
async def test_03_new_intent_after_published_revision_is_next_monotonic(
    db_session: AsyncSession, api_client: AsyncClient, monotonic_rev_fixtures
):
    """3. A new intent created after a prior intent is PUBLISHED gets the next monotonic revision."""
    f = monotonic_rev_fixtures
    payload = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Published Intent Video",
        "made_for_kids": False,
    }

    resp1 = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert resp1.status_code == 201
    intent1_id = resp1.json()["id"]

    # Transition revision 1 to PUBLISHED
    intent1 = await db_session.get(PublishIntent, intent1_id)
    assert intent1 is not None
    intent1.state = PublishIntentState.PUBLISHED.value
    await db_session.commit()

    resp2 = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert resp2.status_code == 201
    data2 = resp2.json()
    assert data2["revision_number"] == 2
    assert data2["state"] == "DRAFT"


@pytest.mark.asyncio
async def test_04_multiple_historical_revisions_increments_max_plus_1(
    db_session: AsyncSession, api_client: AsyncClient, monotonic_rev_fixtures
):
    """4. Multiple historical revisions (e.g. rev 1 FAILED, rev 2 FAILED) yields revision_number = 3."""
    f = monotonic_rev_fixtures
    payload = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Multi-Revision Video",
        "made_for_kids": False,
    }

    # Rev 1
    resp1 = await api_client.post("/api/v1/publisher/intents", json=payload)
    intent1 = await db_session.get(PublishIntent, resp1.json()["id"])
    assert intent1 is not None
    intent1.state = PublishIntentState.FAILED.value
    await db_session.commit()

    # Rev 2
    resp2 = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert resp2.json()["revision_number"] == 2
    intent2 = await db_session.get(PublishIntent, resp2.json()["id"])
    assert intent2 is not None
    intent2.state = PublishIntentState.FAILED.value
    await db_session.commit()

    # Rev 3
    resp3 = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert resp3.status_code == 201
    data3 = resp3.json()
    assert data3["revision_number"] == 3
    assert data3["state"] == "DRAFT"


@pytest.mark.asyncio
async def test_05_active_draft_duplicate_returns_existing_intent(
    api_client: AsyncClient, monotonic_rev_fixtures
):
    """5. Re-submitting identical intent payload while intent is in DRAFT returns existing intent."""
    f = monotonic_rev_fixtures
    payload = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Duplicate Check Video",
        "made_for_kids": False,
    }

    resp1 = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert resp1.status_code == 201
    data1 = resp1.json()

    resp2 = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert resp2.status_code in (200, 201)
    data2 = resp2.json()

    assert data1["id"] == data2["id"]
    assert data2["revision_number"] == 1


@pytest.mark.asyncio
async def test_06_metadata_change_on_active_intent_supersedes_and_increments(
    db_session: AsyncSession, api_client: AsyncClient, monotonic_rev_fixtures
):
    """6. Submitting changed metadata while intent is DRAFT supersedes rev 1 and creates rev 2."""
    f = monotonic_rev_fixtures
    payload1 = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Initial Title Video",
        "made_for_kids": False,
    }

    resp1 = await api_client.post("/api/v1/publisher/intents", json=payload1)
    assert resp1.status_code == 201
    intent1_id = resp1.json()["id"]

    payload2 = dict(payload1)
    payload2["title"] = "Updated Title Video"

    resp2 = await api_client.post("/api/v1/publisher/intents", json=payload2)
    assert resp2.status_code == 201
    data2 = resp2.json()

    assert data2["id"] != intent1_id
    assert data2["revision_number"] == 2

    # Check rev 1 is SUPERSEDED
    intent1 = await db_session.get(PublishIntent, intent1_id)
    assert intent1 is not None
    assert intent1.state == PublishIntentState.SUPERSEDED.value
