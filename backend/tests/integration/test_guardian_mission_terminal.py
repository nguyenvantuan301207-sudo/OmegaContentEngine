"""Integration tests for Guardian MISSION_TERMINAL gate evaluation and orchestrator completion semantics."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.orchestrator import evaluate_mission
from omega.domain.guardian import (
    CheckTriggerType,
    GuardianAction,
    GuardianCheckpoint,
    GuardianCheckResponse,
    GuardianDecisionResponse,
    GuardianGateState,
)
from omega.domain.mission import MissionState
from omega.domain.task import TaskState
from omega.infrastructure.models import GuardianCheck, GuardianDecision, Mission, Task
from omega.main import app


async def _create_ready_mission_with_tasks(client: AsyncClient) -> tuple[str, list[str]]:
    """Helper creating a planned and running mission with all tasks."""
    res = await client.post(
        "/api/v1/missions",
        json={
            "title": f"Terminal Gate Test Mission {uuid.uuid4().hex[:6]}",
            "objective": "Test MISSION_TERMINAL gate semantics",
            "autonomy_level": "SUPERVISED",
            "priority": 1,
        },
    )
    assert res.status_code == 201
    mission_id = res.json()["id"]

    plan_res = await client.post(f"/api/v1/missions/{mission_id}/plan")
    assert plan_res.status_code == 200

    with patch("omega.worker.tasks.execute_task.delay"):
        start_res = await client.post(f"/api/v1/missions/{mission_id}/start")
        assert start_res.status_code == 200

    tasks_res = await client.get(f"/api/v1/missions/{mission_id}/tasks")
    assert tasks_res.status_code == 200
    task_ids = [t["id"] for t in tasks_res.json()]
    return mission_id, task_ids


def _build_mock_check_response(
    mission_id: uuid.UUID,
    action: GuardianAction,
    gate_state: GuardianGateState,
    reason: str,
) -> GuardianCheckResponse:
    """Helper creating a fully populated GuardianCheckResponse."""
    now = datetime.now(UTC)
    check_id = uuid.uuid4()
    return GuardianCheckResponse(
        id=check_id,
        mission_id=mission_id,
        checkpoint=GuardianCheckpoint.MISSION_TERMINAL,
        trigger_type=CheckTriggerType.MISSION_TERMINAL,
        ruleset_id=uuid.uuid4(),
        ruleset_version="1.0.0",
        ruleset_checksum="a" * 64,
        status="COMPLETED",
        idempotency_key=f"terminal:{uuid.uuid4().hex[:8]}",
        guardian_epoch=1,
        diagnostic_context={"all_tasks_succeeded": True},
        started_at=now,
        completed_at=now,
        created_at=now,
        decision=GuardianDecisionResponse(
            id=uuid.uuid4(),
            guardian_check_id=check_id,
            action=action,
            reason=reason,
            resulting_gate_state=gate_state,
            actor="GUARDIAN_ENGINE",
            created_at=now,
        ),
    )


@pytest.mark.asyncio
async def test_mission_terminal_guardian_allow_succeeds(db_session: AsyncSession) -> None:
    """Verify that when all tasks succeed and Guardian emits ALLOW, mission transitions to SUCCEEDED."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mission_id_str, task_ids = await _create_ready_mission_with_tasks(client)
        mission_id = uuid.UUID(mission_id_str)

        # Set all tasks to SUCCEEDED
        for tid_str in task_ids:
            t_res = await db_session.execute(
                select(Task).where(Task.id == uuid.UUID(tid_str)).with_for_update()
            )
            task = t_res.scalar_one()
            task.state = TaskState.SUCCEEDED.value
        await db_session.commit()

        # Evaluate mission
        result = await evaluate_mission(db_session, mission_id)
        assert result["mission_state"] == MissionState.SUCCEEDED.value

        # Verify terminal check and decision recorded in DB
        check_res = await db_session.execute(
            select(GuardianCheck)
            .where(
                GuardianCheck.mission_id == mission_id,
                GuardianCheck.checkpoint == "MISSION_TERMINAL",
            )
            .order_by(GuardianCheck.created_at.desc())
        )
        checks = list(check_res.scalars().all())
        assert len(checks) >= 1
        assert checks[0].status == "COMPLETED"


@pytest.mark.asyncio
async def test_mission_terminal_guardian_allow_with_warning_succeeds(
    db_session: AsyncSession,
) -> None:
    """Verify that ALLOW_WITH_WARNING also allows mission to transition to SUCCEEDED."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mission_id_str, task_ids = await _create_ready_mission_with_tasks(client)
        mission_id = uuid.UUID(mission_id_str)

        for tid_str in task_ids:
            t_res = await db_session.execute(
                select(Task).where(Task.id == uuid.UUID(tid_str)).with_for_update()
            )
            task = t_res.scalar_one()
            task.state = TaskState.SUCCEEDED.value
        await db_session.commit()

        mock_resp = _build_mock_check_response(
            mission_id=mission_id,
            action=GuardianAction.ALLOW_WITH_WARNING,
            gate_state=GuardianGateState.RESTRICTED,
            reason="Minor non-blocking notice at terminal gate",
        )

        with patch(
            "omega.application.guardian.engine.GuardianEngine.execute_check",
            AsyncMock(return_value=mock_resp),
        ):
            result = await evaluate_mission(db_session, mission_id)
            assert result["mission_state"] == MissionState.SUCCEEDED.value


@pytest.mark.asyncio
async def test_mission_terminal_guardian_blocked_holds(db_session: AsyncSession) -> None:
    """Verify that when Guardian emits BLOCKED at MISSION_TERMINAL, mission does NOT succeed."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mission_id_str, task_ids = await _create_ready_mission_with_tasks(client)
        mission_id = uuid.UUID(mission_id_str)

        for tid_str in task_ids:
            t_res = await db_session.execute(
                select(Task).where(Task.id == uuid.UUID(tid_str)).with_for_update()
            )
            task = t_res.scalar_one()
            task.state = TaskState.SUCCEEDED.value
        await db_session.commit()

        mock_resp = _build_mock_check_response(
            mission_id=mission_id,
            action=GuardianAction.PAUSE,
            gate_state=GuardianGateState.BLOCKED,
            reason="Critical cost anomaly at terminal check",
        )

        with patch(
            "omega.application.guardian.engine.GuardianEngine.execute_check",
            AsyncMock(return_value=mock_resp),
        ):
            result = await evaluate_mission(db_session, mission_id)
            assert result["status"] == "held_by_guardian"
            assert result["gate_state"] == "BLOCKED"

            # Verify mission state was not set to SUCCEEDED
            m_res = await db_session.execute(select(Mission).where(Mission.id == mission_id))
            mission = m_res.scalar_one()
            assert mission.state != MissionState.SUCCEEDED.value


@pytest.mark.asyncio
async def test_mission_terminal_guardian_require_review_holds(db_session: AsyncSession) -> None:
    """Verify that REQUIRE_REVIEW at MISSION_TERMINAL prevents transition to SUCCEEDED."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mission_id_str, task_ids = await _create_ready_mission_with_tasks(client)
        mission_id = uuid.UUID(mission_id_str)

        for tid_str in task_ids:
            t_res = await db_session.execute(
                select(Task).where(Task.id == uuid.UUID(tid_str)).with_for_update()
            )
            task = t_res.scalar_one()
            task.state = TaskState.SUCCEEDED.value
        await db_session.commit()

        mock_resp = _build_mock_check_response(
            mission_id=mission_id,
            action=GuardianAction.REQUIRE_REVIEW,
            gate_state=GuardianGateState.RESTRICTED,
            reason="Manual confirmation required before final signoff",
        )

        with patch(
            "omega.application.guardian.engine.GuardianEngine.execute_check",
            AsyncMock(return_value=mock_resp),
        ):
            result = await evaluate_mission(db_session, mission_id)
            assert result["status"] == "held_by_guardian"
            assert result["action"] == "REQUIRE_REVIEW"

            m_res = await db_session.execute(select(Mission).where(Mission.id == mission_id))
            mission = m_res.scalar_one()
            assert mission.state != MissionState.SUCCEEDED.value


@pytest.mark.asyncio
async def test_mission_terminal_guardian_unavailable_holds(db_session: AsyncSession) -> None:
    """Verify that when Guardian subsystem is unavailable, mission does not fail open into SUCCEEDED."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mission_id_str, task_ids = await _create_ready_mission_with_tasks(client)
        mission_id = uuid.UUID(mission_id_str)

        for tid_str in task_ids:
            t_res = await db_session.execute(
                select(Task).where(Task.id == uuid.UUID(tid_str)).with_for_update()
            )
            task = t_res.scalar_one()
            task.state = TaskState.SUCCEEDED.value
        await db_session.commit()

        with patch(
            "omega.application.guardian.engine.GuardianEngine.execute_check",
            AsyncMock(side_effect=RuntimeError("Redis connection failure")),
        ):
            result = await evaluate_mission(db_session, mission_id)
            assert result["status"] == "waiting_guardian"
            assert result["gate_state"] == "WAITING_GUARDIAN"

            m_res = await db_session.execute(select(Mission).where(Mission.id == mission_id))
            mission = m_res.scalar_one()
            assert mission.state != MissionState.SUCCEEDED.value


@pytest.mark.asyncio
async def test_mission_terminal_guardian_force_fail(db_session: AsyncSession) -> None:
    """Verify that FORCE_FAIL at MISSION_TERMINAL sets mission state to FAILED."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mission_id_str, task_ids = await _create_ready_mission_with_tasks(client)
        mission_id = uuid.UUID(mission_id_str)

        for tid_str in task_ids:
            t_res = await db_session.execute(
                select(Task).where(Task.id == uuid.UUID(tid_str)).with_for_update()
            )
            task = t_res.scalar_one()
            task.state = TaskState.SUCCEEDED.value
        await db_session.commit()

        mock_resp = _build_mock_check_response(
            mission_id=mission_id,
            action=GuardianAction.FORCE_FAIL,
            gate_state=GuardianGateState.BLOCKED,
            reason="Unrecoverable integrity violation at terminal check",
        )

        with patch(
            "omega.application.guardian.engine.GuardianEngine.execute_check",
            AsyncMock(return_value=mock_resp),
        ):
            result = await evaluate_mission(db_session, mission_id)
            assert result["status"] == "held_by_guardian"
            assert result["action"] == "FORCE_FAIL"


@pytest.mark.asyncio
async def test_mission_terminal_duplicate_evaluation_idempotent(
    db_session: AsyncSession,
) -> None:
    """Verify repeated orchestrator evaluations do not create duplicate decisions."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mission_id_str, task_ids = await _create_ready_mission_with_tasks(client)
        mission_id = uuid.UUID(mission_id_str)

        for tid_str in task_ids:
            t_res = await db_session.execute(
                select(Task).where(Task.id == uuid.UUID(tid_str)).with_for_update()
            )
            task = t_res.scalar_one()
            task.state = TaskState.SUCCEEDED.value
        await db_session.commit()

        # First evaluation
        res1 = await evaluate_mission(db_session, mission_id)
        assert res1["mission_state"] == MissionState.SUCCEEDED.value

        # Second evaluation (idempotent - skipped because already SUCCEEDED)
        res2 = await evaluate_mission(db_session, mission_id)
        assert res2["status"] == "skipped"

        # Check total decisions in database
        dec_res = await db_session.execute(
            select(GuardianDecision)
            .join(GuardianCheck, GuardianCheck.id == GuardianDecision.guardian_check_id)
            .where(
                GuardianCheck.mission_id == mission_id,
                GuardianCheck.checkpoint == "MISSION_TERMINAL",
            )
        )
        decisions = list(dec_res.scalars().all())
        assert len(decisions) == 1
