"""Unit tests for Task domain models, state machines, and approval gate semantics."""

from __future__ import annotations

import pytest

from omega.domain.mission import InvalidStateTransitionError
from omega.domain.task import (
    TaskCreate,
    TaskRejectRequest,
    TaskState,
    validate_task_transition,
)


def test_valid_task_transitions() -> None:
    """Verify permissible task state transitions."""
    # PENDING -> BLOCKED, READY, WAITING_APPROVAL, CANCELLED
    validate_task_transition(TaskState.PENDING, TaskState.BLOCKED)
    validate_task_transition(TaskState.PENDING, TaskState.READY)
    validate_task_transition(TaskState.PENDING, TaskState.WAITING_APPROVAL)
    validate_task_transition(TaskState.PENDING, TaskState.CANCELLED)

    # BLOCKED -> READY, WAITING_APPROVAL, CANCELLED
    validate_task_transition(TaskState.BLOCKED, TaskState.READY)
    validate_task_transition(TaskState.BLOCKED, TaskState.WAITING_APPROVAL)
    validate_task_transition(TaskState.BLOCKED, TaskState.CANCELLED)

    # WAITING_APPROVAL -> READY (Approval gate), FAILED (Rejection), CANCELLED
    validate_task_transition(TaskState.WAITING_APPROVAL, TaskState.READY)
    validate_task_transition(TaskState.WAITING_APPROVAL, TaskState.FAILED)
    validate_task_transition(TaskState.WAITING_APPROVAL, TaskState.CANCELLED)

    # READY -> QUEUED, CANCELLED
    validate_task_transition(TaskState.READY, TaskState.QUEUED)
    validate_task_transition(TaskState.READY, TaskState.CANCELLED)

    # QUEUED -> RUNNING, FAILED (dispatch error), CANCELLED
    validate_task_transition(TaskState.QUEUED, TaskState.RUNNING)
    validate_task_transition(TaskState.QUEUED, TaskState.FAILED)
    validate_task_transition(TaskState.QUEUED, TaskState.CANCELLED)

    # RUNNING -> SUCCEEDED, FAILED, READY (retry), CANCELLED
    validate_task_transition(TaskState.RUNNING, TaskState.SUCCEEDED)
    validate_task_transition(TaskState.RUNNING, TaskState.FAILED)
    validate_task_transition(TaskState.RUNNING, TaskState.READY)
    validate_task_transition(TaskState.RUNNING, TaskState.CANCELLED)


def test_approval_gate_semantics() -> None:
    """Verify WAITING_APPROVAL cannot jump directly to SUCCEEDED or RUNNING."""
    with pytest.raises(InvalidStateTransitionError):
        validate_task_transition(TaskState.WAITING_APPROVAL, TaskState.SUCCEEDED)

    with pytest.raises(InvalidStateTransitionError):
        validate_task_transition(TaskState.WAITING_APPROVAL, TaskState.RUNNING)


def test_terminal_task_transitions() -> None:
    """Verify terminal task states cannot transition further."""
    with pytest.raises(InvalidStateTransitionError):
        validate_task_transition(TaskState.SUCCEEDED, TaskState.RUNNING)

    with pytest.raises(InvalidStateTransitionError):
        validate_task_transition(TaskState.FAILED, TaskState.READY)

    with pytest.raises(InvalidStateTransitionError):
        validate_task_transition(TaskState.CANCELLED, TaskState.PENDING)


def test_task_schemas() -> None:
    """Verify Task Pydantic schemas."""
    tc = TaskCreate(
        task_type="research",
        title="Fact Verification",
        description="Verify stats",
        priority=3,
        requires_approval=False,
    )
    assert tc.task_type == "research"
    assert tc.requires_approval is False

    tr = TaskRejectRequest(reason="Editorial tone rejected")
    assert tr.reason == "Editorial tone rejected"
