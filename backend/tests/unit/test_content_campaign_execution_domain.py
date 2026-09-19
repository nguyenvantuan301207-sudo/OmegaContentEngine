"""Unit tests for content campaign execution domain models and checksum stability."""

import pytest
from pydantic import ValidationError

from omega.application.planner import StaticMissionPlanner
from omega.domain.content_campaign_execution import (
    FANOUT_AUTONOMY_LEVEL,
    FANOUT_DEPENDENCY_COUNT,
    FANOUT_MAX_ITEMS,
    FANOUT_MIN_ITEMS,
    FANOUT_MISSION_TRIGGER_TYPE,
    FANOUT_POLICY_NAME,
    FANOUT_POLICY_VERSION,
    FANOUT_PRIORITY_MAX,
    FANOUT_PRIORITY_MIN,
    FANOUT_STAGE_COUNT,
    STATIC_MISSION_PLANNER_COMPAT_VERSION,
    ContentCampaignExecutionCreate,
    compute_fanout_policy_checksum,
)


def test_static_mission_planner_compat_version() -> None:
    """Regression test asserting StaticMissionPlanner compatibility version match."""
    planner = StaticMissionPlanner()
    plan = planner.plan(
        mission_title="Test Mission",
        mission_objective="Test Objective",
        autonomy_level="SUPERVISED",
    )
    assert plan.plan_version == STATIC_MISSION_PLANNER_COMPAT_VERSION
    assert plan.plan_version == "1.0.0"


def test_compute_fanout_policy_checksum_deterministic() -> None:
    """Verify checksum is deterministic and produces consistent 64-char hex string."""
    chk1 = compute_fanout_policy_checksum()
    chk2 = compute_fanout_policy_checksum()
    assert chk1 == chk2
    assert len(chk1) == 64
    assert isinstance(chk1, str)


def test_fanout_policy_constants() -> None:
    """Verify authoritative fanout policy configuration values."""
    assert FANOUT_POLICY_NAME == "default_campaign_fanout"
    assert FANOUT_POLICY_VERSION == 1
    assert FANOUT_AUTONOMY_LEVEL == "SUPERVISED"
    assert FANOUT_MISSION_TRIGGER_TYPE == "API"
    assert FANOUT_STAGE_COUNT == 7
    assert FANOUT_DEPENDENCY_COUNT == 6
    assert FANOUT_PRIORITY_MIN == 1
    assert FANOUT_PRIORITY_MAX == 10
    assert FANOUT_MIN_ITEMS == 1
    assert FANOUT_MAX_ITEMS == 50


def test_content_campaign_execution_create_actor_validation() -> None:
    """Verify actor trimming and validation constraints."""
    # Valid trimmed actor
    req = ContentCampaignExecutionCreate(actor="  test-user  ")
    assert req.actor == "test-user"

    # Exactly 100 chars
    req_max = ContentCampaignExecutionCreate(actor="a" * 100)
    assert req_max.actor == "a" * 100

    # Over 100 chars fails
    with pytest.raises(ValidationError):
        ContentCampaignExecutionCreate(actor="a" * 101)

    # Empty string fails
    with pytest.raises(ValidationError):
        ContentCampaignExecutionCreate(actor="")

    # Whitespace only fails
    with pytest.raises(ValidationError):
        ContentCampaignExecutionCreate(actor="   ")
