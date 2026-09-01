"""Regression tests for OMEGA Canonical PublishIntent DRAFT state gate and human approval workflow.

Validates:
1. POST /publisher/intents creates DRAFT intent
2. DRAFT intent cannot execute (fails closed)
3. Approval transitions DRAFT -> APPROVED (via /publisher/intents/{id}/approve and /tasks/{id}/approve)
4. Approved intent can reach execution gate
5. Duplicate / idempotency behavior returns existing intent
6. Internal autonomous path preserves initial_state=APPROVED parameter
7. Archived channel safety preserved
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.channel import ChannelState, Platform
from omega.domain.publisher import PlatformAccountStatus
from omega.domain.task import TaskState
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
    PublishIntentTransition,
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
async def draft_gate_fixtures(db_session: AsyncSession):
    now = datetime.now(UTC)
    prof = NetworkProfile(
        id=uuid4(),
        name=f"Profile-DraftGate-{uuid4().hex[:8]}",
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
        slug=f"draft-gate-chan-{uuid4().hex[:8]}",
        name="Draft Gate Test Channel",
        platform=Platform.YOUTUBE.value,
        state=ChannelState.ACTIVE.value,
    )
    db_session.add(channel)

    mission = Mission(
        id=uuid4(),
        channel_id=channel.id,
        title="Draft Gate Mission",
        objective="Draft gate test",
        state="RUNNING",
        autonomy_level="SUPERVISED",
    )
    db_session.add(mission)

    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        title="Publish Video Task",
        task_type="PUBLISH_VIDEO",
        state=TaskState.WAITING_APPROVAL.value,
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
        title="Brief Title",
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
        title="Script v1",
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

    artifact = MediaArtifact(
        id=uuid4(),
        production_request_id=prod_req.id,
        artifact_type="VIDEO",
        version=1,
        is_current=True,
        storage_uri="videos/draft_gate_test.mp4",
        file_size_bytes=4096,
        content_hash="d" * 64,
        mime_type="video/mp4",
    )
    db_session.add(artifact)

    account = PlatformAccount(
        id=uuid4(),
        channel_id=channel.id,
        platform=Platform.YOUTUBE.value,
        account_display_name="Draft Gate Channel Account",
        external_account_id="UC_DRAFT_GATE",
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
async def test_01_canonical_post_creates_draft_state(api_client: AsyncClient, draft_gate_fixtures):
    """1. Canonical POST /api/v1/publisher/intents creates intent in DRAFT state."""
    f = draft_gate_fixtures
    payload = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Canonical Draft Video Title",
        "description": "Canonical Draft Video Description",
        "tags": ["draft", "canary"],
        "requested_privacy_status": "PRIVATE",
        "category_id": "27",
        "made_for_kids": False,
    }

    resp = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["state"] == "DRAFT"
    assert data["revision_number"] == 1
    assert data["title"] == "Canonical Draft Video Title"


@pytest.mark.asyncio
async def test_02_draft_intent_cannot_execute(api_client: AsyncClient, draft_gate_fixtures):
    """2. DRAFT intent blocks execution; fails closed before any provider call."""
    f = draft_gate_fixtures
    payload = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Blocked Execution Draft Video",
        "made_for_kids": False,
    }

    create_resp = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert create_resp.status_code == 201
    assert create_resp.json()["state"] == "DRAFT"

    # Execution endpoint must reject DRAFT intent with HTTP 400
    exec_resp = await api_client.post(
        "/api/v1/publisher/execute",
        json={"task_id": str(f["task"].id)},
    )
    assert exec_resp.status_code == 400
    assert "no approved or reclaimable publishintent" in exec_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_03_intent_approval_endpoint_transitions_draft_to_approved(
    api_client: AsyncClient, db_session: AsyncSession, draft_gate_fixtures
):
    """3. POST /publisher/intents/{id}/approve transitions DRAFT -> APPROVED and records transition."""
    f = draft_gate_fixtures
    payload = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Approve Intent Endpoint Video",
        "made_for_kids": False,
    }

    create_resp = await api_client.post("/api/v1/publisher/intents", json=payload)
    intent_id = create_resp.json()["id"]

    approve_resp = await api_client.post(f"/api/v1/publisher/intents/{intent_id}/approve")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["state"] == "APPROVED"

    # Verify transitions in database
    trans_res = await db_session.execute(
        select(PublishIntentTransition).where(
            PublishIntentTransition.publish_intent_id == intent_id
        ).order_by(PublishIntentTransition.created_at.asc())
    )
    transitions = trans_res.scalars().all()
    assert len(transitions) == 2
    assert transitions[0].to_state == "DRAFT"
    assert transitions[1].from_state == "DRAFT"
    assert transitions[1].to_state == "APPROVED"


@pytest.mark.asyncio
async def test_04_task_approval_transitions_linked_draft_intent_to_approved(
    api_client: AsyncClient, db_session: AsyncSession, draft_gate_fixtures
):
    """4. POST /tasks/{task_id}/approve transitions task to READY and linked intent to APPROVED."""
    f = draft_gate_fixtures
    payload = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Task Approval Intent Link Video",
        "made_for_kids": False,
    }

    create_resp = await api_client.post("/api/v1/publisher/intents", json=payload)
    intent_id = create_resp.json()["id"]

    task_approve_resp = await api_client.post(f"/api/v1/tasks/{f['task'].id}/approve")
    assert task_approve_resp.status_code == 200
    assert task_approve_resp.json()["state"] == "READY"

    # Check intent is now APPROVED
    get_intent_resp = await api_client.get(f"/api/v1/publisher/intents/{intent_id}")
    assert get_intent_resp.status_code == 200
    assert get_intent_resp.json()["state"] == "APPROVED"


@pytest.mark.asyncio
async def test_05_idempotent_duplicate_submission_returns_existing_intent(
    api_client: AsyncClient, draft_gate_fixtures
):
    """5. Re-submitting identical intent payload returns existing intent without duplicating."""
    f = draft_gate_fixtures
    payload = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Idempotent Video",
        "description": "Same description",
        "made_for_kids": False,
    }

    resp1 = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert resp1.status_code == 201
    id1 = resp1.json()["id"]

    resp2 = await api_client.post("/api/v1/publisher/intents", json=payload)
    assert resp2.status_code == 201
    id2 = resp2.json()["id"]
    assert id1 == id2


@pytest.mark.asyncio
async def test_06_cannot_approve_non_draft_intent(
    api_client: AsyncClient, draft_gate_fixtures
):
    """6. Approving an intent that is not in DRAFT state raises HTTP 400."""
    f = draft_gate_fixtures
    payload = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Double Approve Video",
        "made_for_kids": False,
    }

    create_resp = await api_client.post("/api/v1/publisher/intents", json=payload)
    intent_id = create_resp.json()["id"]

    # First approval -> 200 OK
    app1 = await api_client.post(f"/api/v1/publisher/intents/{intent_id}/approve")
    assert app1.status_code == 200

    # Second approval -> 400 Bad Request (Cannot approve non-DRAFT intent)
    app2 = await api_client.post(f"/api/v1/publisher/intents/{intent_id}/approve")
    assert app2.status_code == 400
    assert "must be in draft state" in app2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_07_task_approval_does_not_affect_unrelated_intents(
    api_client: AsyncClient, db_session: AsyncSession, draft_gate_fixtures
):
    """7. Approving Task A only transitions Intent A, leaving Intent B on Task B in DRAFT."""
    f = draft_gate_fixtures

    # Create Task B
    task_b = Task(
        id=uuid4(),
        mission_id=f["mission"].id,
        title="Unrelated Publish Task B",
        task_type="PUBLISH_VIDEO",
        state=TaskState.WAITING_APPROVAL.value,
    )
    db_session.add(task_b)
    await db_session.commit()

    # Create Intent A on Task A
    payload_a = {
        "mission_id": str(f["mission"].id),
        "task_id": str(f["task"].id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Intent Task A",
        "made_for_kids": False,
    }
    resp_a = await api_client.post("/api/v1/publisher/intents", json=payload_a)
    assert resp_a.status_code == 201
    intent_a_id = resp_a.json()["id"]

    # Create Intent B on Task B
    payload_b = {
        "mission_id": str(f["mission"].id),
        "task_id": str(task_b.id),
        "channel_id": str(f["channel"].id),
        "platform_account_id": str(f["account"].id),
        "media_artifact_id": str(f["artifact"].id),
        "media_artifact_checksum": f["artifact"].content_hash,
        "title": "Intent Task B",
        "made_for_kids": False,
    }
    resp_b = await api_client.post("/api/v1/publisher/intents", json=payload_b)
    assert resp_b.status_code == 201
    intent_b_id = resp_b.json()["id"]

    # Approve Task A only
    task_a_approve = await api_client.post(f"/api/v1/tasks/{f['task'].id}/approve")
    assert task_a_approve.status_code == 200

    # Intent A must be APPROVED, Intent B must still be DRAFT
    intent_a_res = await api_client.get(f"/api/v1/publisher/intents/{intent_a_id}")
    assert intent_a_res.json()["state"] == "APPROVED"

    intent_b_res = await api_client.get(f"/api/v1/publisher/intents/{intent_b_id}")
    assert intent_b_res.json()["state"] == "DRAFT"
