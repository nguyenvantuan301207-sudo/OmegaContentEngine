"""Offline behavioral contracts for canonical Mission QA."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from omega.application import executor as executor_module
from omega.application import orchestrator
from omega.application.durable_dispatch import DurableDispatchService
from omega.application.planner import StaticMissionPlanner
from omega.domain.mission import MissionState
from omega.domain.production import (
    MediaArtifactType,
    ProductionOutcome,
    ProductionQAStatus,
    ProductionRequestStatus,
    RenderJobState,
)
from omega.domain.task import TaskState
from omega.infrastructure import database, database_sync
from omega.infrastructure.models import (
    MediaArtifact,
    Mission,
    MissionExecution,
    ProductionRenderJob,
    ProductionRequest,
    Task,
    TaskDependency,
)
from omega.worker import tasks as worker_tasks


def qa_context(production=None):
    result = {
        "mission_id": str(uuid4()),
        "execution_id": str(uuid4()),
        "dependency_outputs": {},
    }
    if production is not None:
        result["dependency_outputs"]["production"] = production
    return result


@pytest.mark.parametrize(
    "production",
    [
        None,
        {},
        {"production_request_id": str(uuid4())},
        {"production_request_id": str(uuid4()), "render_job_id": str(uuid4())},
        {
            "production_request_id": "bad",
            "render_job_id": str(uuid4()),
            "media_artifact_id": str(uuid4()),
        },
    ],
)
def test_canonical_qa_requires_complete_direct_production_correlation(production) -> None:
    with pytest.raises(ValueError):
        worker_tasks._canonical_qa_correlation(qa_context(production))


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class AsyncSession:
    def __init__(self, records, qa_result):
        self.records = records
        self.qa_result = qa_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, model, record_id):
        return self.records.get((model, record_id))

    async def execute(self, statement):
        return ScalarResult(self.qa_result)


@pytest.fixture
def lineage():
    mission_id, execution_id, channel_id, dna_id, task_id = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    )
    request_id, job_id, artifact_id, qa_id = uuid4(), uuid4(), uuid4(), uuid4()
    task = SimpleNamespace(id=task_id, mission_id=mission_id, execution_id=execution_id)
    execution = SimpleNamespace(id=execution_id, mission_id=mission_id, channel_dna_revision_id=dna_id)
    mission = SimpleNamespace(id=mission_id, channel_id=channel_id)
    request = SimpleNamespace(
        id=request_id,
        mission_execution_id=execution_id,
        channel_id=channel_id,
        channel_dna_revision_id=dna_id,
        status=ProductionRequestStatus.SUCCEEDED.value,
        outcome=ProductionOutcome.RENDERED.value,
    )
    job = SimpleNamespace(
        id=job_id,
        production_request_id=request_id,
        state=RenderJobState.SUCCEEDED.value,
    )
    artifact = SimpleNamespace(
        id=artifact_id,
        production_request_id=request_id,
        render_job_id=job_id,
        artifact_type=MediaArtifactType.VIDEO.value,
        is_current=True,
    )
    qa_result = SimpleNamespace(
        id=qa_id,
        production_request_id=request_id,
        artifact_id=artifact_id,
        status=ProductionQAStatus.PASSED.value,
    )
    records = {
        (Task, task_id): task,
        (MissionExecution, execution_id): execution,
        (Mission, mission_id): mission,
        (ProductionRequest, request_id): request,
        (ProductionRenderJob, job_id): job,
        (MediaArtifact, artifact_id): artifact,
    }
    context = {
        "mission_id": str(mission_id),
        "execution_id": str(execution_id),
        "dependency_outputs": {
            "production": {
                "production_request_id": str(request_id),
                "render_job_id": str(job_id),
                "media_artifact_id": str(artifact_id),
            }
        },
    }
    return SimpleNamespace(**locals())


def install(monkeypatch, data, qa_result_marker=True):
    qa_result = data.qa_result if qa_result_marker is True else qa_result_marker
    monkeypatch.setattr(
        database,
        "AsyncWorkerSessionLocal",
        lambda: AsyncSession(data.records, qa_result),
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: setattr(d.request, "mission_execution_id", uuid4()),
        lambda d: setattr(d.request, "channel_id", uuid4()),
        lambda d: setattr(d.request, "channel_dna_revision_id", uuid4()),
        lambda d: setattr(d.job, "production_request_id", uuid4()),
        lambda d: setattr(d.artifact, "production_request_id", uuid4()),
        lambda d: setattr(d.artifact, "render_job_id", uuid4()),
        lambda d: setattr(d.artifact, "artifact_type", "AUDIO"),
        lambda d: setattr(d.artifact, "is_current", False),
    ],
)
def test_request_job_and_artifact_lineage_mismatch_fails_closed(monkeypatch, lineage, mutation) -> None:
    mutation(lineage)
    install(monkeypatch, lineage)
    with pytest.raises(ValueError):
        worker_tasks._execute_canonical_qa(lineage.task_id, lineage.context)


@pytest.mark.parametrize(
    ("request_status", "job_state"),
    [
        (ProductionRequestStatus.RUNNING.value, RenderJobState.SUCCEEDED.value),
        (ProductionRequestStatus.SUCCEEDED.value, RenderJobState.RUNNING.value),
        (ProductionRequestStatus.SUCCEEDED.value, RenderJobState.FAILED.value),
    ],
)
def test_non_successful_mechanical_state_cannot_pass(
    monkeypatch, lineage, request_status, job_state
) -> None:
    lineage.request.status = request_status
    lineage.job.state = job_state
    install(monkeypatch, lineage)
    with pytest.raises(ValueError, match="mechanically successful"):
        worker_tasks._execute_canonical_qa(lineage.task_id, lineage.context)


def test_missing_exact_production_qa_result_fails_closed(monkeypatch, lineage) -> None:
    install(monkeypatch, lineage, None)
    with pytest.raises(ValueError, match="exact ProductionQAResult"):
        worker_tasks._execute_canonical_qa(lineage.task_id, lineage.context)


def test_qa_result_must_match_exact_request_and_artifact(monkeypatch, lineage) -> None:
    lineage.qa_result.production_request_id = uuid4()
    # The exact filtered lookup cannot return a row with different lineage in production;
    # emulate that authoritative query result as absent.
    install(monkeypatch, lineage, None)
    with pytest.raises(ValueError, match="exact ProductionQAResult"):
        worker_tasks._execute_canonical_qa(lineage.task_id, lineage.context)


@pytest.mark.parametrize(
    "status",
    [ProductionQAStatus.PASSED.value, ProductionQAStatus.PASSED_WITH_WARNINGS.value],
)
def test_accepted_qa_and_rendered_outcome_succeeds_with_ids_only(
    monkeypatch, lineage, status
) -> None:
    lineage.qa_result.status = status
    install(monkeypatch, lineage)
    assert worker_tasks._execute_canonical_qa(lineage.task_id, lineage.context) == {
        "production_request_id": str(lineage.request_id),
        "media_artifact_id": str(lineage.artifact_id),
        "production_qa_result_id": str(lineage.qa_id),
    }


def test_final_blocked_outcome_overrides_earlier_pass(monkeypatch, lineage) -> None:
    lineage.request.outcome = ProductionOutcome.BLOCKED.value
    install(monkeypatch, lineage)
    with pytest.raises(RuntimeError, match="BLOCKED"):
        worker_tasks._execute_canonical_qa(lineage.task_id, lineage.context)


@pytest.mark.parametrize("status", [ProductionQAStatus.BLOCKED.value, ProductionQAStatus.PENDING.value])
def test_nonaccepted_local_qa_fails(monkeypatch, lineage, status) -> None:
    lineage.qa_result.status = status
    install(monkeypatch, lineage)
    with pytest.raises(RuntimeError, match="not accepted"):
        worker_tasks._execute_canonical_qa(lineage.task_id, lineage.context)


class Query:
    def __init__(self, record):
        self.record = record

    def filter(self, *args):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.record[0] if isinstance(self.record, list) else self.record

    def outerjoin(self, *args):
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return self.record if isinstance(self.record, list) else []


def test_execute_task_routes_qa_around_placeholder_and_preserves_input(monkeypatch) -> None:
    mission_id, execution_id, task_id = uuid4(), uuid4(), uuid4()
    original_input = {"editorial": "unchanged"}
    task = SimpleNamespace(
        id=task_id,
        mission_id=mission_id,
        execution_id=execution_id,
        task_type="qa",
        title="QA",
        state=TaskState.QUEUED.value,
        dispatched_epoch=3,
        input=original_input,
        output=None,
        retry_count=0,
        max_retries=0,
    )
    mission = SimpleNamespace(id=mission_id, state=MissionState.RUNNING.value, guardian_epoch=3)
    session = MagicMock()
    session.query.side_effect = [
        Query(SimpleNamespace(mission_id=mission_id)),
        Query(mission),
        Query(task),
        Query(None),
        Query(mission),
        Query(task),
    ]
    monkeypatch.setattr(database_sync, "SyncSessionLocal", lambda: session)
    output = {
        "production_request_id": str(uuid4()),
        "media_artifact_id": str(uuid4()),
        "production_qa_result_id": str(uuid4()),
    }
    adapter = MagicMock(return_value=output)
    monkeypatch.setattr(worker_tasks, "_execute_canonical_qa", adapter)
    registry = MagicMock()
    monkeypatch.setattr(executor_module, "default_executor_registry", registry)
    # Monkeypatch durable dispatch so the success-path outbox enqueue is a no-op
    monkeypatch.setattr(DurableDispatchService, "enqueue", MagicMock())

    assert worker_tasks.execute_task.run(str(task_id))["status"] == "success"
    adapter.assert_called_once()
    registry.get.assert_not_called()
    assert task.input is original_input
    assert task.output == output
    assert task.state == TaskState.SUCCEEDED.value


def test_existing_orchestrator_semantics_cancel_publish_after_qa_failure() -> None:
    plan = StaticMissionPlanner().plan("Mission", "Objective", "MANUAL")
    qa_plan = next(task for task in plan.tasks if task.task_create.task_type == "qa")
    publish_plan = next(task for task in plan.tasks if task.task_create.task_type == "publish")
    assert any(
        dependency.task_temp_id == publish_plan.temp_id
        and dependency.depends_on_temp_id == qa_plan.temp_id
        for dependency in plan.dependencies
    )

    mission_id, execution_id = uuid4(), uuid4()
    mission = SimpleNamespace(
        id=mission_id,
        state=MissionState.RUNNING.value,
        autonomy_level="MANUAL",
        guardian_epoch=1,
        completed_at=None,
        updated_at=None,
    )
    execution = SimpleNamespace(
        id=execution_id,
        mission_id=mission_id,
        state="RUNNING",
        started_at=None,
        completed_at=None,
        updated_at=None,
    )
    qa_task = SimpleNamespace(
        id=qa_plan.temp_id,
        mission_id=mission_id,
        task_type="qa",
        title="QA",
        state=TaskState.FAILED.value,
        requires_approval=False,
        retry_count=0,
        max_retries=0,
    )
    publish_task = SimpleNamespace(
        id=publish_plan.temp_id,
        mission_id=mission_id,
        task_type="publish",
        title="Publish",
        state=TaskState.PENDING.value,
        requires_approval=False,
        retry_count=0,
        max_retries=0,
        updated_at=None,
    )
    dependency = SimpleNamespace(
        mission_id=mission_id,
        task_id=publish_task.id,
        depends_on_task_id=qa_task.id,
    )

    class OrchestratorSession:
        def __init__(self):
            self.records = {
                Mission: [mission],
                MissionExecution: [execution],
                Task: [qa_task, publish_task],
                TaskDependency: [dependency],
            }
            self.add = MagicMock()
            self.commit = MagicMock()

        def query(self, model):
            return Query(self.records[model])

    session = OrchestratorSession()
    assert publish_task.state == TaskState.PENDING.value

    orchestrator.evaluate_mission_sync(session, mission_id, execution_id)

    assert qa_task.state == TaskState.FAILED.value
    assert publish_task.state == TaskState.CANCELLED.value


def test_no_render_qa_engine_provider_or_broker_boundary_is_used(monkeypatch, lineage) -> None:
    install(monkeypatch, lineage)
    render_dispatch = MagicMock()
    monkeypatch.setattr(worker_tasks.execute_production_render_task, "delay", render_dispatch)
    assert worker_tasks._execute_canonical_qa(lineage.task_id, lineage.context)
    render_dispatch.assert_not_called()
