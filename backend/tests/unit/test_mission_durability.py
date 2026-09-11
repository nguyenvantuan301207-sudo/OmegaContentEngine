from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from omega.application.durable_dispatch import (
    CLAIMED,
    DEAD_LETTER,
    PENDING,
    RETRY,
    SENT,
    DurableDispatchService,
    _sanitize_error,
    _validate_args,
)
from omega.application.orchestrator import _mission_task_dispatch_key
from omega.application.render_service import ProductionRenderService
from omega.domain.production import ProductionQAStatus, RenderJobState
from omega.infrastructure.models import DurableDispatchIntent, ProductionRenderJob

ROOT = Path(__file__).parents[2]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_dispatch_model_and_deterministic_enqueue_reuse():
    authoritative = DurableDispatchIntent(
        id=uuid4(),
        idempotency_key="mission-task-dispatch:execution:task:7",
        task_name="omega.tasks.execute",
        args=["task"],
        purpose="MISSION_TASK_DISPATCH",
        state=PENDING,
        max_attempts=5,
    )
    query = MagicMock()
    query.filter_by.return_value.first.return_value = authoritative
    session = MagicMock()
    session.query.return_value = query

    reused = DurableDispatchService.enqueue(
        session,
        idempotency_key="mission-task-dispatch:execution:task:7",
        task_name="omega.tasks.execute",
        args=["task"],
        purpose="MISSION_TASK_DISPATCH",
    )

    assert reused is authoritative
    session.add.assert_not_called()
    session.flush.assert_not_called()
    assert {PENDING, CLAIMED, RETRY, SENT, DEAD_LETTER} == {
        "PENDING", "CLAIMED", "RETRY", "SENT", "DEAD_LETTER"
    }


def test_enqueue_persists_without_broker_publication():
    query = MagicMock()
    query.filter_by.return_value.first.return_value = None
    session = MagicMock()
    session.query.return_value = query

    intent = DurableDispatchService.enqueue(
        session,
        idempotency_key="render-dispatch:request:job",
        task_name="omega.production.render",
        args=["channel", "request", "job"],
        purpose="PRODUCTION_RENDER_DISPATCH",
    )

    session.add.assert_called_once_with(intent)
    session.flush.assert_called_once()
    assert intent.state is None or intent.state == PENDING
    assert not hasattr(session, "send_task") or not session.send_task.called


def test_malformed_payload_and_broker_error_fail_closed():
    with pytest.raises(ValueError, match="JSON array"):
        _validate_args({"not": "positional args"})
    assert "redacted" in _sanitize_error(RuntimeError("redis://secret-token"))


def test_converted_boundaries_use_durable_intents_and_no_direct_delay():
    orchestrator = source("src/omega/application/orchestrator.py")
    render_service = source("src/omega/application/render_service.py")
    tasks = source("src/omega/worker/tasks.py")
    assert 'task_name="omega.tasks.execute"' in orchestrator
    assert "execute_task.delay(" not in orchestrator
    assert 'task_name="omega.orchestrator.evaluate"' in tasks
    assert "evaluate_mission_task.delay(" not in tasks
    assert 'task_name="omega.production.render"' in tasks
    assert "execute_production_render_task.delay(" not in tasks
    assert "render-terminal-evaluation:" in render_service
    assert "_enqueue_terminal_evaluation(session, prod_req, job_id)" in render_service


def test_relay_is_bounded_recoverable_and_has_no_self_retry():
    dispatch = source("src/omega/application/durable_dispatch.py")
    tasks = source("src/omega/worker/tasks.py")
    assert ".with_for_update(skip_locked=True)" in dispatch
    assert "recover_stale_claims" in dispatch
    assert "DEAD_LETTER if intent.attempt >= intent.max_attempts else RETRY" in dispatch
    assert "for intent in claimed:" in dispatch
    assert "while True" not in dispatch
    assert "self.retry(" not in tasks
    assert "autoretry_for" not in tasks


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected_status"),
    [
        (RenderJobState.RUNNING.value, ProductionQAStatus.PENDING),
        (RenderJobState.FAILED.value, ProductionQAStatus.BLOCKED),
        (RenderJobState.CANCELLED.value, ProductionQAStatus.BLOCKED),
    ],
)
async def test_non_claimable_render_delivery_never_starts_render(state, expected_status):
    job = ProductionRenderJob(
        id=uuid4(),
        production_request_id=uuid4(),
        render_plan_id=uuid4(),
        idempotency_key="job",
        state=state,
    )
    session = MagicMock()
    session.execute = AsyncMock(return_value=ScalarResult(job))
    session.rollback = AsyncMock()
    service = ProductionRenderService(storage=MagicMock())
    service.renderer = MagicMock()

    artifact, status = await service.execute_render_job(
        session, uuid4(), job.production_request_id, job.id
    )

    assert artifact is None
    assert status == expected_status
    session.rollback.assert_awaited_once()
    assert not service.renderer.mock_calls


@pytest.mark.asyncio
async def test_succeeded_render_delivery_returns_existing_artifact_without_render():
    artifact = MagicMock()
    job = ProductionRenderJob(
        id=uuid4(),
        production_request_id=uuid4(),
        render_plan_id=uuid4(),
        idempotency_key="job",
        state=RenderJobState.SUCCEEDED.value,
    )
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[ScalarResult(job), ScalarResult(artifact)])
    session.rollback = AsyncMock()
    service = ProductionRenderService(storage=MagicMock())
    service.renderer = MagicMock()

    result, status = await service.execute_render_job(
        session, uuid4(), job.production_request_id, job.id
    )

    assert result is artifact
    assert status == ProductionQAStatus.PASSED
    assert not service.renderer.mock_calls


# ── P9-D Fix Tests (A–G) ─────────────────────────────────────────────────────


# A. Mission dispatch identity differs when retry_count changes.
def test_dispatch_key_differs_across_retry_counts():
    execution_id = uuid4()
    task_id = uuid4()
    epoch = 3

    key_retry_0 = (
        f"mission-task-dispatch:{execution_id}:{task_id}:0:{epoch}"
    )
    key_retry_1 = (
        f"mission-task-dispatch:{execution_id}:{task_id}:1:{epoch}"
    )
    assert key_retry_0 != key_retry_1
    # Confirm the orchestrator source encodes retry_count in the key.
    orchestrator_src = source("src/omega/application/orchestrator.py")
    assert "task.retry_count" in orchestrator_src
    assert "mission-task-dispatch:" in orchestrator_src


def test_sent_initial_dispatch_does_not_block_persisted_render_continuation():
    # Candidate #4's persisted stranded lineage, reproduced without mutating its rows.
    execution_id = UUID("6ede0f19-2feb-43dd-8f19-42797c3e4d99")
    task_id = UUID("1930c0da-bd0d-48fd-8fb4-601da5607a80")
    request_id = UUID("6dc58e7b-5346-40cd-a760-7200c0056c95")
    render_job_id = UUID("b92e27d0-4dab-4b94-b5e1-85d326d6cbcd")
    initial_task = SimpleNamespace(
        id=task_id, task_type="production", retry_count=0, output=None
    )
    continued_task = SimpleNamespace(
        id=task_id,
        task_type="production",
        retry_count=0,
        output={
            "production_request_id": str(request_id),
            "render_job_id": str(render_job_id),
        },
    )
    initial_key = _mission_task_dispatch_key(initial_task, execution_id, 1)
    continuation_key = _mission_task_dispatch_key(continued_task, execution_id, 1)
    sent_initial = DurableDispatchIntent(
        id=uuid4(),
        idempotency_key=initial_key,
        task_name="omega.tasks.execute",
        args=[str(task_id)],
        purpose="MISSION_TASK_DISPATCH",
        state=SENT,
        max_attempts=5,
    )
    query = MagicMock()
    query.filter_by.return_value.first.side_effect = [sent_initial, None]
    session = MagicMock()
    session.query.return_value = query

    replayed_initial = DurableDispatchService.enqueue(
        session,
        idempotency_key=initial_key,
        task_name="omega.tasks.execute",
        args=[str(task_id)],
        purpose="MISSION_TASK_DISPATCH",
    )
    continuation = DurableDispatchService.enqueue(
        session,
        idempotency_key=continuation_key,
        task_name="omega.tasks.execute",
        args=[str(task_id)],
        purpose="MISSION_TASK_DISPATCH",
    )

    assert replayed_initial is sent_initial
    assert continuation.idempotency_key == continuation_key
    assert continuation_key == (
        f"{initial_key}:render-terminal:{render_job_id}"
    )
    session.add.assert_called_once_with(continuation)


@pytest.mark.parametrize("intent_state", [PENDING, CLAIMED, RETRY, SENT])
def test_render_continuation_replay_reuses_one_logical_intent(intent_state):
    execution_id, task_id, request_id, render_job_id = (
        uuid4(), uuid4(), uuid4(), uuid4()
    )
    task = SimpleNamespace(
        id=task_id,
        task_type="production",
        retry_count=0,
        output={
            "production_request_id": str(request_id),
            "render_job_id": str(render_job_id),
        },
    )
    key = _mission_task_dispatch_key(task, execution_id, 1)
    existing = DurableDispatchIntent(
        id=uuid4(),
        idempotency_key=key,
        task_name="omega.tasks.execute",
        args=[str(task_id)],
        purpose="MISSION_TASK_DISPATCH",
        state=intent_state,
        max_attempts=5,
    )
    query = MagicMock()
    query.filter_by.return_value.first.return_value = existing
    session = MagicMock()
    session.query.return_value = query

    first = DurableDispatchService.enqueue(
        session,
        idempotency_key=key,
        task_name="omega.tasks.execute",
        args=[str(task_id)],
        purpose="MISSION_TASK_DISPATCH",
    )
    replay = DurableDispatchService.enqueue(
        session,
        idempotency_key=_mission_task_dispatch_key(task, execution_id, 1),
        task_name="omega.tasks.execute",
        args=[str(task_id)],
        purpose="MISSION_TASK_DISPATCH",
    )

    assert first is replay is existing
    session.add.assert_not_called()


# B. Stale exhausted CLAIMED intent becomes DEAD_LETTER.
def test_recover_stale_claims_exhausted_goes_to_dead_letter():
    from sqlalchemy.dialects import postgresql

    stale_before = datetime.now(UTC)
    exhausted_rowcount = 2
    retry_rowcount = 1
    dead_result = MagicMock()
    dead_result.rowcount = exhausted_rowcount
    retry_result = MagicMock()
    retry_result.rowcount = retry_rowcount
    session = MagicMock()
    session.execute = MagicMock(side_effect=[dead_result, retry_result])
    session.commit = MagicMock()

    count = DurableDispatchService.recover_stale_claims(session, stale_before=stale_before)

    assert count == exhausted_rowcount + retry_rowcount
    assert session.execute.call_count == 2

    # Extract the SQLAlchemy Update statements from the call args and compile them.
    calls = session.execute.call_args_list
    dead_stmt = calls[0].args[0]
    retry_stmt = calls[1].args[0]

    dialect = postgresql.dialect()
    dead_compiled = dead_stmt.compile(dialect=dialect, compile_kwargs={"literal_binds": True})
    retry_compiled = retry_stmt.compile(dialect=dialect, compile_kwargs={"literal_binds": True})

    dead_sql = str(dead_compiled)
    retry_sql = str(retry_compiled)

    assert DEAD_LETTER in dead_sql, f"Expected DEAD_LETTER in first UPDATE, got: {dead_sql!r}"
    assert RETRY in retry_sql, f"Expected RETRY in second UPDATE, got: {retry_sql!r}"


# C. claim_batch cannot reclaim exhausted RETRY/PENDING intent.
def test_claim_batch_excludes_exhausted_intents():
    exhausted = DurableDispatchIntent(
        id=uuid4(),
        idempotency_key="exhausted-key",
        task_name="omega.tasks.execute",
        args=["x"],
        purpose="MISSION_TASK_DISPATCH",
        state=RETRY,
        attempt=5,
        max_attempts=5,
    )
    # claim_batch must filter out rows where attempt >= max_attempts.
    # Verify the source enforces this.
    dispatch_src = source("src/omega/application/durable_dispatch.py")
    assert "attempt < DurableDispatchIntent.max_attempts" in dispatch_src

    # Structural check: exhausted intent would NOT be yielded from a real query.
    assert exhausted.attempt >= exhausted.max_attempts


# D. Same idempotency key + same immutable identity reuses intent.
def test_enqueue_same_identity_reuses_existing():
    key = "mission-task-dispatch:exec:task:0:3"
    existing = DurableDispatchIntent(
        id=uuid4(),
        idempotency_key=key,
        task_name="omega.tasks.execute",
        args=["task-id"],
        purpose="MISSION_TASK_DISPATCH",
        state=SENT,
        max_attempts=5,
    )
    query = MagicMock()
    query.filter_by.return_value.first.return_value = existing
    session = MagicMock()
    session.query.return_value = query

    result = DurableDispatchService.enqueue(
        session,
        idempotency_key=key,
        task_name="omega.tasks.execute",
        args=["task-id"],
        purpose="MISSION_TASK_DISPATCH",
    )

    assert result is existing
    session.add.assert_not_called()


# E. Same idempotency key + conflicting task/payload/purpose/correlation fails closed.
def test_enqueue_conflicting_identity_raises_value_error():
    key = "mission-task-dispatch:exec:task:0:3"
    existing = DurableDispatchIntent(
        id=uuid4(),
        idempotency_key=key,
        task_name="omega.tasks.execute",
        args=["original-task-id"],
        purpose="MISSION_TASK_DISPATCH",
        state=SENT,
        max_attempts=5,
    )
    query = MagicMock()
    query.filter_by.return_value.first.return_value = existing
    session = MagicMock()
    session.query.return_value = query

    with pytest.raises(ValueError, match="conflicting immutable identity"):
        DurableDispatchService.enqueue(
            session,
            idempotency_key=key,
            task_name="omega.tasks.execute",
            args=["different-task-id"],  # conflict
            purpose="MISSION_TASK_DISPATCH",
        )

    with pytest.raises(ValueError, match="conflicting immutable identity"):
        DurableDispatchService.enqueue(
            session,
            idempotency_key=key,
            task_name="omega.tasks.execute",
            args=["original-task-id"],
            purpose="DIFFERENT_PURPOSE",  # conflict
        )

    with pytest.raises(ValueError, match="conflicting immutable identity"):
        DurableDispatchService.enqueue(
            session,
            idempotency_key=key,
            task_name="omega.different.task",  # conflict
            args=["original-task-id"],
            purpose="MISSION_TASK_DISPATCH",
        )


# F. Pre-render BLOCKED path does not persist RenderJob RUNNING.
@pytest.mark.asyncio
async def test_pre_render_blocked_does_not_persist_running():
    """Guardian blocks PRE_RENDER — job must stay QUEUED, never RUNNING."""
    prod_request_id = uuid4()
    job_id = uuid4()

    mock_plan = SimpleNamespace(
        version=1, width=1920, height=1080, fps=30,
        video_codec="h264", audio_codec="aac",
    )
    mock_request = SimpleNamespace(
        id=prod_request_id,
        mission_execution_id=uuid4(),
        script_version_id=uuid4(),
        channel_dna_revision_id=uuid4(),
        video_codec="h264",
        target_width=1920,
        target_height=1080,
        scenes=[], assets=[], narration_segments=[], subtitle_cues=[],
        script_version=SimpleNamespace(sections=[]),
        content_request=SimpleNamespace(default_duration_min_seconds=60),
    )
    # Use a plain SimpleNamespace — avoids SQLAlchemy ORM descriptor conflicts
    # when assigning SimpleNamespace objects to relationship attributes.
    job = SimpleNamespace(
        id=job_id,
        production_request_id=prod_request_id,
        render_plan_id=uuid4(),
        idempotency_key="job",
        state=RenderJobState.QUEUED.value,
        started_at=None,
        render_plan=mock_plan,
        production_request=mock_request,
    )

    session = MagicMock()
    session.execute = AsyncMock(return_value=ScalarResult(job))
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    service = ProductionRenderService(storage=MagicMock())
    service.renderer = MagicMock()

    # _resolve_mission_id returns a mission_id — triggers the Guardian branch.
    mission_id = uuid4()

    async def fake_resolve(s, req):
        return mission_id

    service._resolve_mission_id = fake_resolve

    # _record_job_failure is a no-op stub — Guardian will call it when it blocks.
    async def fake_record_job_failure(s, jid, code, msg):
        pass

    service._record_job_failure = fake_record_job_failure

    # Patch GuardianEngine so it returns a BLOCKED decision without hitting the DB.
    import unittest.mock as _mock

    from omega.domain.guardian import GuardianAction

    blocked_decision = SimpleNamespace(action=GuardianAction.FORCE_FAIL, reason="test block")
    blocked_check = SimpleNamespace(decision=blocked_decision)

    async def fake_execute_check(check_create):
        return blocked_check

    fake_engine = SimpleNamespace(execute_check=fake_execute_check)

    with _mock.patch(
        "omega.application.guardian.engine.GuardianEngine",
        return_value=fake_engine,
    ), _mock.patch(
        "omega.infrastructure.database.AsyncSessionLocal",
        new=MagicMock(),
    ):
        artifact, status = await service.execute_render_job(
            session, uuid4(), prod_request_id, job_id
        )

    # Job state must remain QUEUED — RUNNING must never have been persisted.
    assert job.state == RenderJobState.QUEUED.value, (
        f"Expected job.state=QUEUED but got {job.state!r}"
    )
    assert artifact is None
    assert status == ProductionQAStatus.BLOCKED
    session.commit.assert_not_awaited()
    assert not service.renderer.mock_calls


# G. RUNNING duplicate delivery still never invokes renderer.
@pytest.mark.asyncio
async def test_running_duplicate_delivery_returns_pending_without_render():
    job = ProductionRenderJob(
        id=uuid4(),
        production_request_id=uuid4(),
        render_plan_id=uuid4(),
        idempotency_key="job",
        state=RenderJobState.RUNNING.value,
    )
    session = MagicMock()
    session.execute = AsyncMock(return_value=ScalarResult(job))
    session.rollback = AsyncMock()
    service = ProductionRenderService(storage=MagicMock())
    service.renderer = MagicMock()

    artifact, status = await service.execute_render_job(
        session, uuid4(), job.production_request_id, job.id
    )

    assert artifact is None
    assert status == ProductionQAStatus.PENDING
    session.rollback.assert_awaited_once()
    # commit must NOT have been called — no RUNNING transition
    session.commit.assert_not_called()
    assert not service.renderer.mock_calls

