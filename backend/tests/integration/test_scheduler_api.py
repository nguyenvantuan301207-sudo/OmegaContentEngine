"""Integration tests for OMEGA-010 Scheduler REST API endpoints.

Tests policy CRUD/activation, evaluate endpoint, reservation queries,
manual release with audit, and channel schedule timeline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.scheduler import SchedulePolicyService
from omega.domain.scheduler import ReservationState
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    Mission,
    ScheduleDecision,
    SchedulePolicy,
    SchedulerDispatchOutbox,
    ScheduleReservation,
    ScheduleStateTransition,
    Task,
)
from omega.main import create_app


@pytest_asyncio.fixture(autouse=True)
async def clean_scheduler_db(db_session: AsyncSession):
    """Clean up scheduler tables before each test."""
    await db_session.execute(delete(SchedulerDispatchOutbox))
    await db_session.execute(delete(ScheduleStateTransition))
    await db_session.execute(delete(ScheduleReservation))
    await db_session.execute(delete(ScheduleDecision))
    await db_session.execute(delete(SchedulePolicy))
    await db_session.commit()


@pytest_asyncio.fixture
async def api_client():
    """Async HTTP test client for FastAPI application."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_scheduler_policy_api_lifecycle(api_client: AsyncClient):
    """Test policy create, list, and activate endpoints."""
    # 1. Create DRAFT policy
    payload = {
        "workload_category": "EXTERNAL_PUBLISH",
        "version": f"1.0.0-{uuid4().hex[:6]}",
        "policy_config": {
            "min_lead_time_seconds": 0,
            "max_schedule_horizon_days": 14,
            "min_gap_between_channel_items_seconds": 3600,
            "max_scheduled_items_per_day": 5,
            "global_concurrency_limit": 10,
            "channel_concurrency_limit": 2,
        },
        "activate": False,
    }
    resp = await api_client.post("/api/v1/scheduler/policies", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "DRAFT"
    policy_id = data["id"]

    # 2. List policies
    list_resp = await api_client.get(
        "/api/v1/scheduler/policies?workload_category=EXTERNAL_PUBLISH"
    )
    assert list_resp.status_code == 200
    policies = list_resp.json()
    assert any(p["id"] == policy_id for p in policies)

    # 3. Activate policy
    act_resp = await api_client.post(f"/api/v1/scheduler/policies/{policy_id}/activate")
    assert act_resp.status_code == 200
    assert act_resp.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_scheduler_evaluate_and_decision_api(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Test POST /api/v1/scheduler/evaluate and GET /api/v1/scheduler/decisions/{id}."""
    # Create active policy
    _policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category="EXTERNAL_PUBLISH",
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config={
            "min_lead_time_seconds": 0,
            "max_schedule_horizon_days": 14,
            "min_gap_between_channel_items_seconds": 3600,
            "max_scheduled_items_per_day": 5,
            "global_concurrency_limit": 10,
            "channel_concurrency_limit": 2,
        },
        activate=True,
    )

    channel = Channel(
        id=uuid4(), slug=f"chan-{uuid4().hex[:6]}", name="API Channel", state="ACTIVE"
    )
    db_session.add(channel)
    mission = Mission(
        id=uuid4(),
        channel_id=channel.id,
        title="API Mission",
        objective="API Test",
        state="RUNNING",
        priority=1,
    )
    db_session.add(mission)
    task = Task(
        id=uuid4(),
        mission_id=mission.id,
        task_type="publish_video",
        title="Publish Task",
        state="READY",
    )
    db_session.add(task)
    await db_session.commit()

    now = datetime.now(UTC)
    eval_payload = {
        "mission_id": str(mission.id),
        "task_id": str(task.id),
        "target_type": "TASK_EXECUTION",
        "target_id": str(task.id),
        "workload_category": "EXTERNAL_PUBLISH",
        "channel_id": str(channel.id),
        "earliest_start_at": now.isoformat(),
        "priority": 1,
        "caller_key": "api-eval-1",
    }

    # Evaluate schedule
    eval_resp = await api_client.post("/api/v1/scheduler/evaluate", json=eval_payload)
    assert eval_resp.status_code == 200
    decision_data = eval_resp.json()
    assert decision_data["action"] == "SCHEDULE"
    decision_id = decision_data["id"]

    # Get decision by ID
    get_resp = await api_client.get(f"/api/v1/scheduler/decisions/{decision_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == decision_id


@pytest.mark.asyncio
async def test_scheduler_reservations_and_release_api(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Test reservation query, manual release, and release rejection on consumed slots."""
    policy = await SchedulePolicyService.create_policy(
        db_session,
        workload_category="EXTERNAL_PUBLISH",
        version=f"1.0.0-{uuid4().hex[:6]}",
        policy_config={
            "min_lead_time_seconds": 0,
            "max_schedule_horizon_days": 14,
            "global_concurrency_limit": 10,
        },
        activate=True,
    )
    mission = Mission(
        id=uuid4(), title="Res Mission", objective="Reservation Test", state="RUNNING", priority=1
    )
    db_session.add(mission)

    dec = ScheduleDecision(
        id=uuid4(),
        mission_id=mission.id,
        target_type="TASK_EXECUTION",
        target_id=uuid4(),
        workload_category="EXTERNAL_PUBLISH",
        action="SCHEDULE",
        reason="Test",
        policy_id=policy.id,
        policy_version=policy.version,
        policy_checksum=policy.checksum,
        guardian_epoch=1,
        idempotency_key=f"api-res-{uuid4().hex}",
        evaluated_at=datetime.now(UTC),
    )
    db_session.add(dec)

    res = ScheduleReservation(
        id=uuid4(),
        decision_id=dec.id,
        mission_id=mission.id,
        target_type="TASK_EXECUTION",
        target_id=dec.target_id,
        workload_category="EXTERNAL_PUBLISH",
        scheduled_start_at=datetime.now(UTC) + timedelta(hours=1),
        scheduled_end_at=datetime.now(UTC) + timedelta(hours=2),
        state=ReservationState.ACTIVE.value,
        policy_id=policy.id,
        policy_version=policy.version,
        policy_checksum=policy.checksum,
        guardian_epoch=1,
    )
    db_session.add(res)
    await db_session.commit()

    # 1. List reservations
    list_resp = await api_client.get("/api/v1/scheduler/reservations?state=ACTIVE")
    assert list_resp.status_code == 200
    assert any(r["id"] == str(res.id) for r in list_resp.json())

    # 2. Get reservation by ID
    get_resp = await api_client.get(f"/api/v1/scheduler/reservations/{str(res.id)}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == str(res.id)

    # 3. Release reservation
    rel_resp = await api_client.post(
        f"/api/v1/scheduler/reservations/{str(res.id)}/release?reason=Operator%20request"
    )
    assert rel_resp.status_code == 200
    assert rel_resp.json()["state"] == "RELEASED"

    # 4. Re-releasing RELEASED reservation should fail with 400
    rel_again = await api_client.post(
        f"/api/v1/scheduler/reservations/{str(res.id)}/release?reason=Second%20release"
    )
    assert rel_again.status_code == 400


@pytest.mark.asyncio
async def test_scheduler_channel_timeline_api(api_client: AsyncClient, db_session: AsyncSession):
    """Test GET /api/v1/scheduler/channels/{channel_id}/timeline endpoint."""
    channel = Channel(
        id=uuid4(), slug=f"chan-{uuid4().hex[:6]}", name="Timeline Channel", state="ACTIVE"
    )
    db_session.add(channel)

    dna = ChannelDNARevision(
        id=uuid4(),
        channel_id=channel.id,
        version=1,
        snapshot={"publishing_preferences": {"target_timezone": "America/New_York"}},
        change_reason="Setup",
    )
    db_session.add(dna)
    await db_session.commit()

    resp = await api_client.get(f"/api/v1/scheduler/channels/{str(channel.id)}/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert data["channel_id"] == str(channel.id)
    assert data["timezone"] == "America/New_York"
    assert "capacity_used_today" in data
    assert "capacity_limit_today" in data
    assert isinstance(data["reservations"], list)
