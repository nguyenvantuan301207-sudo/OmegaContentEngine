"""Integration tests for OMEGA-011 Publisher REST API endpoints and log sanitization."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.channel import ChannelState, Platform
from omega.domain.publisher import (
    PlatformAccountStatus,
    PublishAttemptState,
    PublishIntentState,
)
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    MediaArtifact,
    Mission,
    NetworkProfile,
    NetworkRoute,
    PlatformAccount,
    ProductionRequest,
    PublishAttempt,
    PublishIntent,
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    Task,
    TopicCandidate,
    UploadSession,
)
from omega.main import app

pytestmark = pytest.mark.usefixtures("publisher_test_env")


async def create_artifact_with_ancestry(
    db_session: AsyncSession, channel_id: uuid4, art_hash: str
) -> MediaArtifact:
    """Helper to create full valid ancestry for MediaArtifact fixture."""
    dna_rev = ChannelDNARevision(
        id=uuid4(),
        channel_id=channel_id,
        version=1,
        snapshot={"name": "DNA"},
        change_reason="Initial",
    )
    db_session.add(dna_rev)

    topic = TopicCandidate(
        id=uuid4(),
        channel_id=channel_id,
        title="Test Topic",
        normalized_title="test topic",
        source_name="Manual Entry",
        summary="Test topic summary",
        topic_fingerprint=uuid4().hex,
        status="APPROVED",
    )

    db_session.add(topic)

    r_req = ResearchRequest(
        id=uuid4(),
        channel_id=channel_id,
        topic_candidate_id=topic.id,
        status="COMPLETED",
    )
    db_session.add(r_req)

    brief = ResearchBrief(
        id=uuid4(),
        research_request_id=r_req.id,
        channel_id=channel_id,
        topic_candidate_id=topic.id,
        title="Brief Title",
        summary="Brief summary",
    )
    db_session.add(brief)

    content_req = ContentGenerationRequest(
        id=uuid4(),
        channel_id=channel_id,
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
        title="Script v1",
        hook_text="Hook",
        closing_text="Close",
        cta_text="CTA",
    )
    db_session.add(script_ver)

    prod_req = ProductionRequest(
        id=uuid4(),
        channel_id=channel_id,
        script_version_id=script_ver.id,
        content_request_id=content_req.id,
        channel_dna_revision_id=dna_rev.id,
        status="APPROVED",
    )
    db_session.add(prod_req)

    artifact = MediaArtifact(
        id=uuid4(),
        production_request_id=prod_req.id,
        artifact_type="VIDEO",
        version=1,
        is_current=True,
        storage_uri="videos/api_test.mp4",
        file_size_bytes=4096,
        content_hash=art_hash,
        mime_type="video/mp4",
    )
    db_session.add(artifact)
    await db_session.flush()
    return artifact


@pytest_asyncio.fixture
async def api_client(db_session: AsyncSession):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def api_publisher_fixtures(db_session: AsyncSession):
    now = datetime.now(UTC)
    prof = NetworkProfile(
        id=uuid4(),
        name=f"Profile-{uuid4().hex[:8]}",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(prof)
    await db_session.flush()

    route = NetworkRoute(
        id=uuid4(),
        profile_id=prof.id,
        name="Direct Internet",
        route_type="DIRECT",
        allowed_service_categories=["YOUTUBE_API", "GENERAL_HTTP"],
        tls_verify=True,
        config_version=1,
        config_checksum="chk1",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)

    channel = Channel(
        id=uuid4(),
        slug=f"api-pub-chan-{uuid4().hex[:8]}",
        name="API Pub Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)

    mission = Mission(
        id=uuid4(),
        title="API Mission",
        objective="API publish test",
        state="RUNNING",
        autonomy_level="AUTONOMOUS",
        channel_id=channel.id,
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="PUBLISH_VIDEO",
        title="API Publish Task",
        state="READY",
    )
    db_session.add(task)

    artifact = await create_artifact_with_ancestry(db_session, channel.id, "e" * 64)

    account = PlatformAccount(
        id=uuid4(),
        channel_id=channel.id,
        platform=Platform.YOUTUBE.value,
        account_display_name="API Channel",
        external_account_id="UC_API_123",
        status=PlatformAccountStatus.ACTIVE.value,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    db_session.add(account)

    await db_session.commit()
    return {
        "channel": channel,
        "mission": mission,
        "task": task,
        "artifact": artifact,
        "account": account,
    }


@pytest.mark.asyncio
async def test_publisher_accounts_api(api_client: AsyncClient, api_publisher_fixtures):
    """Verify listing platform accounts hides all secrets."""
    f = api_publisher_fixtures
    resp = await api_client.get(f"/api/v1/publisher/accounts?channel_id={f['channel'].id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["account_display_name"] == "API Channel"
    # Ensure no token fields returned
    assert "access_token" not in data[0]
    assert "refresh_token" not in data[0]
    assert "encrypted_access_token" not in data[0]


@pytest.mark.asyncio
async def test_create_and_get_publish_intent_api(api_client: AsyncClient, api_publisher_fixtures):
    """Verify creating and retrieving PublishIntents via API defaults to DRAFT state."""
    f = api_publisher_fixtures
    payload = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "API Created Intent",
        "description": "API Created Desc",
        "tags": ["api", "test"],
        "requested_privacy_status": "PRIVATE",
        "category_id": "28",
        "made_for_kids": False,
    }

    create_resp = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert create_resp.status_code == 201
    intent_data = create_resp.json()
    assert intent_data["title"] == "API Created Intent"
    assert intent_data["state"] == "DRAFT"
    assert intent_data["revision_number"] == 1

    get_resp = await api_client.get(f"/api/v1/publisher/intents/{intent_data['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == intent_data["id"]
    assert get_resp.json()["state"] == "DRAFT"

    # Test approve intent endpoint
    approve_resp = await api_client.post(f"/api/v1/publisher/intents/{intent_data['id']}/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["state"] == "APPROVED"


@pytest.mark.asyncio
async def test_draft_intent_blocks_execution_until_approved(
    api_client: AsyncClient, api_publisher_fixtures
):
    """Verify execution is refused while intent is DRAFT, and allowed once approved."""
    f = api_publisher_fixtures
    payload = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Gated Execution Intent",
        "description": "Gated Desc",
        "made_for_kids": False,
    }

    create_resp = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert create_resp.status_code == 201
    intent_id = create_resp.json()["id"]
    assert create_resp.json()["state"] == "DRAFT"

    # Execution should fail with 400 because intent is in DRAFT
    exec_resp = await api_client.post("/api/v1/publisher/execute", json={"task_id": str(f["task"].id)})
    assert exec_resp.status_code == 400
    assert "approved" in exec_resp.json()["detail"].lower()

    # Now approve the intent
    approve_resp = await api_client.post(f"/api/v1/publisher/intents/{intent_id}/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["state"] == "APPROVED"


@pytest.mark.asyncio
async def test_upload_progress_api_does_not_leak_session_uri(
    api_client: AsyncClient, db_session: AsyncSession, api_publisher_fixtures
):
    """Verify upload progress endpoint returns safe progress data and never leaks full session URIs."""
    f = api_publisher_fixtures

    intent = PublishIntent(
        id=uuid4(),
        mission_id=f["mission"].id,
        task_id=f["task"].id,
        channel_id=f["channel"].id,
        platform_account_id=f["account"].id,
        media_artifact_id=f["artifact"].id,
        media_artifact_checksum=f["artifact"].content_hash,
        title="Prog Test",
        description="Desc",
        requested_privacy_status="PRIVATE",
        made_for_kids=False,
        intent_checksum="c" * 64,
        state=PublishIntentState.CLAIMED.value,
    )
    db_session.add(intent)

    attempt = PublishAttempt(
        id=uuid4(),
        publish_intent_id=intent.id,
        attempt_number=1,
        idempotency_key=f"idemp_prog_{uuid4().hex}",
        state=PublishAttemptState.UPLOADING.value,
    )

    db_session.add(attempt)

    session_row = UploadSession(
        id=uuid4(),
        publish_attempt_id=attempt.id,
        session_uri="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=SUPER_SECRET_TOKEN_999",
        total_bytes=1000,
        bytes_uploaded=400,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(session_row)
    await db_session.commit()

    resp = await api_client.get(f"/api/v1/publisher/attempts/{attempt.id}/progress")
    assert resp.status_code == 200
    data = resp.json()
    assert data["bytes_uploaded"] == 400
    assert data["total_bytes"] == 1000
    assert data["progress_percentage"] == 40.0
    assert data["is_complete"] is False
    # CRITICAL: session_uri must NOT be present
    assert "session_uri" not in data
    assert "SUPER_SECRET_TOKEN_999" not in str(data)
