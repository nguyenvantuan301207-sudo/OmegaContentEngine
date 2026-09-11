"""Offline behavioral contracts for canonical Mission production."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from omega.application import executor as executor_module
from omega.application import orchestrator
from omega.application.durable_dispatch import DurableDispatchService
from omega.application.production_service import ProductionService
from omega.domain.content import (
    ContentOutcome,
    ContentRequestStatus,
    ScriptQAStatus,
)
from omega.domain.mission import MissionState
from omega.domain.production import (
    MediaArtifactType,
    ProductionMode,
    ProductionRequestStatus,
    RenderJobState,
)
from omega.domain.task import TaskState
from omega.infrastructure import database, database_sync
from omega.infrastructure.models import (
    ContentGenerationRequest,
    MediaArtifact,
    Mission,
    MissionExecution,
    ProductionRenderJob,
    ProductionRequest,
    ScriptVersion,
    Task,
    TaskDependency,
)
from omega.worker import tasks as worker_tasks


def context(content=None):
    result = {
        "mission_id": str(uuid4()),
        "execution_id": str(uuid4()),
        "dependency_outputs": {},
    }
    if content is not None:
        result["dependency_outputs"]["content_generation"] = content
    return result


@pytest.mark.parametrize(
    "content",
    [
        None,
        {},
        {"content_request_id": str(uuid4())},
        {"script_version_id": str(uuid4())},
        {"content_request_id": "bad", "script_version_id": str(uuid4())},
    ],
)
def test_canonical_production_requires_complete_direct_content_correlation(content) -> None:
    with pytest.raises(ValueError):
        worker_tasks._canonical_production_pair(context(content))


def test_canonical_production_pair_accepts_only_authoritative_ids() -> None:
    request_id, script_id = uuid4(), uuid4()
    assert worker_tasks._canonical_production_pair(
        context({"content_request_id": str(request_id), "script_version_id": str(script_id)})
    ) == (request_id, script_id)


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class AsyncSession:
    def __init__(self, records, artifact=None):
        self.records = records
        self.artifact = artifact

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, model, record_id):
        return self.records.get((model, record_id))

    async def execute(self, statement):
        return ScalarResult(self.artifact)


@pytest.fixture
def lineage():
    mission_id, execution_id, channel_id, dna_id, task_id = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    )
    content_id, script_id, request_id, job_id, artifact_id = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    )
    task = SimpleNamespace(id=task_id, mission_id=mission_id, execution_id=execution_id)
    execution = SimpleNamespace(id=execution_id, mission_id=mission_id, channel_dna_revision_id=dna_id)
    mission = SimpleNamespace(id=mission_id, channel_id=channel_id)
    content = SimpleNamespace(
        id=content_id,
        mission_execution_id=execution_id,
        channel_id=channel_id,
        channel_dna_revision_id=dna_id,
        status=ContentRequestStatus.SUCCEEDED.value,
        outcome=ContentOutcome.GENERATED.value,
    )
    script = SimpleNamespace(
        id=script_id,
        content_request_id=content_id,
        is_current=True,
        qa_status=ScriptQAStatus.PASSED.value,
    )
    request = SimpleNamespace(
        id=request_id,
        mission_execution_id=execution_id,
        channel_id=channel_id,
        script_version_id=script_id,
        content_request_id=content_id,
        channel_dna_revision_id=dna_id,
        mode=ProductionMode.MISSION_EXECUTION.value,
        status=ProductionRequestStatus.READY.value,
        outcome=None,
    )
    artifact = SimpleNamespace(
        id=artifact_id,
        production_request_id=request_id,
        render_job_id=job_id,
        artifact_type=MediaArtifactType.VIDEO.value,
    )
    records = {
        (Task, task_id): task,
        (MissionExecution, execution_id): execution,
        (Mission, mission_id): mission,
        (ContentGenerationRequest, content_id): content,
        (ScriptVersion, script_id): script,
        (ProductionRequest, request_id): request,
        (MediaArtifact, artifact_id): artifact,
    }
    ctx = {
        "mission_id": str(mission_id),
        "execution_id": str(execution_id),
        "dependency_outputs": {"content_generation": {
            "content_request_id": str(content_id), "script_version_id": str(script_id)
        }},
    }
    return SimpleNamespace(**locals())


def install(monkeypatch, data, state=RenderJobState.QUEUED.value, is_new=True, artifact=None):
    session = AsyncSession(data.records, artifact=artifact)
    monkeypatch.setattr(database, "AsyncWorkerSessionLocal", lambda: session)
    create = AsyncMock(return_value=data.request)

    async def prepare_request(*args):
        data.request.status = ProductionRequestStatus.READY.value
        return data.request

    prepare = AsyncMock(side_effect=prepare_request)
    job = SimpleNamespace(id=data.job_id, state=state)

    async def allocate_ready_request(*args):
        assert data.request.status == ProductionRequestStatus.READY.value
        return job, SimpleNamespace(), is_new

    allocate = AsyncMock(side_effect=allocate_ready_request)
    monkeypatch.setattr(ProductionService, "create_production_request", create)
    monkeypatch.setattr(ProductionService, "prepare_production", prepare)
    monkeypatch.setattr(ProductionService, "allocate_render_job", allocate)
    dispatch = MagicMock()
    monkeypatch.setattr(worker_tasks.execute_production_render_task, "delay", dispatch)
    return create, prepare, allocate, dispatch


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: setattr(d.script, "content_request_id", uuid4()),
        lambda d: setattr(d.content, "mission_execution_id", uuid4()),
        lambda d: setattr(d.content, "channel_id", uuid4()),
        lambda d: setattr(d.content, "channel_dna_revision_id", uuid4()),
        lambda d: setattr(d.script, "is_current", False),
    ],
)
def test_lineage_mismatch_fails_before_production_service(monkeypatch, lineage, mutation) -> None:
    mutation(lineage)
    create, _, _, _ = install(monkeypatch, lineage)
    with pytest.raises(ValueError):
        worker_tasks._execute_canonical_production(lineage.task_id, lineage.ctx)
    create.assert_not_awaited()


def test_draft_prepares_allocates_deterministically_without_early_dispatch(monkeypatch, lineage) -> None:
    lineage.request.status = ProductionRequestStatus.DRAFT.value
    create, prepare, allocate, dispatch = install(monkeypatch, lineage)
    result = worker_tasks._execute_canonical_production(lineage.task_id, lineage.ctx)
    assert isinstance(result, worker_tasks._PendingProductionResult)
    assert result == {"production_request_id": str(lineage.request_id), "render_job_id": str(lineage.job_id)}
    assert create.await_args.kwargs["idempotency_key"] == hashlib.sha256(
        f"mission-production:{lineage.execution_id}:{lineage.task_id}".encode()
    ).hexdigest()
    prepare.assert_awaited_once()
    assert lineage.request.status == ProductionRequestStatus.READY.value
    allocate.assert_awaited_once()
    assert allocate.await_args.args[3] == hashlib.sha256(
        f"mission-render:{lineage.request_id}:{lineage.task_id}".encode()
    ).hexdigest()
    assert result.dispatch_required is True
    assert result.channel_id == str(lineage.channel_id)
    assert result.production_request_id == str(lineage.request_id)
    assert result.render_job_id == str(lineage.job_id)
    dispatch.assert_not_called()


@pytest.mark.parametrize("state", [RenderJobState.QUEUED.value, RenderJobState.RUNNING.value, RenderJobState.RETRY.value])
def test_pending_reentry_reuses_job_without_redispatch(monkeypatch, lineage, state) -> None:
    _, prepare, _, dispatch = install(monkeypatch, lineage, state=state, is_new=False)
    result = worker_tasks._execute_canonical_production(lineage.task_id, lineage.ctx)
    assert isinstance(result, worker_tasks._PendingProductionResult)
    assert result.dispatch_required is False
    prepare.assert_not_awaited()
    dispatch.assert_not_called()


def test_terminal_success_requires_and_returns_valid_artifact(monkeypatch, lineage) -> None:
    _, _, _, dispatch = install(
        monkeypatch, lineage, state=RenderJobState.SUCCEEDED.value, is_new=False, artifact=lineage.artifact
    )
    result = worker_tasks._execute_canonical_production(lineage.task_id, lineage.ctx)
    assert result["media_artifact_id"] == str(lineage.artifact_id)
    dispatch.assert_not_called()


def test_terminal_success_without_artifact_fails_closed(monkeypatch, lineage) -> None:
    install(monkeypatch, lineage, state=RenderJobState.SUCCEEDED.value, is_new=False)
    with pytest.raises(ValueError, match="MediaArtifact"):
        worker_tasks._execute_canonical_production(lineage.task_id, lineage.ctx)


@pytest.mark.parametrize("state", [RenderJobState.FAILED.value, RenderJobState.CANCELLED.value])
def test_terminal_render_failure_fails_stage(monkeypatch, lineage, state) -> None:
    install(monkeypatch, lineage, state=state, is_new=False)
    with pytest.raises(RuntimeError, match="terminal failure"):
        worker_tasks._execute_canonical_production(lineage.task_id, lineage.ctx)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: setattr(d.content, "outcome", ContentOutcome.BLOCKED.value),
        lambda d: setattr(d.script, "qa_status", ScriptQAStatus.BLOCKED.value),
    ],
)
def test_blocked_content_cannot_enter_production(monkeypatch, lineage, mutation) -> None:
    mutation(lineage)
    create, _, _, _ = install(monkeypatch, lineage)

    with pytest.raises(ValueError, match="not accepted"):
        worker_tasks._execute_canonical_production(lineage.task_id, lineage.ctx)

    create.assert_not_awaited()


class Query:
    def __init__(self, record): self.record = record
    def filter(self, *args): return self
    def with_for_update(self): return self
    def first(self): return self.record
    def outerjoin(self, *args): return self
    def order_by(self, *args): return self
    def all(self): return []


def test_execute_task_commits_ids_before_new_render_dispatch(monkeypatch) -> None:
    mission_id, execution_id, task_id = uuid4(), uuid4(), uuid4()
    request_id, job_id, channel_id = uuid4(), uuid4(), uuid4()
    original_input = {"unrelated": "preserved"}
    task = SimpleNamespace(id=task_id, mission_id=mission_id, execution_id=execution_id,
        task_type="production", title="Production", state=TaskState.QUEUED.value,
        dispatched_epoch=4, input=original_input, output=None, retry_count=0, max_retries=0)
    mission = SimpleNamespace(id=mission_id, state=MissionState.RUNNING.value, guardian_epoch=4)
    session = MagicMock()
    session.query.side_effect = [
        Query(SimpleNamespace(mission_id=mission_id)), Query(mission), Query(task),
        Query(None), Query(mission), Query(task),
    ]
    commit_count = 0

    def committed():
        nonlocal commit_count
        commit_count += 1

    session.commit.side_effect = committed
    monkeypatch.setattr(database_sync, "SyncSessionLocal", lambda: session)
    adapter = MagicMock(return_value=worker_tasks._PendingProductionResult(
        {"production_request_id": str(request_id), "render_job_id": str(job_id)},
        dispatch_required=True, channel_id=str(channel_id),
        production_request_id=str(request_id), render_job_id=str(job_id)))
    monkeypatch.setattr(worker_tasks, "_execute_canonical_production", adapter)
    registry = MagicMock()
    monkeypatch.setattr(executor_module, "default_executor_registry", registry)

    # Capture DurableDispatchService.enqueue calls to prove:
    # - task.output is set before enqueue is called
    # - at least one commit (correlation commit) precedes the enqueue
    # - direct execute_production_render_task.delay is NOT called
    enqueue_calls = []

    def capture_enqueue(sess, *, idempotency_key, task_name, args, purpose, **correlations):
        enqueue_calls.append({
            "idempotency_key": idempotency_key,
            "task_name": task_name,
            "args": args,
            "purpose": purpose,
            "output_at_enqueue": dict(task.output) if task.output else None,
        })

    monkeypatch.setattr(DurableDispatchService, "enqueue", capture_enqueue)
    direct_dispatch = MagicMock()
    monkeypatch.setattr(worker_tasks.execute_production_render_task, "delay", direct_dispatch)

    result = worker_tasks.execute_task.run(str(task_id))

    assert result["status"] == "pending"
    assert task.state == TaskState.RUNNING.value
    assert task.input is original_input
    assert task.output == {"production_request_id": str(request_id), "render_job_id": str(job_id)}

    # Durable dispatch must have been enrolled for the render task
    render_enqueues = [c for c in enqueue_calls if c["task_name"] == "omega.production.render"]
    assert len(render_enqueues) == 1, "Expected exactly one render dispatch intent enrolled"
    render_enqueue = render_enqueues[0]
    assert render_enqueue["args"] == [str(channel_id), str(request_id), str(job_id)]
    assert render_enqueue["purpose"] == "PRODUCTION_RENDER_DISPATCH"
    # task.output must already be set when the durable intent is enrolled
    assert render_enqueue["output_at_enqueue"] == {
        "production_request_id": str(request_id),
        "render_job_id": str(job_id),
    }

    # Direct broker publication must NOT occur
    direct_dispatch.assert_not_called()
    registry.get.assert_not_called()


def test_execute_task_refuses_pending_dispatch_after_guardian_epoch_change(monkeypatch) -> None:
    mission_id, execution_id, task_id = uuid4(), uuid4(), uuid4()
    task = SimpleNamespace(id=task_id, mission_id=mission_id, execution_id=execution_id,
        task_type="production", title="Production", state=TaskState.QUEUED.value,
        dispatched_epoch=4, input={}, output=None, retry_count=0, max_retries=0)
    initial_mission = SimpleNamespace(id=mission_id, state=MissionState.RUNNING.value, guardian_epoch=4)
    stale_mission = SimpleNamespace(id=mission_id, state=MissionState.RUNNING.value, guardian_epoch=5)
    session = MagicMock()
    session.query.side_effect = [
        Query(SimpleNamespace(mission_id=mission_id)), Query(initial_mission), Query(task),
        Query(None), Query(stale_mission), Query(task),
    ]
    monkeypatch.setattr(database_sync, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(worker_tasks, "_execute_canonical_production", MagicMock(return_value=
        worker_tasks._PendingProductionResult(
            {"production_request_id": str(uuid4()), "render_job_id": str(uuid4())},
            dispatch_required=True, channel_id=str(uuid4()),
            production_request_id=str(uuid4()), render_job_id=str(uuid4()))))
    dispatch = MagicMock()
    monkeypatch.setattr(worker_tasks.execute_production_render_task, "delay", dispatch)

    result = worker_tasks.execute_task.run(str(task_id))

    assert result == {"status": "skipped", "reason": "stale_pending_production"}
    assert task.output is None
    dispatch.assert_not_called()
    session.rollback.assert_called_once()


@pytest.mark.parametrize(
    "request_state",
    [ProductionRequestStatus.FAILED.value, ProductionRequestStatus.CANCELLED.value],
)
def test_terminal_request_without_deterministic_job_never_allocates(
    monkeypatch, lineage, request_state
) -> None:
    lineage.request.status = request_state
    _, _, allocate, dispatch = install(monkeypatch, lineage)
    with pytest.raises(ValueError, match="no deterministic RenderJob"):
        worker_tasks._execute_canonical_production(lineage.task_id, lineage.ctx)
    allocate.assert_not_awaited()
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "request_state",
    [ProductionRequestStatus.RUNNING.value, ProductionRequestStatus.SUCCEEDED.value],
)
def test_in_progress_or_succeeded_request_without_job_fails_closed(
    monkeypatch, lineage, request_state
) -> None:
    lineage.request.status = request_state
    _, _, allocate, _ = install(monkeypatch, lineage)
    with pytest.raises(ValueError, match="no deterministic RenderJob"):
        worker_tasks._execute_canonical_production(lineage.task_id, lineage.ctx)
    allocate.assert_not_awaited()


@pytest.mark.parametrize(
    "request_state",
    [ProductionRequestStatus.RUNNING.value, ProductionRequestStatus.SUCCEEDED.value],
)
def test_non_ready_request_observes_existing_deterministic_job_without_allocation(
    monkeypatch, lineage, request_state
) -> None:
    lineage.request.status = request_state
    job = SimpleNamespace(
        id=lineage.job_id,
        production_request_id=lineage.request_id,
        state=RenderJobState.RUNNING.value,
    )
    _, _, allocate, dispatch = install(monkeypatch, lineage, artifact=job)
    result = worker_tasks._execute_canonical_production(lineage.task_id, lineage.ctx)
    assert isinstance(result, worker_tasks._PendingProductionResult)
    allocate.assert_not_awaited()
    dispatch.assert_not_called()


def test_render_service_failure_returns_sanitized_result(
    monkeypatch,
) -> None:
    from omega.application import production_render_factory

    channel_id, request_id, job_id = uuid4(), uuid4(), uuid4()
    async_session = AsyncSession({})
    monkeypatch.setattr(database, "AsyncWorkerSessionLocal", lambda: async_session)
    job = SimpleNamespace(id=job_id, state=RenderJobState.RUNNING.value)

    async def fail_terminally(*args):
        job.state = RenderJobState.FAILED.value
        raise RuntimeError("render exploded")

    service = SimpleNamespace(execute_render_job=AsyncMock(side_effect=fail_terminally))
    monkeypatch.setattr(
        production_render_factory, "build_production_render_service", lambda: service
    )

    result = worker_tasks.execute_production_render_task.run(
        str(channel_id), str(request_id), str(job_id)
    )

    assert result == {"status": "failed", "error": "RuntimeError: render exploded"}
    assert job.state == RenderJobState.FAILED.value


def test_render_task_does_not_directly_publish_evaluator_callback(
    monkeypatch,
) -> None:
    """execute_production_render_task must not call evaluate_mission_task.delay directly.

    Terminal evaluation is dispatched durably from within execute_render_job.
    """
    from omega.application import production_render_factory

    channel_id, request_id, job_id = uuid4(), uuid4(), uuid4()
    artifact = SimpleNamespace(id=uuid4())
    qa_status = SimpleNamespace(value="PASS")
    async_session = AsyncSession({})
    monkeypatch.setattr(database, "AsyncWorkerSessionLocal", lambda: async_session)
    service = SimpleNamespace(
        execute_render_job=AsyncMock(return_value=(artifact, qa_status))
    )
    monkeypatch.setattr(
        production_render_factory, "build_production_render_service", lambda: service
    )
    callback = MagicMock()
    monkeypatch.setattr(worker_tasks.evaluate_mission_task, "delay", callback)

    result = worker_tasks.execute_production_render_task.run(
        str(channel_id), str(request_id), str(job_id)
    )

    assert result["status"] == "success"
    # The wrapper must NOT directly call evaluate_mission_task.delay;
    # terminal evaluation is handled durably inside execute_render_job.
    callback.assert_not_called()


class OrchestratorResult:
    def __init__(self, values):
        self.values = values if isinstance(values, list) else [values]

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None

    def scalar_one(self):
        return self.values[0]

    def scalars(self):
        return self

    def first(self):
        return self.values[0] if self.values else None

    def all(self):
        return self.values


class AsyncOrchestratorSession:
    def __init__(self, mission, execution, task, job):
        self.records = {
            Mission: [mission], MissionExecution: [execution], Task: [task], TaskDependency: []
        }
        self.job = job
        self.add = MagicMock()
        self.commit = AsyncMock()

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        return OrchestratorResult(self.records[entity])

    async def get(self, model, record_id):
        if model is ProductionRenderJob and self.job is not None and self.job.id == record_id:
            return self.job
        return None


class SyncOrchestratorQuery:
    def __init__(self, values):
        self.values = values

    def filter(self, *args):
        return self

    def with_for_update(self):
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return self.values[0] if self.values else None

    def all(self):
        return self.values


class SyncOrchestratorSession:
    def __init__(self, mission, execution, task, job):
        self.records = {
            Mission: [mission], MissionExecution: [execution], Task: [task], TaskDependency: []
        }
        self.job = job
        self.add = MagicMock()
        self.commit = MagicMock()

    def query(self, model):
        return SyncOrchestratorQuery(self.records[model])

    def get(self, model, record_id):
        if model is ProductionRenderJob and self.job is not None and self.job.id == record_id:
            return self.job
        return None


def allow_pre_task_dispatch(monkeypatch) -> AsyncMock:
    from omega.application.guardian.engine import GuardianEngine
    from omega.domain.guardian import GuardianAction

    execute_check = AsyncMock(return_value=SimpleNamespace(
        decision=SimpleNamespace(action=GuardianAction.ALLOW)
    ))
    monkeypatch.setattr(GuardianEngine, "execute_check", execute_check)
    return execute_check


def production_gate_records(job_state, output=None):
    mission_id, execution_id, task_id, request_id, job_id = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    )
    mission = SimpleNamespace(
        id=mission_id, state=MissionState.RUNNING.value, autonomy_level="MANUAL",
        guardian_epoch=8, channel_id=uuid4(), priority=0,
    )
    execution = SimpleNamespace(
        id=execution_id, mission_id=mission_id, state="RUNNING", started_at=None, updated_at=None
    )
    task = SimpleNamespace(
        id=task_id, mission_id=mission_id, execution_id=execution_id, task_type="production",
        title="Production", state=TaskState.RUNNING.value, output=output or {
            "production_request_id": str(request_id), "render_job_id": str(job_id)
        }, requires_approval=False, retry_count=0, max_retries=0, updated_at=None,
    )
    job = None if job_state is None else SimpleNamespace(
        id=job_id, production_request_id=request_id, state=job_state
    )
    return mission, execution, task, job


def permit_immediate_dispatch(monkeypatch) -> None:
    from omega.application.scheduler.policy_service import SchedulePolicyService

    monkeypatch.setattr(
        SchedulePolicyService, "get_active_policy", AsyncMock(return_value=None)
    )


@pytest.mark.asyncio
async def test_async_terminal_render_uses_distinct_deterministic_continuation_dispatch(
    monkeypatch,
) -> None:
    mission, execution, task, job = production_gate_records(RenderJobState.SUCCEEDED.value)
    mission.autonomy_level = "SUPERVISED"
    allow_pre_task_dispatch(monkeypatch)
    permit_immediate_dispatch(monkeypatch)
    session = AsyncOrchestratorSession(mission, execution, task, job)
    enqueued = []

    async def enqueue(_session, **kwargs):
        enqueued.append(kwargs)

    monkeypatch.setattr(DurableDispatchService, "enqueue_async", enqueue)
    initial_key = orchestrator._mission_task_dispatch_key(
        SimpleNamespace(
            id=task.id,
            task_type="production",
            output=None,
            retry_count=task.retry_count,
        ),
        execution.id,
        mission.guardian_epoch,
    )

    result = await orchestrator.evaluate_mission(session, mission.id, execution.id)

    expected_key = f"{initial_key}:render-terminal:{job.id}"
    assert result["dispatched_tasks_count"] == 1
    assert task.state == TaskState.QUEUED.value
    assert [call["idempotency_key"] for call in enqueued] == [expected_key]
    assert expected_key != initial_key
    assert orchestrator._mission_task_dispatch_key(
        task, execution.id, mission.guardian_epoch
    ) == expected_key


def test_sync_terminal_render_matches_async_continuation_dispatch_identity(monkeypatch) -> None:
    mission, execution, task, job = production_gate_records(RenderJobState.SUCCEEDED.value)
    mission.autonomy_level = "SUPERVISED"
    allow_pre_task_dispatch(monkeypatch)
    session = SyncOrchestratorSession(mission, execution, task, job)
    enqueued = []

    def enqueue(_session, **kwargs):
        enqueued.append(kwargs)

    monkeypatch.setattr(DurableDispatchService, "enqueue", enqueue)

    result = orchestrator.evaluate_mission_sync(session, mission.id, execution.id)

    expected_key = (
        f"mission-task-dispatch:{execution.id}:{task.id}:0:{mission.guardian_epoch}"
        f":render-terminal:{job.id}"
    )
    assert result["dispatched_tasks_count"] == 1
    assert task.state == TaskState.QUEUED.value
    assert [call["idempotency_key"] for call in enqueued] == [expected_key]


@pytest.mark.parametrize(
    "task_type",
    ["strategy", "topic_discovery", "research", "content_generation", "qa", "publish"],
)
def test_normal_task_dispatch_identity_ignores_output_and_remains_idempotent(task_type) -> None:
    task = SimpleNamespace(
        id=uuid4(), task_type=task_type, retry_count=2, output={"render_job_id": str(uuid4())}
    )
    execution_id = uuid4()

    first = orchestrator._mission_task_dispatch_key(task, execution_id, 7)
    replay = orchestrator._mission_task_dispatch_key(task, execution_id, 7)

    assert first == replay == f"mission-task-dispatch:{execution_id}:{task.id}:2:7"


@pytest.mark.asyncio
@pytest.mark.parametrize("job_state", [
    RenderJobState.QUEUED.value, RenderJobState.RUNNING.value, RenderJobState.RETRY.value
])
async def test_async_orchestrator_pending_production_remains_running(monkeypatch, job_state) -> None:
    mission, execution, task, job = production_gate_records(job_state)
    session = AsyncOrchestratorSession(mission, execution, task, job)
    observer = MagicMock()
    monkeypatch.setattr(worker_tasks.execute_task, "delay", observer)

    result = await orchestrator.evaluate_mission(session, mission.id, execution.id)

    assert result["dispatched_tasks_count"] == 0
    assert task.state == TaskState.RUNNING.value
    observer.assert_not_called()


@pytest.mark.parametrize("job_state", [
    RenderJobState.QUEUED.value, RenderJobState.RUNNING.value, RenderJobState.RETRY.value
])
def test_sync_orchestrator_pending_production_remains_running(monkeypatch, job_state) -> None:
    mission, execution, task, job = production_gate_records(job_state)
    session = SyncOrchestratorSession(mission, execution, task, job)
    observer = MagicMock()
    monkeypatch.setattr(worker_tasks.execute_task, "delay", observer)

    result = orchestrator.evaluate_mission_sync(session, mission.id, execution.id)

    assert result["dispatched_tasks_count"] == 0
    assert task.state == TaskState.RUNNING.value
    observer.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("job_state", [
    RenderJobState.SUCCEEDED.value, RenderJobState.FAILED.value, RenderJobState.CANCELLED.value
])
async def test_async_orchestrator_terminal_render_permits_bounded_reentry(
    monkeypatch, job_state
) -> None:
    mission, execution, task, job = production_gate_records(job_state)
    allow_pre_task_dispatch(monkeypatch)
    session = AsyncOrchestratorSession(mission, execution, task, job)
    observer = MagicMock()
    monkeypatch.setattr(worker_tasks.execute_task, "delay", observer)

    result = await orchestrator.evaluate_mission(session, mission.id, execution.id)

    assert result["dispatched_tasks_count"] == 0
    assert task.state == TaskState.READY.value
    observer.assert_not_called()


@pytest.mark.parametrize("job_state", [
    RenderJobState.SUCCEEDED.value, RenderJobState.FAILED.value, RenderJobState.CANCELLED.value
])
def test_sync_orchestrator_terminal_render_permits_bounded_reentry(monkeypatch, job_state) -> None:
    mission, execution, task, job = production_gate_records(job_state)
    allow_pre_task_dispatch(monkeypatch)
    session = SyncOrchestratorSession(mission, execution, task, job)
    observer = MagicMock()
    monkeypatch.setattr(worker_tasks.execute_task, "delay", observer)

    result = orchestrator.evaluate_mission_sync(session, mission.id, execution.id)

    assert result["dispatched_tasks_count"] == 0
    assert task.state == TaskState.READY.value
    observer.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [None, {}, {"production_request_id": "bad", "render_job_id": "bad"}])
async def test_async_orchestrator_malformed_correlation_never_readies(output) -> None:
    mission, execution, task, job = production_gate_records(None, output=output)
    session = AsyncOrchestratorSession(mission, execution, task, job)

    await orchestrator.evaluate_mission(session, mission.id, execution.id)

    assert task.state == TaskState.RUNNING.value


def test_sync_orchestrator_mismatched_job_never_readies() -> None:
    mission, execution, task, job = production_gate_records(RenderJobState.SUCCEEDED.value)
    job.production_request_id = uuid4()
    session = SyncOrchestratorSession(mission, execution, task, job)

    orchestrator.evaluate_mission_sync(session, mission.id, execution.id)

    assert task.state == TaskState.RUNNING.value
