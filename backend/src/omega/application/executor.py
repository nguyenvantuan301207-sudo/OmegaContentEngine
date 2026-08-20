"""Task Executor architecture and registry.

Defines the generic TaskExecutor abstraction and TaskExecutorRegistry.
Executors in OMEGA-002 are safe generic placeholders with zero AI or media APIs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID


class TaskExecutor(ABC):
    """Abstract base class for all task executors."""

    @abstractmethod
    def execute(
        self,
        task_id: UUID,
        task_type: str,
        task_input: dict[str, Any] | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute the task and return an output dictionary on success.

        Raises an Exception on failure.
        """
        pass


class NoopExecutor(TaskExecutor):
    """A no-op executor that completes immediately."""

    def execute(
        self,
        task_id: UUID,
        task_type: str,
        task_input: dict[str, Any] | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {"status": "noop_completed", "task_id": str(task_id)}


class TestExecutor(TaskExecutor):
    """A generic test executor verifying payload handling."""

    def execute(
        self,
        task_id: UUID,
        task_type: str,
        task_input: dict[str, Any] | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "status": "test_completed",
            "task_id": str(task_id),
            "input_received": task_input or {},
        }


class GenericWorkflowExecutor(TaskExecutor):
    """Generic workflow node executor for placeholder stages (e.g. strategy, research)."""

    def execute(
        self,
        task_id: UUID,
        task_type: str,
        task_input: dict[str, Any] | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "stage": task_type,
            "status": "success",
            "task_id": str(task_id),
            "summary": f"Placeholder execution for stage '{task_type}' completed successfully.",
        }


class TaskExecutorRegistry:
    """Registry mapping task_type strings to TaskExecutor instances."""

    def __init__(self) -> None:
        self._executors: dict[str, TaskExecutor] = {}
        self._default_executor: TaskExecutor = GenericWorkflowExecutor()

        # Register standard built-in generic executors
        self.register("system.noop", NoopExecutor())
        self.register("system.test", TestExecutor())

    def register(self, task_type: str, executor: TaskExecutor) -> None:
        """Register an executor for a given task_type."""
        self._executors[task_type] = executor

    def get(self, task_type: str) -> TaskExecutor:
        """Retrieve the executor for task_type, or default to GenericWorkflowExecutor."""
        return self._executors.get(task_type, self._default_executor)


# Global singleton registry instance
default_executor_registry = TaskExecutorRegistry()
