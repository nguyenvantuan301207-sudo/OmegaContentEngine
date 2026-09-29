from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from omega.application import mission_service, orchestrator
from omega.domain.guardian import GuardianAction, GuardianCheckpoint
from omega.domain.mission import ExecutionState, MissionState
from omega.infrastructure.models import Mission, MissionExecution


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def first(self):
        return self.value

    def all(self):
        return [self.value] if self.value is not None else []


class RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


@pytest.fixture
def mission_id():
    return uuid4()


def make_mission(mission_id, state):
    return Mission(
        id=mission_id,
        title="Dispatch mission",
        objective="Verify asynchronous evaluation dispatch",
        state=state,
        autonomy_level="SUPERVISED",
        priority=1,
        metadata_={},
        guardian_epoch=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def make_execution(mission_id, state):
    return MissionExecution(
        id=uuid4(),
        mission_id=mission_id,
        state=state,
        trigger_type="MANUAL",
    )


@pytest.mark.asyncio
async def test_valid_start_commits_running_with_durable_intent(monkeypatch, mission_id):
    mission = make_mission(mission_id, MissionState.READY.value)
    execution = make_execution(mission_id, ExecutionState.PLANNED.value)
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[ScalarResult(mission), ScalarResult(execution), ScalarResult(mission)]
    )
    committed = False

    async def commit():
        nonlocal committed
        committed = True

    session.commit = AsyncMock(side_effect=commit)
    session.flush = AsyncMock()
    enqueue = AsyncMock(return_value=SimpleNamespace(state="PENDING"))
    monkeypatch.setattr(mission_service.DurableDispatchService, "enqueue_async", enqueue)
    publish = MagicMock()
    direct_evaluate = AsyncMock()
    monkeypatch.setattr(mission_service.evaluate_mission_task, "delay", publish)
    monkeypatch.setattr(orchestrator, "evaluate_mission", direct_evaluate)

    result = await mission_service.start_mission(session, mission_id)

    assert result.state == MissionState.RUNNING
    assert mission.state == MissionState.RUNNING.value
    assert execution.state == ExecutionState.RUNNING.value
    session.commit.assert_awaited_once()
    publish.assert_not_called()
    assert enqueue.await_args.kwargs["purpose"] == "MISSION_START_EVALUATION"
    assert enqueue.await_args.kwargs["args"] == [str(mission_id), str(execution.id)]
    assert enqueue.await_args.kwargs["idempotency_key"].startswith(
        f"mission-evaluation:{execution.id}:"
    )
    direct_evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_resume_commits_running_then_publishes_once(monkeypatch, mission_id):
    mission = make_mission(mission_id, MissionState.PAUSED.value)
    execution = make_execution(mission_id, ExecutionState.PAUSED.value)
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            ScalarResult(mission),
            RowsResult([]),
            ScalarResult(None),
            ScalarResult(mission),
            ScalarResult(execution),
            ScalarResult(mission),
        ]
    )
    commit_count = 0

    async def commit():
        nonlocal commit_count
        commit_count += 1

    session.commit = AsyncMock(side_effect=commit)
    guardian_check = SimpleNamespace(
        decision=SimpleNamespace(action=GuardianAction.ALLOW, reason="allowed")
    )
    execute_check = AsyncMock(return_value=guardian_check)
    monkeypatch.setattr(
        "omega.application.guardian.engine.GuardianEngine.execute_check",
        execute_check,
    )
    publish = MagicMock(
        side_effect=lambda *_: commit_count == 2 or pytest.fail("published before resume commit")
    )
    direct_evaluate = AsyncMock()
    monkeypatch.setattr(mission_service.evaluate_mission_task, "delay", publish)
    monkeypatch.setattr(orchestrator, "evaluate_mission", direct_evaluate)

    result = await mission_service.resume_mission(session, mission_id)

    assert result.state == MissionState.RUNNING
    assert mission.state == MissionState.RUNNING.value
    assert execution.state == ExecutionState.RUNNING.value
    assert mission.guardian_epoch == 2
    assert session.commit.await_count == 2
    publish.assert_called_once_with(str(mission_id), str(execution.id))
    direct_evaluate.assert_not_awaited()
    request = execute_check.await_args.args[0]
    assert request.checkpoint == GuardianCheckpoint.PRE_TASK_DISPATCH


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", [mission_service.start_mission, mission_service.resume_mission]
)
async def test_missing_mission_publishes_nothing(monkeypatch, mission_id, operation):
    session = MagicMock()
    session.execute = AsyncMock(return_value=ScalarResult(None))
    publish = MagicMock()
    monkeypatch.setattr(mission_service.evaluate_mission_task, "delay", publish)

    assert await operation(session, mission_id) is None
    publish.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_running_start_replay_reuses_canonical_lineage(monkeypatch, mission_id):
    mission = make_mission(mission_id, MissionState.RUNNING.value)
    execution = make_execution(mission_id, ExecutionState.RUNNING.value)
    decision = SimpleNamespace(id=uuid4())
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[ScalarResult(mission), ScalarResult(execution), ScalarResult(decision)]
    )
    session.commit = AsyncMock()
    enqueue = AsyncMock(return_value=SimpleNamespace(state="PENDING"))
    monkeypatch.setattr(mission_service.DurableDispatchService, "enqueue_async", enqueue)
    publish = MagicMock()
    monkeypatch.setattr(mission_service.evaluate_mission_task, "delay", publish)

    result = await mission_service.start_mission(session, mission_id)

    assert result.state == MissionState.RUNNING
    session.commit.assert_awaited_once()
    assert enqueue.await_args.kwargs["idempotency_key"] == (
        f"mission-evaluation:{execution.id}:{decision.id}"
    )
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_start_is_independent_of_immediate_broker_availability(monkeypatch, mission_id):
    mission = make_mission(mission_id, MissionState.READY.value)
    execution = make_execution(mission_id, ExecutionState.PLANNED.value)
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[ScalarResult(mission), ScalarResult(execution), ScalarResult(mission)]
    )
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    enqueue = AsyncMock(return_value=SimpleNamespace(state="PENDING"))
    monkeypatch.setattr(mission_service.DurableDispatchService, "enqueue_async", enqueue)
    monkeypatch.setattr(
        mission_service.evaluate_mission_task,
        "delay",
        MagicMock(side_effect=RuntimeError("broker unavailable")),
    )

    result = await mission_service.start_mission(session, mission_id)

    session.commit.assert_awaited_once()
    assert result.state == MissionState.RUNNING
    enqueue.assert_awaited_once()
    assert mission.state == MissionState.RUNNING.value
    assert mission.state != MissionState.SUCCEEDED.value


@pytest.mark.asyncio
async def test_start_intent_failure_rolls_back_before_commit(monkeypatch, mission_id):
    mission = make_mission(mission_id, MissionState.READY.value)
    execution = make_execution(mission_id, ExecutionState.PLANNED.value)
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[ScalarResult(mission), ScalarResult(execution)])
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    monkeypatch.setattr(
        mission_service.DurableDispatchService,
        "enqueue_async",
        AsyncMock(side_effect=RuntimeError("intent construction failed")),
    )

    with pytest.raises(RuntimeError, match="intent construction failed"):
        await mission_service.start_mission(session, mission_id)

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_running_start_replay_dead_letter_fails_explicitly(monkeypatch, mission_id):
    mission = make_mission(mission_id, MissionState.RUNNING.value)
    execution = make_execution(mission_id, ExecutionState.RUNNING.value)
    decision = SimpleNamespace(id=uuid4())
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[ScalarResult(mission), ScalarResult(execution), ScalarResult(decision)]
    )
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    monkeypatch.setattr(
        mission_service.DurableDispatchService,
        "enqueue_async",
        AsyncMock(return_value=SimpleNamespace(state="DEAD_LETTER")),
    )

    with pytest.raises(mission_service.MissionStartDeliveryError, match="DEAD_LETTER"):
        await mission_service.start_mission(session, mission_id)

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()
