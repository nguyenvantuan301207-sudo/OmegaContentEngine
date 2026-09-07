"""Unit tests for TaskExecutor abstractions and TaskExecutorRegistry."""

from __future__ import annotations

import uuid

from omega.application.executor import (
    ContentEngineExecutor,
    GenericWorkflowExecutor,
    MissionQAExecutor,
    NoopExecutor,
    ProductionEngineExecutor,
    PublishExecutor,
    ResearchEngineExecutor,
    StrategyExecutor,
    TaskExecutorRegistry,
    TestExecutor,
    TopicIntelligenceExecutor,
)
from omega.application.planner import StaticMissionPlanner


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


def test_static_mission_planner_stages_have_explicit_executors() -> None:
    """Every canonical planner stage should resolve without the generic fallback."""
    plan = StaticMissionPlanner().plan("Mission", "Objective", "AUTONOMOUS")
    registry = TaskExecutorRegistry()
    expected_types = {
        "strategy": StrategyExecutor,
        "topic_discovery": TopicIntelligenceExecutor,
        "research": ResearchEngineExecutor,
        "content_generation": ContentEngineExecutor,
        "production": ProductionEngineExecutor,
        "qa": MissionQAExecutor,
        "publish": PublishExecutor,
    }

    planned_types = [task.task_create.task_type for task in plan.tasks]
    assert planned_types == list(expected_types)
    for task_type, expected_executor_type in expected_types.items():
        executor = registry.get(task_type)
        assert isinstance(executor, expected_executor_type)
        assert not isinstance(executor, GenericWorkflowExecutor)


def test_legacy_executor_registrations_still_resolve() -> None:
    """Existing legacy task names should retain their specialized routing."""
    registry = TaskExecutorRegistry()
    expected_types = {
        "system.noop": NoopExecutor,
        "system.test": TestExecutor,
        "topic.evaluate": TopicIntelligenceExecutor,
        "topic.recommend": TopicIntelligenceExecutor,
        "research.collect": ResearchEngineExecutor,
        "research.analyze": ResearchEngineExecutor,
        "research.synthesize": ResearchEngineExecutor,
        "research.execute": ResearchEngineExecutor,
        "content.prepare": ContentEngineExecutor,
        "content.generate": ContentEngineExecutor,
        "content.qa": ContentEngineExecutor,
        "content.execute": ContentEngineExecutor,
        "production.prepare": ProductionEngineExecutor,
        "production.render": ProductionEngineExecutor,
        "production.qa": ProductionEngineExecutor,
        "production.execute": ProductionEngineExecutor,
    }

    for task_type, expected_executor_type in expected_types.items():
        assert isinstance(registry.get(task_type), expected_executor_type)


def test_unknown_task_type_retains_deterministic_generic_fallback() -> None:
    """Unknown tasks should retain the repository-compatible placeholder behavior."""
    registry = TaskExecutorRegistry()
    executor = registry.get("some.future.unregistered.type")
    task_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    assert isinstance(executor, GenericWorkflowExecutor)
    assert executor.execute(
        task_id=task_id,
        task_type="some.future.unregistered.type",
        task_input={},
        context={},
    ) == {
        "stage": "some.future.unregistered.type",
        "status": "success",
        "task_id": str(task_id),
        "summary": (
            "Placeholder execution for stage "
            "'some.future.unregistered.type' completed successfully."
        ),
    }
