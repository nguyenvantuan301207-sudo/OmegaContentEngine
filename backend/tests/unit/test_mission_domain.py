"""Unit tests for Mission domain models, state machines, and schemas."""

from __future__ import annotations

import pytest

from omega.domain.mission import (
    AutonomyLevel,
    ExecutionState,
    InvalidStateTransitionError,
    MissionCreate,
    MissionState,
    MissionTriggerType,
    MissionUpdate,
    validate_mission_transition,
)


def test_valid_mission_transitions() -> None:
    """Verify all permissible mission state transitions."""
    # DRAFT -> READY
    validate_mission_transition(MissionState.DRAFT, MissionState.READY)

    # READY -> RUNNING, CANCELLED
    validate_mission_transition(MissionState.READY, MissionState.RUNNING)
    validate_mission_transition(MissionState.READY, MissionState.CANCELLED)

    # RUNNING -> PAUSED, SUCCEEDED, FAILED, CANCELLED
    validate_mission_transition(MissionState.RUNNING, MissionState.PAUSED)
    validate_mission_transition(MissionState.RUNNING, MissionState.SUCCEEDED)
    validate_mission_transition(MissionState.RUNNING, MissionState.FAILED)
    validate_mission_transition(MissionState.RUNNING, MissionState.CANCELLED)

    # PAUSED -> RUNNING, FAILED, CANCELLED
    validate_mission_transition(MissionState.PAUSED, MissionState.RUNNING)
    validate_mission_transition(MissionState.PAUSED, MissionState.FAILED)
    validate_mission_transition(MissionState.PAUSED, MissionState.CANCELLED)


def test_invalid_mission_transitions() -> None:
    """Verify that disallowed state transitions raise InvalidStateTransitionError."""
    # Cannot jump directly from DRAFT to RUNNING or SUCCEEDED
    with pytest.raises(InvalidStateTransitionError):
        validate_mission_transition(MissionState.DRAFT, MissionState.RUNNING)

    with pytest.raises(InvalidStateTransitionError):
        validate_mission_transition(MissionState.DRAFT, MissionState.SUCCEEDED)

    # Cannot transition out of terminal states
    with pytest.raises(InvalidStateTransitionError):
        validate_mission_transition(MissionState.SUCCEEDED, MissionState.RUNNING)

    with pytest.raises(InvalidStateTransitionError):
        validate_mission_transition(MissionState.FAILED, MissionState.READY)

    with pytest.raises(InvalidStateTransitionError):
        validate_mission_transition(MissionState.CANCELLED, MissionState.DRAFT)


def test_mission_schemas() -> None:
    """Verify Pydantic schema validation for missions."""
    create = MissionCreate(
        title="Test Campaign",
        objective="Generate high-value technical briefings",
        autonomy_level=AutonomyLevel.SUPERVISED,
        priority=2,
    )
    assert create.title == "Test Campaign"
    assert create.autonomy_level == AutonomyLevel.SUPERVISED
    assert create.priority == 2

    update = MissionUpdate(title="Updated Title")
    assert update.title == "Updated Title"
    assert update.objective is None


def test_reserved_enums() -> None:
    """Verify that reserved enums are defined properly."""
    assert AutonomyLevel.AUTONOMOUS.value == "AUTONOMOUS"
    assert AutonomyLevel.STRATEGIC_AUTONOMOUS.value == "STRATEGIC_AUTONOMOUS"
    assert MissionTriggerType.SCHEDULED.value == "SCHEDULED"
    assert ExecutionState.PLANNED.value == "PLANNED"
