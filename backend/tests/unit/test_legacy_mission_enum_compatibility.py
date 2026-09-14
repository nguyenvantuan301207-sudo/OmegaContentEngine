"""Focused unit tests for legacy mission enum compatibility and invariant hardening."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from omega.domain.mission import (
    AutonomyLevel,
    MissionCreate,
    MissionResponse,
    MissionState,
    MissionUpdate,
)


class MockLegacyMissionORM:
    """Simulates a persisted SQLAlchemy Mission ORM instance with legacy string values."""

    def __init__(
        self,
        mission_id=None,
        state="ACTIVE",
        autonomy_level="FULL",
        title="Legacy Canary Mission",
        objective="Verify forensic preservation",
        channel_id=None,
        description=None,
        priority=1,
        metadata_=None,
        created_at=None,
        updated_at=None,
    ) -> None:
        self.id = mission_id or uuid4()
        self.title = title
        self.objective = objective
        self.channel_id = channel_id
        self.description = description
        self.state = state
        self.autonomy_level = autonomy_level
        self.priority = priority
        self.metadata_ = metadata_ or {}
        now = datetime.now(UTC)
        self.created_at = created_at or now
        self.updated_at = updated_at or now
        self.started_at = None
        self.paused_at = None
        self.completed_at = None
        self.cancelled_at = None


def test_a_legacy_mission_response_serialization() -> None:
    """A. Persisted legacy Mission with state='ACTIVE' and autonomy_level='FULL'
    serializes as state='RUNNING' and autonomy_level='AUTONOMOUS'.
    """
    orm = MockLegacyMissionORM(state="ACTIVE", autonomy_level="FULL")
    response = MissionResponse.model_validate(orm)

    assert response.state == MissionState.RUNNING
    assert response.autonomy_level == AutonomyLevel.AUTONOMOUS
    assert response.id == orm.id


def test_b_raw_orm_values_remain_unmodified() -> None:
    """B. Raw ORM/database values remain 'ACTIVE' and 'FULL' after serialization."""
    orm = MockLegacyMissionORM(state="ACTIVE", autonomy_level="FULL")
    _ = MissionResponse.model_validate(orm)

    assert orm.state == "ACTIVE"
    assert orm.autonomy_level == "FULL"


def test_c_mission_create_rejects_full() -> None:
    """C. MissionCreate with autonomy_level='FULL' is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        MissionCreate(
            title="Attempt Full Autonomy Create",
            objective="Should fail closed",
            autonomy_level="FULL",  # type: ignore[arg-type]
        )
    errors = exc_info.value.errors()
    assert any("autonomy_level" in str(e["loc"]) for e in errors)


def test_d_mission_update_rejects_full() -> None:
    """D. MissionUpdate with autonomy_level='FULL' is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        MissionUpdate(
            autonomy_level="FULL",  # type: ignore[arg-type]
        )
    errors = exc_info.value.errors()
    assert any("autonomy_level" in str(e["loc"]) for e in errors)


def test_e_unknown_autonomy_fails_closed() -> None:
    """E. Unknown autonomy value such as 'GOD_MODE' is NOT silently normalized."""
    orm = MockLegacyMissionORM(state="RUNNING", autonomy_level="GOD_MODE")
    with pytest.raises(ValidationError) as exc_info:
        MissionResponse.model_validate(orm)
    errors = exc_info.value.errors()
    assert any("autonomy_level" in str(e["loc"]) for e in errors)


def test_f_unknown_state_fails_closed() -> None:
    """F. Unknown state such as 'LIVE' is NOT silently normalized."""
    orm = MockLegacyMissionORM(state="LIVE", autonomy_level="SUPERVISED")
    with pytest.raises(ValidationError) as exc_info:
        MissionResponse.model_validate(orm)
    errors = exc_info.value.errors()
    assert any("state" in str(e["loc"]) for e in errors)


def test_g_canonical_mission_values_serialize_unchanged() -> None:
    """G. Normal canonical Mission values serialize unchanged."""
    orm = MockLegacyMissionORM(state="DRAFT", autonomy_level="MANUAL")
    response = MissionResponse.model_validate(orm)

    assert response.state == MissionState.DRAFT
    assert response.autonomy_level == AutonomyLevel.MANUAL


def test_h_p17_e5_canonical_style_unchanged() -> None:
    """H. P17-E5 style: state='RUNNING', autonomy_level='SUPERVISED' remains unchanged."""
    orm = MockLegacyMissionORM(
        state="RUNNING",
        autonomy_level="SUPERVISED",
        title="OMEGA P17E5_CANONICAL_PRIVATE_20260914",
    )
    response = MissionResponse.model_validate(orm)

    assert response.state == MissionState.RUNNING
    assert response.autonomy_level == AutonomyLevel.SUPERVISED
    assert response.title == "OMEGA P17E5_CANONICAL_PRIVATE_20260914"


def test_i_migration_contains_not_valid_constraints() -> None:
    """I. Migration contains DB constraints for both fields and uses NOT VALID."""
    migration_file = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "016_add_mission_enum_check_constraints.py"
    )
    assert migration_file.exists(), f"Migration file not found at {migration_file}"

    content = migration_file.read_text(encoding="utf-8")
    assert "ck_missions_state" in content
    assert "ck_missions_autonomy_level" in content
    assert "NOT VALID" in content
    assert 'down_revision = "015"' in content
