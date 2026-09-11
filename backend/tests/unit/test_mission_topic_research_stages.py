"""Offline contracts for canonical Mission topic and research stages."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from omega.application import executor as executor_module
from omega.application import research_service
from omega.application.durable_dispatch import DurableDispatchService
from omega.application.mission_service import _enrich_canonical_content_seed
from omega.application.planner import StaticMissionPlanner
from omega.domain.mission import MissionState
from omega.domain.research import ResearchOutcome, ResearchRequestStatus
from omega.domain.task import TaskState
from omega.domain.topic import TopicStatus
from omega.infrastructure import database, database_sync
from omega.infrastructure.models import (
    Mission,
    MissionExecution,
    ResearchBrief,
    ResearchRequest,
    Task,
    TopicCandidate,
)
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


class AsyncScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class ResearchSession:
    def __init__(self, records, request, brief):
        self.records = records
        self.request = request
        self.brief = brief

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, model, record_id):
        return self.records.get((model, record_id))

    async def execute(self, statement):
        sql = str(statement)
        if "research_requests" in sql:
            return AsyncScalarResult(self.request)
        if "research_briefs" in sql:
            return AsyncScalarResult(self.brief)
        raise AssertionError(f"Unexpected statement: {sql}")


def install_research_stage(monkeypatch, *, outcome):
    mission_id, execution_id, channel_id, dna_id = uuid4(), uuid4(), uuid4(), uuid4()
    task_id, topic_id, request_id, brief_id = uuid4(), uuid4(), uuid4(), uuid4()
    identity = f"mission-research:{execution_id}:{task_id}"
    task = SimpleNamespace(id=task_id, mission_id=mission_id, execution_id=execution_id)
    execution = SimpleNamespace(
        id=execution_id,
        mission_id=mission_id,
        channel_dna_revision_id=dna_id,
    )
    mission = SimpleNamespace(id=mission_id, channel_id=channel_id)
    topic = SimpleNamespace(
        id=topic_id,
        channel_id=channel_id,
        status=TopicStatus.SELECTED.value,
    )
    request = SimpleNamespace(
        id=request_id,
        topic_candidate_id=topic_id,
        mission_execution_id=execution_id,
        channel_id=channel_id,
        metadata_={"canonical_task_identity": identity},
        status=ResearchRequestStatus.PENDING.value,
        outcome=None,
    )
    brief = SimpleNamespace(
        id=brief_id,
        research_request_id=request_id,
        topic_candidate_id=topic_id,
        channel_id=channel_id,
        outcome=outcome,
    )
    records = {
        (Task, task_id): task,
        (MissionExecution, execution_id): execution,
        (Mission, mission_id): mission,
        (TopicCandidate, topic_id): topic,
        (ResearchRequest, request_id): request,
        (ResearchBrief, brief_id): brief,
    }
    monkeypatch.setattr(
        database,
        "AsyncWorkerSessionLocal",
        lambda: ResearchSession(records, request, brief),
    )
    create = AsyncMock()
    add_sources = AsyncMock(return_value=[])

    async def run_research(*args):
        request.status = ResearchRequestStatus.SUCCEEDED.value
        request.outcome = outcome
        return SimpleNamespace(id=brief_id)

    run = AsyncMock(side_effect=run_research)
    monkeypatch.setattr(research_service, "create_research_request", create)
    monkeypatch.setattr(research_service, "batch_add_sources", add_sources)
    monkeypatch.setattr(research_service, "run_research", run)
    ctx = {
        "mission_id": str(mission_id),
        "execution_id": str(execution_id),
        "dependency_outputs": {
            "topic_discovery": {"topic_candidate_id": str(topic_id)}
        },
    }
    return SimpleNamespace(
        task_id=task_id,
        topic_id=topic_id,
        request_id=request_id,
        brief_id=brief_id,
        request=request,
        brief=brief,
        context=ctx,
        create=create,
        add_sources=add_sources,
        run=run,
    )


def test_zero_source_research_cannot_silently_authorize_content(monkeypatch) -> None:
    data = install_research_stage(monkeypatch, outcome=ResearchOutcome.INSUFFICIENT.value)

    with pytest.raises(ValueError, match="SUFFICIENT"):
        worker_tasks._execute_canonical_research(data.task_id, {}, data.context)

    data.add_sources.assert_not_awaited()
    data.run.assert_awaited_once()


def test_explicit_sources_use_existing_ingestion_and_replay_is_safe(monkeypatch) -> None:
    data = install_research_stage(monkeypatch, outcome=ResearchOutcome.SUFFICIENT.value)
    task_input = {
        "canonical_research": {
            "sources": [
                {
                    "source_type": "MANUAL",
                    "title": "Explicit local source",
                    "publisher": "Local editorial desk",
                    "content_excerpt": "This is bounded explicit source authority.",
                }
            ]
        }
    }

    first = worker_tasks._execute_canonical_research(
        data.task_id, task_input, data.context
    )
    second = worker_tasks._execute_canonical_research(
        data.task_id, task_input, data.context
    )

    assert first == second == {
        "topic_candidate_id": str(data.topic_id),
        "research_request_id": str(data.request_id),
        "research_brief_id": str(data.brief_id),
    }
    data.add_sources.assert_awaited_once()
    assert data.add_sources.await_args.args[1] == data.request_id
    data.run.assert_awaited_once()


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
    # Monkeypatch durable dispatch so the success-path outbox enqueue is a no-op
    monkeypatch.setattr(DurableDispatchService, "enqueue", MagicMock())

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
