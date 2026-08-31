"""Integration tests for Celery autonomy tick sweep execution.

Verifies:
1. No MissingGreenlet or DetachedInstanceError during tick sweep.
2. No cross-event-loop RuntimeError across repeated task runs.
3. Valid state transitions including PLANNING state before dispatch.
4. Safe rollback handling without expired ORM attribute access.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.autonomy.loop_service import AutonomyLoopService
from omega.domain.autonomy import AutonomyLevel, AutonomyLoopState
from omega.infrastructure.models import (
    AutonomyLoopLatestPointer,
    Channel,
    ChannelDNARevision,
    GuardianCheck,
    GuardianDecision,
    GuardianRuleSet,
    GuardianStateTransition,
    Mission,
)
from omega.worker.tasks import autonomy_tick_sweep_task

pytestmark = pytest.mark.usefixtures("publisher_test_env")


@pytest_asyncio.fixture
async def autonomy_env(db_session: AsyncSession) -> dict[str, Any]:
    """Setup base entities for autonomy loop tests."""
    chan = Channel(
        id=uuid4(),
        slug=f"autonomy-chan-{uuid4().hex[:8]}",
        name="Autonomy Channel",
        platform="YOUTUBE",
    )
    db_session.add(chan)

    dna = ChannelDNARevision(
        id=uuid4(),
        channel_id=chan.id,
        version=1,
        snapshot={"name": "Autonomy Channel DNA"},
        change_reason="Initial snapshot",
    )
    db_session.add(dna)

    mission = Mission(
        id=uuid4(),
        channel_id=chan.id,
        title="Autonomous Mission",
        objective="Autonomously execute mission loop",
        state="ACTIVE",
        guardian_epoch=1,
    )
    db_session.add(mission)

    await db_session.execute(
        text("UPDATE guardian_rulesets SET status = 'INACTIVE' WHERE status = 'ACTIVE'")
    )

    ruleset = GuardianRuleSet(
        id=uuid4(),
        version=f"v-{uuid4().hex[:6]}",
        status="ACTIVE",
        effective_at=datetime.now(UTC),
        rules_config={"rules": []},
        checksum=hashlib.sha256(b"autonomy-default-ruleset").hexdigest(),
    )
    db_session.add(ruleset)

    check = GuardianCheck(
        id=uuid4(),
        mission_id=mission.id,
        trigger_type="MANUAL",
        checkpoint="PRE_TASK_DISPATCH",
        ruleset_id=ruleset.id,
        ruleset_version=ruleset.version,
        ruleset_checksum=ruleset.checksum,
        status="COMPLETED",
        idempotency_key=f"chk-{uuid4().hex}",
        guardian_epoch=1,
    )
    db_session.add(check)

    decision = GuardianDecision(
        id=uuid4(),
        guardian_check_id=check.id,
        action="ALLOW",
        reason="Initial allow",
        resulting_gate_state="OPEN",
    )
    db_session.add(decision)

    trans = GuardianStateTransition(
        id=uuid4(),
        mission_id=mission.id,
        checkpoint="PRE_TASK_DISPATCH",
        from_gate_state="WAITING_GUARDIAN",
        to_gate_state="OPEN",
        decision_id=decision.id,
        guardian_epoch=1,
        reason="Initial setup pass",
    )
    db_session.add(trans)

    await db_session.commit()

    return {
        "channel": chan,
        "dna": dna,
        "mission": mission,
    }


def test_autonomy_tick_sweep_celery_execution_no_missing_greenlet(
    autonomy_env: dict[str, Any],
):
    """Verify autonomy_tick_sweep_task executes in synchronous Celery context without MissingGreenlet."""
    # Run the Celery task directly in synchronous worker mode (uses asyncio.run internally)
    res = autonomy_tick_sweep_task()
    assert res["status"] == "success"
    assert "ticks_processed" in res


def test_autonomy_tick_sweep_cross_event_loop_repeated_runs(
    autonomy_env: dict[str, Any],
):
    """Verify repeated Celery worker task runs do not leak event loops or throw attached-to-different-loop."""
    for _ in range(3):
        res = autonomy_tick_sweep_task()
        assert res["status"] == "success"
        assert res["ticks_processed"] >= 0


@pytest.mark.asyncio
async def test_autonomy_tick_sweep_processes_idle_loop_and_transitions(
    db_session: AsyncSession,
    autonomy_env: dict[str, Any],
):
    """Verify an IDLE loop is processed through PLANNING and transitions without ORM expiration issues."""
    chan = autonomy_env["channel"]
    mission = autonomy_env["mission"]

    loop = await AutonomyLoopService.create_loop(
        session=db_session,
        mission_id=mission.id,
        channel_id=chan.id,
        autonomy_level=AutonomyLevel.SUPERVISED,
    )
    await db_session.commit()

    # Verify loop is initially IDLE
    stmt = select(AutonomyLoopLatestPointer).where(AutonomyLoopLatestPointer.loop_id == loop.id)
    pointer = (await db_session.execute(stmt)).scalar_one()
    assert pointer.operational_state == AutonomyLoopState.IDLE.value

    # Execute synchronous Celery task in thread (simulates Celery worker without ambient loop)
    import asyncio

    res = await asyncio.to_thread(autonomy_tick_sweep_task)
    assert res["status"] == "success"
    assert res["ticks_processed"] >= 1

    # Verify loop pointer state moved beyond IDLE/OBSERVING (e.g. to WAITING_APPROVAL for SUPERVISED)
    await db_session.refresh(pointer)
    assert pointer.operational_state in (
        AutonomyLoopState.WAITING_APPROVAL.value,
        AutonomyLoopState.EXECUTING.value,
        AutonomyLoopState.PLANNING.value,
        AutonomyLoopState.VERIFYING.value,
    )


@pytest.mark.asyncio
async def test_autonomy_tick_sweep_rollback_safety_without_orm_expiration_crash(
    db_session: AsyncSession,
    autonomy_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify that when a loop raises an exception and triggers rollback, no MissingGreenlet occurs."""
    from omega.application.autonomy.observation_service import AutonomyObservationService

    call_count = 0

    async def mock_capture_fail_first(session: AsyncSession, loop_id: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("Injected observation failure for rollback testing")
        return await original_capture(session, loop_id)

    original_capture = AutonomyObservationService.capture_snapshot
    monkeypatch.setattr(
        AutonomyObservationService,
        "capture_snapshot",
        mock_capture_fail_first,
    )

    chan = autonomy_env["channel"]
    mission = autonomy_env["mission"]

    await AutonomyLoopService.create_loop(
        session=db_session,
        mission_id=mission.id,
        channel_id=chan.id,
        autonomy_level=AutonomyLevel.SUPERVISED,
    )
    await db_session.commit()

    # Execute tick task — loop 1 will fail and rollback; subsequent loops should not crash with MissingGreenlet
    import asyncio

    res = await asyncio.to_thread(autonomy_tick_sweep_task)
    assert res["status"] == "success"
