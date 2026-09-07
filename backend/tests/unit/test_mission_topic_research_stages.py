"""Offline contracts for canonical Mission topic and research stages."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from omega.application import executor as executor_module
from omega.application.mission_service import _enrich_canonical_content_seed
from omega.application.planner import StaticMissionPlanner
from omega.domain.mission import MissionState
from omega.domain.task import TaskState
from omega.infrastructure import database_sync
from omega.worker import tasks as worker_tasks


def context(topic_output=None):
    value = {
        "mission_id": str(uuid4()),
        "execution_id": str(uuid4()),
        "dependency_outputs": {},
    }
    if topic_output is not None:
        value["dependency_outputs"]["topic_discovery"] = topic_output
    return value


def test_plan_propagates_only_explicit_persisted_topic_authority() -> None:
    topic_id = uuid4()
    metadata = {"canonical_inputs": {"topic_candidate_id": topic_id}}
    original = deepcopy(metadata)
    plan = StaticMissionPlanner().plan("Mission", "Objective", "AUTONOMOUS")

    _enrich_canonical_content_seed(plan, metadata)

    inputs = {item.task_create.task_type: item.task_create.input for item in plan.tasks}
    assert inputs["topic_discovery"] == {"topic_candidate_id": str(topic_id)}
    assert inputs["research"] == {}
    assert inputs["content_generation"] == {}
    assert metadata == original


@pytest.mark.parametrize("task_input", [None, {}, {"topic_candidate_id": "bad"}])
def test_missing_or_malformed_topic_authority_fails_closed(task_input) -> None:
    with pytest.raises(ValueError, match="topic"):
        worker_tasks._canonical_topic_id(task_input)


def test_complete_legacy_content_seed_remains_supported() -> None:
    topic_id, brief_id = uuid4(), uuid4()
    plan = StaticMissionPlanner().plan("Mission", "Objective", "AUTONOMOUS")
    _enrich_canonical_content_seed(
        plan,
        {"canonical_inputs": {"topic_candidate_id": topic_id, "research_brief_id": brief_id}},
    )
    inputs = {item.task_create.task_type: item.task_create.input for item in plan.tasks}
    assert inputs["topic_discovery"] == {"topic_candidate_id": str(topic_id)}
    assert inputs["content_generation"] == {
        "canonical_seed": {
            "topic_candidate_id": str(topic_id),
            "research_brief_id": str(brief_id),
        }
    }


def test_research_requires_direct_well_formed_topic_dependency() -> None:
    topic_id = uuid4()
    assert worker_tasks._canonical_research_topic_id(
        context({"topic_candidate_id": str(topic_id)})
    ) == topic_id
    for bad_context in (
        context(),
        context({}),
        context({"topic_candidate_id": "bad"}),
        {"dependency_outputs": []},
    ):
        with pytest.raises(ValueError, match="topic_discovery|dependency_outputs"):
            worker_tasks._canonical_research_topic_id(bad_context)


def test_research_output_is_directly_consumable_by_content_resolver() -> None:
    topic_id, request_id, brief_id = uuid4(), uuid4(), uuid4()
    research_output = {
        "topic_candidate_id": str(topic_id),
        "research_request_id": str(request_id),
        "research_brief_id": str(brief_id),
    }
    resolved = worker_tasks._canonical_content_pair(
        None,
        {
            "mission_id": str(uuid4()),
            "execution_id": str(uuid4()),
            "dependency_outputs": {"research": research_output},
        },
    )
    assert resolved == (topic_id, brief_id)


class Query:
    def __init__(self, record):
        self.record = record

    def filter(self, *args):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.record

    def outerjoin(self, *args):
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return []


@pytest.mark.parametrize("task_type", ["topic_discovery", "research"])
def test_execute_task_routes_canonical_stages_around_placeholder(monkeypatch, task_type) -> None:
    mission_id, execution_id, task_id = uuid4(), uuid4(), uuid4()
    original_input = {"topic_candidate_id": str(uuid4())} if task_type == "topic_discovery" else {}
    task = SimpleNamespace(
        id=task_id,
        mission_id=mission_id,
        execution_id=execution_id,
        task_type=task_type,
        title=task_type,
        state=TaskState.QUEUED.value,
        dispatched_epoch=3,
        input=original_input,
        output=None,
        retry_count=0,
        max_retries=0,
    )
    mission = SimpleNamespace(
        id=mission_id, state=MissionState.RUNNING.value, guardian_epoch=3
    )
    session = MagicMock()
    session.query.side_effect = [
        Query(SimpleNamespace(mission_id=mission_id)),
        Query(mission),
        Query(task),
        Query(None),
        Query(mission),
        Query(task),
    ]
    output = (
        {"topic_candidate_id": str(uuid4())}
        if task_type == "topic_discovery"
        else {
            "topic_candidate_id": str(uuid4()),
            "research_request_id": str(uuid4()),
            "research_brief_id": str(uuid4()),
        }
    )
    topic_adapter = MagicMock(return_value=output)
    research_adapter = MagicMock(return_value=output)
    registry = MagicMock()
    monkeypatch.setattr(database_sync, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(worker_tasks, "_execute_canonical_topic", topic_adapter)
    monkeypatch.setattr(worker_tasks, "_execute_canonical_research", research_adapter)
    monkeypatch.setattr(executor_module, "default_executor_registry", registry)
    monkeypatch.setattr(worker_tasks.evaluate_mission_task, "delay", MagicMock())

    result = worker_tasks.execute_task.run(str(task_id))

    assert result == {"status": "success", "task_id": str(task_id)}
    assert task.output == output
    assert task.input is original_input
    registry.get.assert_not_called()
    selected = topic_adapter if task_type == "topic_discovery" else research_adapter
    selected.assert_called_once()


def test_output_contracts_contain_persisted_ids_only() -> None:
    topic_id, request_id, brief_id = uuid4(), uuid4(), uuid4()
    topic_output = {"topic_candidate_id": str(topic_id)}
    research_output = {
        "topic_candidate_id": str(topic_id),
        "research_request_id": str(request_id),
        "research_brief_id": str(brief_id),
    }
    assert set(topic_output) == {"topic_candidate_id"}
    assert set(research_output) == {
        "topic_candidate_id",
        "research_request_id",
        "research_brief_id",
    }
    assert all(UUID(value) for value in research_output.values())
