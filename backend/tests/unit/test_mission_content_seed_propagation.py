"""Offline contracts for canonical Mission content seed propagation."""

from __future__ import annotations

from copy import deepcopy
from uuid import UUID, uuid4

import pytest

from omega.application.mission_service import _enrich_canonical_content_seed
from omega.application.planner import StaticMissionPlanner


def make_plan():
    """Return the canonical static plan without invoking external systems."""
    return StaticMissionPlanner().plan("Mission", "Objective", "AUTONOMOUS")


def task_inputs(plan) -> dict[str, dict | None]:
    return {task.task_create.task_type: task.task_create.input for task in plan.tasks}


def test_complete_seed_is_normalized_and_propagated_to_canonical_stages() -> None:
    topic_id = uuid4()
    research_id = uuid4()
    metadata = {
        "canonical_inputs": {
            "topic_candidate_id": topic_id.hex.upper(),
            "research_brief_id": research_id,
        },
        "unrelated": {"provider": "must-not-propagate"},
    }
    original_metadata = deepcopy(metadata)
    plan = make_plan()
    original_inputs = deepcopy(task_inputs(plan))

    _enrich_canonical_content_seed(plan, metadata)

    inputs = task_inputs(plan)

    assert inputs["topic_discovery"] == {
        "topic_candidate_id": str(topic_id),
    }
    assert inputs["content_generation"] == {
        "canonical_seed": {
            "topic_candidate_id": str(topic_id),
            "research_brief_id": str(research_id),
        }
    }

    assert UUID(inputs["topic_discovery"]["topic_candidate_id"])
    assert UUID(inputs["content_generation"]["canonical_seed"]["topic_candidate_id"])
    assert UUID(inputs["content_generation"]["canonical_seed"]["research_brief_id"])

    assert "unrelated" not in inputs["topic_discovery"]
    assert "unrelated" not in inputs["content_generation"]

    assert metadata == original_metadata
    assert metadata["canonical_inputs"]["research_brief_id"] is research_id

    for task_type, task_input in inputs.items():
        if task_type not in ("topic_discovery", "content_generation"):
            assert task_input == original_inputs[task_type]


@pytest.mark.parametrize("metadata", [None, {}, {"unrelated": True}, {"canonical_inputs": {}}])
def test_absent_seed_preserves_canonical_task_inputs(metadata) -> None:
    plan = make_plan()
    original_inputs = deepcopy(task_inputs(plan))

    _enrich_canonical_content_seed(plan, metadata)

    assert task_inputs(plan) == original_inputs
    assert task_inputs(plan)["content_generation"] == {}


@pytest.mark.parametrize(
    "canonical_inputs",
    [
        {"research_brief_id": str(uuid4())},
    ],
)
def test_partial_seed_fails_closed(canonical_inputs) -> None:
    plan = make_plan()
    original_inputs = deepcopy(task_inputs(plan))

    with pytest.raises(ValueError, match="requires"):
        _enrich_canonical_content_seed(plan, {"canonical_inputs": canonical_inputs})

    assert task_inputs(plan) == original_inputs


def test_topic_candidate_id_alone_is_valid_authority() -> None:
    topic_id = uuid4()
    plan = make_plan()

    _enrich_canonical_content_seed(
        plan, {"canonical_inputs": {"topic_candidate_id": str(topic_id)}}
    )

    inputs = task_inputs(plan)
    assert inputs["topic_discovery"] == {"topic_candidate_id": str(topic_id)}
    # content_generation receives nothing when research_brief_id is absent
    assert inputs["content_generation"] == {}


@pytest.mark.parametrize("malformed_topic_id", ["not-a-uuid", "", "   ", 123, None])
def test_malformed_topic_candidate_id_fails_closed(malformed_topic_id) -> None:
    plan = make_plan()

    with pytest.raises(ValueError, match="topic_candidate_id"):
        _enrich_canonical_content_seed(
            plan,
            {
                "canonical_inputs": {
                    "topic_candidate_id": malformed_topic_id,
                    "research_brief_id": str(uuid4()),
                }
            },
        )

    assert task_inputs(plan)["content_generation"] == {}


@pytest.mark.parametrize("malformed_research_id", ["not-a-uuid", "", "   ", 123, None])
def test_malformed_research_brief_id_fails_closed(malformed_research_id) -> None:
    plan = make_plan()

    with pytest.raises(ValueError, match="research_brief_id"):
        _enrich_canonical_content_seed(
            plan,
            {
                "canonical_inputs": {
                    "topic_candidate_id": str(uuid4()),
                    "research_brief_id": malformed_research_id,
                }
            },
        )

    assert task_inputs(plan)["content_generation"] == {}


def test_unrelated_metadata_locations_are_not_scanned() -> None:
    plan = make_plan()
    metadata = {
        "topic_candidate_id": str(uuid4()),
        "research_brief_id": str(uuid4()),
        "nested": {
            "canonical_inputs": {
                "topic_candidate_id": str(uuid4()),
                "research_brief_id": str(uuid4()),
            }
        },
    }

    _enrich_canonical_content_seed(plan, metadata)

    assert task_inputs(plan)["content_generation"] == {}
