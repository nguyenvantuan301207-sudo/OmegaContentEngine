"""DAG (Directed Acyclic Graph) domain validation.

Ensures that mission task dependency graphs are valid, acyclic, and sound.
Zero infrastructure dependencies.
"""

from __future__ import annotations

from collections import defaultdict, deque
from uuid import UUID


class DAGValidationError(Exception):
    """Raised when a task dependency graph violates DAG constraints."""

    pass


def validate_dag(
    task_ids: set[UUID] | list[UUID],
    dependencies: list[
        tuple[UUID, UUID]
    ],  # (task_id, depends_on_task_id) -> task_id depends on depends_on_task_id
) -> list[UUID]:
    """Validate task dependency graph and return a topological ordering.

    Args:
        task_ids: All task UUIDs in the mission DAG.
        dependencies: Pairs of (task_id, depends_on_task_id), meaning task_id
                      cannot run until depends_on_task_id has succeeded.

    Raises:
        DAGValidationError: If self-dependency, duplicate, unknown task, or cycle is detected.

    Returns:
        A valid topological ordering of task IDs (dependencies first).
    """
    task_set = set(task_ids)
    if not task_set:
        return []

    # 1. Check for unknown tasks, self-dependencies, and duplicates
    seen_deps: set[tuple[UUID, UUID]] = set()
    for task_id, depends_on in dependencies:
        if task_id not in task_set:
            raise DAGValidationError(f"Dependency references unknown task_id: {task_id}")
        if depends_on not in task_set:
            raise DAGValidationError(
                f"Dependency references unknown depends_on_task_id: {depends_on}"
            )
        if task_id == depends_on:
            raise DAGValidationError(f"Task cannot depend on itself: {task_id}")

        pair = (task_id, depends_on)
        if pair in seen_deps:
            raise DAGValidationError(f"Duplicate dependency detected: {task_id} -> {depends_on}")
        seen_deps.add(pair)

    # 2. Build adjacency list and in-degree map
    # Edge: depends_on -> task_id (depends_on must finish before task_id can start)
    adjacency: dict[UUID, list[UUID]] = defaultdict(list)
    in_degree: dict[UUID, int] = {tid: 0 for tid in task_set}

    for task_id, depends_on in dependencies:
        adjacency[depends_on].append(task_id)
        in_degree[task_id] += 1

    # 3. Kahn's Algorithm for Topological Sort & Cycle Detection
    queue: deque[UUID] = deque([tid for tid in task_set if in_degree[tid] == 0])
    topological_order: list[UUID] = []

    while queue:
        current = queue.popleft()
        topological_order.append(current)

        for downstream in adjacency[current]:
            in_degree[downstream] -= 1
            if in_degree[downstream] == 0:
                queue.append(downstream)

    # 4. If not all tasks are sorted, a cycle exists
    if len(topological_order) != len(task_set):
        remaining_tasks = [tid for tid, deg in in_degree.items() if deg > 0]
        raise DAGValidationError(
            f"Cycle detected in task dependencies among tasks: {remaining_tasks}"
        )

    return topological_order
