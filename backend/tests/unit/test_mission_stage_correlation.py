"""Offline contracts for Mission direct-dependency output hydration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from omega.application import executor as executor_module
from omega.application.durable_dispatch import DurableDispatchService
from omega.domain.mission import MissionState
from omega.domain.task import TaskState
from omega.infrastructure import database_sync
from omega.worker import tasks as worker_tasks
from omega.worker.tasks import _load_dependency_outputs


class DependencyQuery:
    """Minimal synchronous query double for dependency hydration."""

    def __init__(self, rows):
        self.rows = rows

    def outerjoin(self, *args):
        return self

    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return self.rows


class Session:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *entities):
        return DependencyQuery(self.rows)


class RecordQuery:
    """Chainable query double returning one configured record."""

    def __init__(self, record):
        self.record = record

    def filter(self, *args):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.record


def make_task(
    *,
    mission_id=None,
    execution_id=None,
    task_type="research",
    state=TaskState.SUCCEEDED.value,
    output=None,
    task_input=None,
):
    return SimpleNamespace(
        id=uuid4(),
        mission_id=mission_id or uuid4(),
        execution_id=execution_id or uuid4(),
        task_type=task_type,
        state=state,
        output={"correlation_id": "canonical"} if output is None else output,
        input={"request": "unchanged"} if task_input is None else task_input,
    )


def make_dependency(task, upstream, *, mission_id=None):
    return SimpleNamespace(
        id=uuid4(),
        task_id=task.id,
        depends_on_task_id=upstream.id if upstream else uuid4(),
        mission_id=mission_id or task.mission_id,
    )


def test_execute_task_supplies_dependency_outputs_without_replacing_task_input(
    monkeypatch,
) -> None:
    original_input = {"request": {"topic": "persisted-current-task-input"}}
    task = make_task(
        task_type="production",
        state=TaskState.QUEUED.value,
        task_input=original_input,
    )
    task.title = "Production"
    task.dispatched_epoch = 7
    task.retry_count = 0
    task.max_retries = 0
    upstream_output = {
        "content_request_id": "content-request-1",
        "script_version_id": "script-version-1",
    }
    upstream = make_task(
        mission_id=task.mission_id,
        execution_id=task.execution_id,
        task_type="content_generation",
        output=upstream_output,
    )
    mission = SimpleNamespace(
        id=task.mission_id,
        state=MissionState.RUNNING.value,
        guardian_epoch=7,
    )
    session = MagicMock()
    session.query.side_effect = [
        RecordQuery(SimpleNamespace(mission_id=task.mission_id)),
        RecordQuery(mission),
        RecordQuery(task),
        DependencyQuery([(make_dependency(task, upstream), upstream)]),
        RecordQuery(mission),
        RecordQuery(task),
    ]

    # Capture the context passed to the canonical production adapter.
    received_context = {}
    received_task_input = {}

    def fake_canonical_production(task_id, context):
        received_context.update(context)
        received_task_input["value"] = task.input
        # Return a plain dict (not _PendingProductionResult) so execute_task
        # takes the SUCCEEDED path without calling DurableDispatchService for render.
        return {"production_request_id": "production-request-1"}

    monkeypatch.setattr(worker_tasks, "_execute_canonical_production", fake_canonical_production)
    monkeypatch.setattr(database_sync, "SyncSessionLocal", lambda: session)
    # Monkeypatch durable dispatch so the success-path outbox enqueue is a no-op
    monkeypatch.setattr(DurableDispatchService, "enqueue", MagicMock())
    registry = MagicMock()
    monkeypatch.setattr(executor_module, "default_executor_registry", registry)

    result = worker_tasks.execute_task.run(str(task.id))

    assert result == {"status": "success", "task_id": str(task.id)}
    # canonical production adapter was used (not the registry)
    registry.get.assert_not_called()
    assert received_context["dependency_outputs"] == {
        "content_generation": upstream_output
    }
    assert received_task_input["value"] is original_input
    assert received_task_input["value"] == {
        "request": {"topic": "persisted-current-task-input"}
    }
    assert task.input is original_input
    assert task.input == {"request": {"topic": "persisted-current-task-input"}}


def test_one_successful_dependency_output_is_available_for_context() -> None:
    task = make_task(task_type="content_generation")
    upstream = make_task(
        mission_id=task.mission_id,
        execution_id=task.execution_id,
        task_type="research",
        output={"research_request_id": "request-1"},
    )

    dependency_outputs = _load_dependency_outputs(
        Session([(make_dependency(task, upstream), upstream)]), task
    )

    assert dependency_outputs == {"research": {"research_request_id": "request-1"}}


def test_multiple_dependencies_are_task_type_keyed_in_query_order() -> None:
    task = make_task(task_type="production")
    content = make_task(
        mission_id=task.mission_id,
        execution_id=task.execution_id,
        task_type="content_generation",
        output={"content_request_id": "content-1"},
    )
    research = make_task(
        mission_id=task.mission_id,
        execution_id=task.execution_id,
        task_type="research",
        output={"research_request_id": "research-1"},
    )

    outputs = _load_dependency_outputs(
        Session(
            [
                (make_dependency(task, content), content),
                (make_dependency(task, research), research),
            ]
        ),
        task,
    )

    assert list(outputs) == ["content_generation", "research"]
    assert outputs == {
        "content_generation": {"content_request_id": "content-1"},
        "research": {"research_request_id": "research-1"},
    }


def test_cross_mission_dependency_fails_closed() -> None:
    task = make_task()
    upstream = make_task(execution_id=task.execution_id)

    with pytest.raises(RuntimeError, match="different mission"):
        _load_dependency_outputs(Session([(make_dependency(task, upstream), upstream)]), task)


def test_cross_execution_dependency_fails_closed() -> None:
    task = make_task()
    upstream = make_task(mission_id=task.mission_id)

    with pytest.raises(RuntimeError, match="different mission execution"):
        _load_dependency_outputs(Session([(make_dependency(task, upstream), upstream)]), task)


def test_non_succeeded_dependency_fails_closed() -> None:
    task = make_task()
    upstream = make_task(
        mission_id=task.mission_id,
        execution_id=task.execution_id,
        state=TaskState.RUNNING.value,
    )

    with pytest.raises(RuntimeError, match="not SUCCEEDED"):
        _load_dependency_outputs(Session([(make_dependency(task, upstream), upstream)]), task)


@pytest.mark.parametrize("output", [None, [], "invalid"])
def test_malformed_dependency_output_fails_closed(output) -> None:
    task = make_task()
    upstream = make_task(
        mission_id=task.mission_id,
        execution_id=task.execution_id,
        output={"temporary": True},
    )
    upstream.output = output

    with pytest.raises(RuntimeError, match="output is malformed"):
        _load_dependency_outputs(Session([(make_dependency(task, upstream), upstream)]), task)


def test_missing_upstream_task_fails_closed() -> None:
    task = make_task()

    with pytest.raises(RuntimeError, match="missing upstream task"):
        _load_dependency_outputs(Session([(make_dependency(task, None), None)]), task)


def test_duplicate_dependency_task_type_fails_closed() -> None:
    task = make_task()
    upstream_one = make_task(mission_id=task.mission_id, execution_id=task.execution_id)
    upstream_two = make_task(mission_id=task.mission_id, execution_id=task.execution_id)

    with pytest.raises(RuntimeError, match="task_type is ambiguous"):
        _load_dependency_outputs(
            Session(
                [
                    (make_dependency(task, upstream_one), upstream_one),
                    (make_dependency(task, upstream_two), upstream_two),
                ]
            ),
            task,
        )


def test_no_dependency_preserves_empty_context_mapping_and_task_input() -> None:
    original_input = {"request": {"topic": "unchanged"}}
    task = make_task(task_input=original_input)

    outputs = _load_dependency_outputs(Session([]), task)

    assert outputs == {}
    assert task.input is original_input
    assert task.input == {"request": {"topic": "unchanged"}}
