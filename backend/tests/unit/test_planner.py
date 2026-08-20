"""Unit tests for StaticMissionPlanner DAG generation."""

from __future__ import annotations

from omega.application.planner import StaticMissionPlanner
from omega.domain.dag import validate_dag


def test_static_mission_planner_generates_valid_dag() -> None:
    """StaticMissionPlanner should generate 7 connected stages forming a valid acyclic DAG."""
    planner = StaticMissionPlanner()
    plan = planner.plan(
        mission_title="Tech Innovation Brief",
        mission_objective="Analyze quantum computing breakthroughs",
        autonomy_level="SUPERVISED",
    )

    assert len(plan.tasks) == 7
    assert len(plan.dependencies) == 6

    # Verify task types sequence
    task_types = [t.task_create.task_type for t in plan.tasks]
    assert task_types == [
        "strategy",
        "topic_discovery",
        "research",
        "content_generation",
        "production",
        "qa",
        "publish",
    ]

    # Verify QA requires approval under SUPERVISED
    qa_task = next(t for t in plan.tasks if t.task_create.task_type == "qa")
    assert qa_task.task_create.requires_approval is True

    # Validate output plan DAG directly
    task_id_set = {t.temp_id for t in plan.tasks}
    dep_tuples = [(d.task_temp_id, d.depends_on_temp_id) for d in plan.dependencies]
    order = validate_dag(task_id_set, dep_tuples)
    assert len(order) == 7
