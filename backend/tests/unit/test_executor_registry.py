"""Unit tests for TaskExecutor abstractions and TaskExecutorRegistry."""

from __future__ import annotations

import uuid

from omega.application.executor import (
    NoopExecutor,
    TaskExecutorRegistry,
    TestExecutor,
)


def test_noop_executor() -> None:
    """NoopExecutor should return immediate success with task_id."""
    executor = NoopExecutor()
    tid = uuid.uuid4()
    out = executor.execute(task_id=tid, task_type="system.noop", task_input={}, context={})
    assert out["status"] == "noop_completed"
    assert out["task_id"] == str(tid)


def test_test_executor() -> None:
    """TestExecutor should return success echo."""
    executor = TestExecutor()
    tid = uuid.uuid4()
    out = executor.execute(
        task_id=tid,
        task_type="system.test",
        task_input={"foo": "bar"},
        context={},
    )
    assert out["status"] == "test_completed"
    assert out["input_received"] == {"foo": "bar"}


def test_executor_registry_lookup_and_fallback() -> None:
    """Registry should resolve registered executors and fallback to generic executor."""
    registry = TaskExecutorRegistry()
    assert isinstance(registry.get("system.noop"), NoopExecutor)
    assert isinstance(registry.get("system.test"), TestExecutor)

    # Unknown type should fallback to generic workflow executor safely
    generic = registry.get("some.future.unregistered.type")
    tid = uuid.uuid4()
    res = generic.execute(task_id=tid, task_type="strategy", task_input={}, context={})
    assert res["status"] == "success"
