"""Offline contracts for the real canonical Mission content adapter."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from omega.application import content_service
from omega.application import executor as executor_module
from omega.application.durable_dispatch import DurableDispatchService
from omega.domain.content import (
    ContentOutcome,
    ContentRequestStatus,
    ContentType,
    ScriptQAStatus,
)
from omega.domain.mission import MissionState
from omega.domain.research import ResearchOutcome, ResearchRequestStatus
from omega.domain.task import TaskState
from omega.domain.topic import TopicStatus
from omega.infrastructure import database, database_sync
from omega.infrastructure.models import (
    ContentGenerationRequest,
    Mission,
    MissionExecution,
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    TopicCandidate,
)
from omega.worker import tasks as worker_tasks


def pair(topic_id=None, brief_id=None):
    return {
        "topic_candidate_id": str(topic_id or uuid4()),
        "research_brief_id": str(brief_id or uuid4()),
    }


def context(research=None):
    result = {
        "mission_id": str(uuid4()),
        "execution_id": str(uuid4()),
        "dependency_outputs": {},
    }
    if research is not None:
        result["dependency_outputs"]["research"] = research
    return result


@pytest.mark.parametrize(
    ("research", "task_input"),
    [
        (pair(), None),
        ({"stage": "research", "status": "success"}, {"canonical_seed": pair()}),
    ],
)
def test_complete_dependency_or_seed_is_accepted(research, task_input) -> None:
    topic_id, brief_id = worker_tasks._canonical_content_pair(task_input, context(research))
    expected = research if "topic_candidate_id" in research else task_input["canonical_seed"]
    assert (topic_id, brief_id) == (
        UUID(expected["topic_candidate_id"]),
        UUID(expected["research_brief_id"]),
    )


def test_identical_dependency_and_seed_are_accepted() -> None:
    canonical = pair()
    assert worker_tasks._canonical_content_pair(
        {"canonical_seed": canonical}, context(canonical)
    ) == (UUID(canonical["topic_candidate_id"]), UUID(canonical["research_brief_id"]))


@pytest.mark.parametrize(
    ("research", "task_input", "message"),
    [
        (pair(), {"canonical_seed": pair()}, "mismatch"),
        ({"topic_candidate_id": str(uuid4())}, None, "both"),
        (None, {"canonical_seed": {"research_brief_id": str(uuid4())}}, "both"),
        (None, {"canonical_seed": {}}, "both"),
        (None, {"canonical_seed": {"unexpected": "value"}}, "both"),
        (pair(), {"canonical_seed": {}}, "both"),
        (None, {"canonical_seed": {"topic_candidate_id": "bad", "research_brief_id": str(uuid4())}}, "malformed"),
        ({"status": "success"}, None, "required"),
    ],
)
def test_invalid_correlation_fails_closed(research, task_input, message) -> None:
    with pytest.raises(ValueError, match=message):
        worker_tasks._canonical_content_pair(task_input, context(research))


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class AsyncSession:
    def __init__(self, records, current_script=None):
        self.records = records
        self.current_script = current_script

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, model, record_id):
        return self.records.get((model, record_id))

    async def execute(self, statement):
        return ScalarResult(self.current_script)


def lineage(*, topic_status=TopicStatus.SELECTED.value):
    mission_id, execution_id, channel_id, dna_id = uuid4(), uuid4(), uuid4(), uuid4()
    topic_id, brief_id, research_id = uuid4(), uuid4(), uuid4()
    request_id, script_id = uuid4(), uuid4()
    records = {
        (MissionExecution, execution_id): SimpleNamespace(
            id=execution_id, mission_id=mission_id, channel_dna_revision_id=dna_id
        ),
        (Mission, mission_id): SimpleNamespace(id=mission_id, channel_id=channel_id),
        (TopicCandidate, topic_id): SimpleNamespace(
            id=topic_id, channel_id=channel_id, status=topic_status
        ),
        (ResearchBrief, brief_id): SimpleNamespace(
            id=brief_id,
            topic_candidate_id=topic_id,
            channel_id=channel_id,
            research_request_id=research_id,
            outcome=ResearchOutcome.SUFFICIENT.value,
        ),
        (ResearchRequest, research_id): SimpleNamespace(
            id=research_id,
            topic_candidate_id=topic_id,
            channel_id=channel_id,
            mission_execution_id=execution_id,
            status=ResearchRequestStatus.SUCCEEDED.value,
            outcome=ResearchOutcome.SUFFICIENT.value,
        ),
        (ContentGenerationRequest, request_id): SimpleNamespace(
            id=request_id,
            mission_execution_id=execution_id,
            channel_id=channel_id,
            topic_candidate_id=topic_id,
            research_brief_id=brief_id,
            channel_dna_revision_id=dna_id,
            status=ContentRequestStatus.DRAFT.value,
            outcome=ContentOutcome.GENERATED.value,
        ),
        (ScriptVersion, script_id): SimpleNamespace(
            id=script_id,
            content_request_id=request_id,
            qa_status=ScriptQAStatus.PASSED.value,
        ),
    }
    ctx = {
        "mission_id": str(mission_id),
        "execution_id": str(execution_id),
        "dependency_outputs": {"research": pair(topic_id, brief_id)},
    }
    return SimpleNamespace(
        mission_id=mission_id,
        execution_id=execution_id,
        channel_id=channel_id,
        dna_id=dna_id,
        topic_id=topic_id,
        brief_id=brief_id,
        research_id=research_id,
        request_id=request_id,
        script_id=script_id,
        records=records,
        context=ctx,
    )


def install_adapter_fakes(monkeypatch, data, *, current_script=None):
    session = AsyncSession(data.records, current_script=current_script)
    monkeypatch.setattr(database, "AsyncWorkerSessionLocal", lambda: session)
    create = AsyncMock(
        return_value=SimpleNamespace(id=data.request_id, status=ContentRequestStatus.DRAFT.value)
    )
    generate = AsyncMock(return_value=SimpleNamespace(id=data.script_id))
    monkeypatch.setattr(content_service, "create_request", create)
    monkeypatch.setattr(content_service, "generate_content", generate)
    return create, generate


def test_invalid_topic_or_brief_relationship_fails_before_service(monkeypatch) -> None:
    data = lineage(topic_status="DISCOVERED")
    create, generate = install_adapter_fakes(monkeypatch, data)
    with pytest.raises(ValueError, match="TopicCandidate"):
        worker_tasks._execute_canonical_content(uuid4(), None, data.context)
    create.assert_not_awaited()
    generate.assert_not_awaited()

    data = lineage()
    data.records[(ResearchBrief, data.brief_id)].topic_candidate_id = uuid4()
    create, generate = install_adapter_fakes(monkeypatch, data)
    with pytest.raises(ValueError, match="ResearchBrief"):
        worker_tasks._execute_canonical_content(uuid4(), None, data.context)
    create.assert_not_awaited()
    generate.assert_not_awaited()


@pytest.mark.parametrize("field", ["mission_execution_id", "channel_id"])
def test_research_execution_or_channel_mismatch_fails_before_service(monkeypatch, field) -> None:
    data = lineage()
    setattr(data.records[(ResearchRequest, data.research_id)], field, uuid4())
    create, generate = install_adapter_fakes(monkeypatch, data)
    with pytest.raises(ValueError, match="lineage"):
        worker_tasks._execute_canonical_content(uuid4(), None, data.context)
    create.assert_not_awaited()
    generate.assert_not_awaited()


@pytest.mark.parametrize(
    ("target", "outcome"),
    [
        (ResearchRequest, ResearchOutcome.INSUFFICIENT.value),
        (ResearchBrief, ResearchOutcome.INSUFFICIENT.value),
    ],
)
def test_insufficient_research_cannot_authorize_content(
    monkeypatch, target, outcome
) -> None:
    data = lineage()
    record_id = data.research_id if target is ResearchRequest else data.brief_id
    data.records[(target, record_id)].outcome = outcome
    create, generate = install_adapter_fakes(monkeypatch, data)

    with pytest.raises(ValueError, match="SUFFICIENT"):
        worker_tasks._execute_canonical_content(uuid4(), None, data.context)

    create.assert_not_awaited()
    generate.assert_not_awaited()


def test_pinned_dna_mismatch_fails_before_generation(monkeypatch) -> None:
    data = lineage()
    data.records[(ContentGenerationRequest, data.request_id)].channel_dna_revision_id = uuid4()
    create, generate = install_adapter_fakes(monkeypatch, data)
    with pytest.raises(ValueError, match="ContentGenerationRequest lineage"):
        worker_tasks._execute_canonical_content(uuid4(), None, data.context)
    create.assert_awaited_once()
    generate.assert_not_awaited()


def test_canonical_long_form_uses_pinned_dna_default_runtime(monkeypatch) -> None:
    data = lineage()
    request = data.records[(ContentGenerationRequest, data.request_id)]
    request.content_type = ContentType.YOUTUBE_LONGFORM.value
    request.target_duration_seconds = 840
    create, _ = install_adapter_fakes(monkeypatch, data)

    worker_tasks._execute_canonical_content(uuid4(), None, data.context)

    request_in = create.await_args.args[2]
    assert request_in.content_type == ContentType.YOUTUBE_LONGFORM
    assert request_in.target_duration_seconds is None
    assert request.channel_dna_revision_id == data.dna_id
    assert request.target_duration_seconds == 840
    assert request.target_duration_seconds != 480


def test_explicit_short_runtime_reaches_content_request(monkeypatch) -> None:
    data = lineage()
    create, _ = install_adapter_fakes(monkeypatch, data)
    task_input = {
        "canonical_content": {
            "content_type": ContentType.YOUTUBE_SHORT.value,
            "target_duration_seconds": 45,
        }
    }

    worker_tasks._execute_canonical_content(uuid4(), task_input, data.context)

    request_in = create.await_args.args[2]
    assert request_in.content_type == ContentType.YOUTUBE_SHORT
    assert request_in.target_duration_seconds == 45


def test_generation_uses_deterministic_request_and_returns_persisted_ids(monkeypatch) -> None:
    data = lineage()
    task_id = uuid4()
    create, generate = install_adapter_fakes(monkeypatch, data)

    output = worker_tasks._execute_canonical_content(task_id, None, data.context)

    expected_key = hashlib.sha256(
        f"mission-content:{data.execution_id}:{task_id}".encode()
    ).hexdigest()
    assert create.await_args.kwargs["idempotency_key"] == expected_key
    assert create.await_args.args[2].mission_execution_id == data.execution_id
    generate.assert_awaited_once_with(ANY, data.channel_id, data.request_id)
    assert output == {
        "content_request_id": str(data.request_id),
        "script_version_id": str(data.script_id),
        "topic_candidate_id": str(data.topic_id),
        "research_brief_id": str(data.brief_id),
    }


def test_successful_request_reuses_authoritative_current_script(monkeypatch) -> None:
    data = lineage()
    request = data.records[(ContentGenerationRequest, data.request_id)]
    request.status = ContentRequestStatus.SUCCEEDED.value
    script = data.records[(ScriptVersion, data.script_id)]
    create, generate = install_adapter_fakes(monkeypatch, data, current_script=script)

    output = worker_tasks._execute_canonical_content(uuid4(), None, data.context)

    create.assert_awaited_once()
    generate.assert_not_awaited()
    assert output["script_version_id"] == str(data.script_id)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: setattr(
            data.records[(ContentGenerationRequest, data.request_id)],
            "outcome",
            ContentOutcome.BLOCKED.value,
        ),
        lambda data: setattr(
            data.records[(ScriptVersion, data.script_id)],
            "qa_status",
            ScriptQAStatus.BLOCKED.value,
        ),
    ],
)
def test_blocked_generated_content_is_not_returned(monkeypatch, mutation) -> None:
    data = lineage()
    mutation(data)
    _, generate = install_adapter_fakes(monkeypatch, data)

    with pytest.raises(ValueError, match="accepted script"):
        worker_tasks._execute_canonical_content(uuid4(), None, data.context)

    generate.assert_awaited_once()


def test_script_ownership_is_verified(monkeypatch) -> None:
    data = lineage()
    data.records[(ScriptVersion, data.script_id)].content_request_id = uuid4()
    _, generate = install_adapter_fakes(monkeypatch, data)
    with pytest.raises(ValueError, match="does not belong"):
        worker_tasks._execute_canonical_content(uuid4(), None, data.context)
    generate.assert_awaited_once()


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


def test_execute_task_routes_canonical_content_around_placeholder_and_preserves_input(monkeypatch) -> None:
    mission_id, execution_id, task_id = uuid4(), uuid4(), uuid4()
    original_input = {"canonical_seed": pair()}
    task = SimpleNamespace(
        id=task_id,
        mission_id=mission_id,
        execution_id=execution_id,
        task_type="content_generation",
        title="Content",
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
    adapter = MagicMock(
        return_value={"content_request_id": str(uuid4()), "script_version_id": str(uuid4())}
    )
    registry = MagicMock()
    monkeypatch.setattr(database_sync, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(worker_tasks, "_execute_canonical_content", adapter)
    monkeypatch.setattr(executor_module, "default_executor_registry", registry)
    # Monkeypatch durable dispatch so the success-path outbox enqueue is a no-op
    monkeypatch.setattr(DurableDispatchService, "enqueue", MagicMock())

    result = worker_tasks.execute_task.run(str(task_id))

    assert result == {"status": "success", "task_id": str(task_id)}
    adapter.assert_called_once()
    registry.get.assert_not_called()
    assert task.input is original_input
    assert task.output == adapter.return_value
