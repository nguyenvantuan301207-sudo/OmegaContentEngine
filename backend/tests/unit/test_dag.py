"""Unit tests for DAG validation and cycle detection."""

from __future__ import annotations

import uuid

import pytest

from omega.domain.dag import DAGValidationError, validate_dag


def test_valid_linear_dag() -> None:
    """A -> B -> C linear DAG should return topological sort in order."""
    task_a = uuid.uuid4()
    task_b = uuid.uuid4()
    task_c = uuid.uuid4()

    tasks = [task_a, task_b, task_c]
    deps = [
        (task_b, task_a),  # B depends on A
        (task_c, task_b),  # C depends on B
    ]

    order = validate_dag(tasks, deps)
    assert order == [task_a, task_b, task_c]


def test_valid_diamond_dag() -> None:
    """A -> (B, C) -> D diamond DAG should validate and put A first, D last."""
    task_a = uuid.uuid4()
    task_b = uuid.uuid4()
    task_c = uuid.uuid4()
    task_d = uuid.uuid4()

    tasks = [task_a, task_b, task_c, task_d]
    deps = [
        (task_b, task_a),
        (task_c, task_a),
        (task_d, task_b),
        (task_d, task_c),
    ]

    order = validate_dag(tasks, deps)
    assert order[0] == task_a
    assert order[-1] == task_d
    assert set(order[1:3]) == {task_b, task_c}


def test_dag_cycle_detection() -> None:
    """A -> B -> C -> A cycle should raise DAGValidationError."""
    task_a = uuid.uuid4()
    task_b = uuid.uuid4()
    task_c = uuid.uuid4()

    tasks = [task_a, task_b, task_c]
    deps = [
        (task_b, task_a),
        (task_c, task_b),
        (task_a, task_c),  # Cycle!
    ]

    with pytest.raises(DAGValidationError, match="Cycle detected"):
        validate_dag(tasks, deps)


def test_dag_self_dependency() -> None:
    """Task depending on itself should raise DAGValidationError."""
    task_a = uuid.uuid4()

    with pytest.raises(DAGValidationError, match="cannot depend on itself"):
        validate_dag([task_a], [(task_a, task_a)])


def test_dag_duplicate_dependency() -> None:
    """Duplicate dependency edges should raise DAGValidationError."""
    task_a = uuid.uuid4()
    task_b = uuid.uuid4()

    deps = [(task_b, task_a), (task_b, task_a)]
    with pytest.raises(DAGValidationError, match="Duplicate dependency"):
        validate_dag([task_a, task_b], deps)


def test_dag_unknown_task_reference() -> None:
    """Referencing a non-existent task in dependencies should raise DAGValidationError."""
    task_a = uuid.uuid4()
    unknown_task = uuid.uuid4()

    with pytest.raises(DAGValidationError, match="unknown task_id"):
        validate_dag([task_a], [(unknown_task, task_a)])
