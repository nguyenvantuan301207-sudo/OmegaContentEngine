"""Offline behavioral contracts for the canonical Mission publish bridge."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from omega.application import executor as executor_module
from omega.application.durable_dispatch import DurableDispatchService
from omega.application.guardian.engine import GuardianEngine
from omega.application.media_storage import LocalMediaStorageProvider
from omega.application.network.preflight import NetworkPreflightService
from omega.application.publisher.adapters.base import AdapterRegistry
from omega.application.publisher.intent_service import PublishIntentService
from omega.application.publisher.publish_service import PublishExecutionService
from omega.application.publisher.reconciliation_service import ReconciliationService
from omega.domain.mission import MissionState
from omega.domain.publisher import (
    PublishAttemptState,
    PublishIntentState,
    ReconciliationStatus,
)
from omega.domain.task import TaskState
from omega.infrastructure import database, database_sync
from omega.infrastructure.models import (
    MediaArtifact,
    Mission,
    MissionExecution,
    PlatformAccount,
    ProductionRequest,
    PublishIntent,
    Task,
)
from omega.worker import tasks as worker_tasks


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class PublishSession:
    def __init__(self, records, mission, task, events):
        self.records = records
        self.results = iter((mission, task))
        self.events = events
        self.commit = AsyncMock(side_effect=lambda: events.append("commit"))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return ScalarResult(next(self.results))

    async def get(self, model, record_id):
        return self.records.get((model, record_id))


@pytest.fixture
def publish_lineage():
    mission_id, execution_id, channel_id, dna_id = uuid4(), uuid4(), uuid4(), uuid4()
    task_id, artifact_id, unrelated_id, account_id, intent_id = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    )
    mission = SimpleNamespace(
        id=mission_id,
        state=MissionState.RUNNING.value,
        channel_id=channel_id,
        guardian_epoch=7,
    )
    execution = SimpleNamespace(
        id=execution_id,
        mission_id=mission_id,
        channel_dna_revision_id=dna_id,
    )
    task = SimpleNamespace(
        id=task_id,
        mission_id=mission_id,
        execution_id=execution_id,
        task_type="publish",
        state=TaskState.RUNNING.value,
        dispatched_epoch=7,
        output=None,
        updated_at=None,
    )
    artifact = SimpleNamespace(id=artifact_id, content_hash="a" * 64)
    unrelated = SimpleNamespace(id=unrelated_id, content_hash="b" * 64)
    intent = SimpleNamespace(id=intent_id, state=PublishIntentState.APPROVED.value)
    records = {
        (MissionExecution, execution_id): execution,
        (MediaArtifact, artifact_id): artifact,
        (MediaArtifact, unrelated_id): unrelated,
    }
    context = {
        "mission_id": str(mission_id),
        "execution_id": str(execution_id),
        "dependency_outputs": {"qa": {"media_artifact_id": str(artifact_id)}},
    }
    task_input = {
        "canonical_publish": {
            "platform_account_id": account_id,
            "title": "Canonical title",
            "made_for_kids": False,
        }
    }
    return SimpleNamespace(**locals())


def _install_publish(monkeypatch, data, *, intent=None):
    events = []
    session = PublishSession(data.records, data.mission, data.task, events)
    monkeypatch.setattr(database, "AsyncWorkerSessionLocal", lambda: session)
    create = AsyncMock(
        return_value=intent or data.intent,
        side_effect=lambda *_args, **_kwargs: events.append("intent") or (intent or data.intent),
    )

    async def capture_enqueue(*_args, **kwargs):
        events.append("enqueue")
        return SimpleNamespace(**kwargs)

    enqueue = AsyncMock(side_effect=capture_enqueue)
    monkeypatch.setattr(PublishIntentService, "create_publish_intent", create)
    monkeypatch.setattr(DurableDispatchService, "enqueue_async", enqueue)
    return session, create, enqueue, events


def test_publish_requires_explicit_canonical_authority_before_async_preparation() -> None:
    context = {
        "mission_id": str(uuid4()),
        "execution_id": str(uuid4()),
        "dependency_outputs": {"qa": {"media_artifact_id": str(uuid4())}},
    }
    with pytest.raises(ValueError, match="authority"):
        worker_tasks._execute_canonical_publish(uuid4(), {}, context)


def test_publish_artifact_identity_is_only_the_direct_qa_output() -> None:
    artifact_id = uuid4()
    context = {"dependency_outputs": {"qa": {"media_artifact_id": str(artifact_id)}}}
    assert worker_tasks._canonical_publish_artifact_id(context) == artifact_id
    with pytest.raises(ValueError, match="QA dependency"):
        worker_tasks._canonical_publish_artifact_id(
            {"dependency_outputs": {"production": {"media_artifact_id": str(uuid4())}}}
        )


def test_valid_preparation_uses_exact_qa_artifact_and_commits_after_enqueue(
    monkeypatch, publish_lineage
) -> None:
    data = publish_lineage
    session, create, enqueue, events = _install_publish(monkeypatch, data)

    result = worker_tasks._execute_canonical_publish(data.task_id, data.task_input, data.context)

    assert isinstance(result, worker_tasks._PendingPublishResult)
    create.assert_awaited_once()
    create_kwargs = create.await_args.kwargs
    assert create_kwargs["commit"] is False
    assert create_kwargs["initial_state"] is PublishIntentState.APPROVED
    payload = create.await_args.args[1]
    assert payload.media_artifact_id == data.artifact_id
    assert payload.media_artifact_id != data.unrelated_id
    assert payload.media_artifact_checksum == data.artifact.content_hash
    assert payload.platform_account_id == data.account_id
    assert data.task.output == {
        "publish_intent_id": str(data.intent_id),
        "media_artifact_id": str(data.artifact_id),
    }
    enqueue.assert_awaited_once()
    dispatch = enqueue.await_args.kwargs
    assert dispatch["task_name"] == "omega.publisher.execute_publish"
    assert dispatch["args"] == [str(data.task_id)]
    assert dispatch["idempotency_key"] == f"publish-dispatch:{data.intent_id}"
    session.commit.assert_awaited_once()
    assert events == ["intent", "enqueue", "commit"]


@pytest.mark.parametrize(
    "stale",
    [
        lambda d: setattr(d.mission, "guardian_epoch", 8),
        lambda d: setattr(d.execution, "mission_id", uuid4()),
        lambda d: setattr(d.task, "state", TaskState.READY.value),
    ],
)
def test_stale_current_state_fails_before_intent_dispatch_or_commit(
    monkeypatch, publish_lineage, stale
) -> None:
    stale(publish_lineage)
    session, create, enqueue, _events = _install_publish(monkeypatch, publish_lineage)
    with pytest.raises(ValueError, match="current-state"):
        worker_tasks._execute_canonical_publish(
            publish_lineage.task_id, publish_lineage.task_input, publish_lineage.context
        )
    create.assert_not_awaited()
    enqueue.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_reused_approved_intent_presents_same_dispatch_idempotency_key(
    monkeypatch, publish_lineage
) -> None:
    keys = []
    for _ in range(2):
        _session, _create, enqueue, _events = _install_publish(
            monkeypatch, publish_lineage, intent=publish_lineage.intent
        )
        worker_tasks._execute_canonical_publish(
            publish_lineage.task_id, publish_lineage.task_input, publish_lineage.context
        )
        keys.append(enqueue.await_args.kwargs["idempotency_key"])
    assert keys == [
        f"publish-dispatch:{publish_lineage.intent_id}",
        f"publish-dispatch:{publish_lineage.intent_id}",
    ]


def test_internal_readiness_only_creates_intent_without_external_dispatch(
    monkeypatch, publish_lineage
) -> None:
    data = publish_lineage
    data.task_input["canonical_publish"]["execution_mode"] = "INTERNAL_READINESS_ONLY"
    session, create, enqueue, events = _install_publish(monkeypatch, data)
    readiness = AsyncMock(
        return_value={
            "status": "INTERNAL_READY",
            "artifact_verified": True,
            "guardian_valid": True,
            "account_valid": True,
            "privacy_valid": True,
            "external_ready": False,
            "validation_errors": [],
        }
    )
    network = AsyncMock(side_effect=AssertionError("network preflight called"))
    external_publish = AsyncMock(side_effect=AssertionError("external publish called"))
    monkeypatch.setattr(
        PublishExecutionService, "validate_internal_publish_readiness", readiness
    )
    monkeypatch.setattr(NetworkPreflightService, "preflight", network)
    monkeypatch.setattr(PublishExecutionService, "execute_publish", external_publish)

    result = worker_tasks._execute_canonical_publish(data.task_id, data.task_input, data.context)

    create.assert_awaited_once()
    readiness.assert_awaited_once_with(
        session,
        guardian_session_factory=database.AsyncWorkerSessionLocal,
        task_id=data.task_id,
        mission_id=data.mission_id,
        execution_id=data.execution_id,
        artifact_id=data.artifact_id,
        intent_id=data.intent_id,
    )
    enqueue.assert_not_awaited()
    network.assert_not_awaited()
    external_publish.assert_not_awaited()
    assert not isinstance(result, worker_tasks._PendingPublishResult)
    assert result["internal_readiness"]["status"] == "INTERNAL_READY"
    assert result["internal_readiness"]["external_ready"] is False
    assert events == ["intent", "commit", "commit"]


@pytest.mark.asyncio
async def test_internal_readiness_rejects_stale_intent_artifact_checksum(
    monkeypatch, tmp_path
) -> None:
    mission_id, execution_id, task_id, channel_id = uuid4(), uuid4(), uuid4(), uuid4()
    artifact_id, request_id, intent_id, account_id = uuid4(), uuid4(), uuid4(), uuid4()
    media = tmp_path / "artifact.mp4"
    media.write_bytes(b"canonical artifact")
    artifact_hash = __import__("hashlib").sha256(media.read_bytes()).hexdigest()
    records = {
        (Task, task_id): SimpleNamespace(
            id=task_id, mission_id=mission_id, execution_id=execution_id
        ),
        (Mission, mission_id): SimpleNamespace(
            id=mission_id, state=MissionState.RUNNING.value, channel_id=channel_id
        ),
        (MissionExecution, execution_id): SimpleNamespace(
            id=execution_id, mission_id=mission_id
        ),
        (MediaArtifact, artifact_id): SimpleNamespace(
            id=artifact_id,
            production_request_id=request_id,
            storage_uri="artifacts/artifact.mp4",
            content_hash=artifact_hash,
            version=1,
        ),
        (PublishIntent, intent_id): SimpleNamespace(
            id=intent_id,
            task_id=task_id,
            mission_id=mission_id,
            channel_id=channel_id,
            media_artifact_id=artifact_id,
            media_artifact_checksum="0" * 64,
            platform_account_id=account_id,
            requested_privacy_status="PRIVATE",
            platform_custom_options={},
            state=PublishIntentState.APPROVED.value,
        ),
        (ProductionRequest, request_id): SimpleNamespace(
            channel_id=channel_id, mission_execution_id=execution_id
        ),
        (PlatformAccount, account_id): SimpleNamespace(
            channel_id=channel_id, status="ACTIVE"
        ),
    }
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model, key: records.get((model, key))))
    monkeypatch.setattr(
        LocalMediaStorageProvider, "resolve_artifact_path", MagicMock(return_value=media)
    )
    guardian = AsyncMock(side_effect=AssertionError("Guardian must not run after checksum failure"))
    monkeypatch.setattr(GuardianEngine, "execute_check", guardian)
    network = AsyncMock(side_effect=AssertionError("network preflight called"))
    monkeypatch.setattr(NetworkPreflightService, "preflight", network)

    guardian_session_factory = MagicMock()
    report = await PublishExecutionService.validate_internal_publish_readiness(
        session,
        guardian_session_factory=guardian_session_factory,
        task_id=task_id,
        mission_id=mission_id,
        execution_id=execution_id,
        artifact_id=artifact_id,
        intent_id=intent_id,
    )

    assert report["status"] == "INTERNAL_NOT_READY"
    assert "PublishIntent artifact checksum is stale or mismatched." in report["validation_errors"]
    guardian.assert_not_awaited()
    guardian_session_factory.assert_not_called()
    network.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_internal_readiness_happy_path_is_provider_free(monkeypatch, tmp_path) -> None:
    mission_id, execution_id, task_id, channel_id = uuid4(), uuid4(), uuid4(), uuid4()
    artifact_id, request_id, intent_id, account_id = uuid4(), uuid4(), uuid4(), uuid4()
    media = tmp_path / "internal-ready.mp4"
    media.write_bytes(b"verified internal publish artifact")
    artifact_hash = hashlib.sha256(media.read_bytes()).hexdigest()
    records = {
        (Task, task_id): SimpleNamespace(
            id=task_id, mission_id=mission_id, execution_id=execution_id
        ),
        (Mission, mission_id): SimpleNamespace(
            id=mission_id, state=MissionState.RUNNING.value, channel_id=channel_id
        ),
        (MissionExecution, execution_id): SimpleNamespace(
            id=execution_id, mission_id=mission_id
        ),
        (MediaArtifact, artifact_id): SimpleNamespace(
            id=artifact_id,
            production_request_id=request_id,
            storage_uri="artifacts/internal-ready.mp4",
            content_hash=artifact_hash,
            version=1,
        ),
        (PublishIntent, intent_id): SimpleNamespace(
            id=intent_id,
            task_id=task_id,
            mission_id=mission_id,
            channel_id=channel_id,
            media_artifact_id=artifact_id,
            media_artifact_checksum=artifact_hash,
            platform_account_id=account_id,
            requested_privacy_status="PRIVATE",
            platform_custom_options={},
            state=PublishIntentState.APPROVED.value,
        ),
        (ProductionRequest, request_id): SimpleNamespace(
            channel_id=channel_id, mission_execution_id=execution_id
        ),
        (PlatformAccount, account_id): SimpleNamespace(
            channel_id=channel_id, status="ACTIVE"
        ),
    }
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model, key: records.get((model, key))))
    monkeypatch.setattr(
        LocalMediaStorageProvider, "resolve_artifact_path", MagicMock(return_value=media)
    )
    guardian = AsyncMock(
        return_value=SimpleNamespace(
            decision=SimpleNamespace(action=SimpleNamespace(value="ALLOW"))
        )
    )
    monkeypatch.setattr(GuardianEngine, "execute_check", guardian)
    network = AsyncMock(side_effect=AssertionError("network preflight called"))
    external_publish = AsyncMock(side_effect=AssertionError("external publish called"))
    monkeypatch.setattr(NetworkPreflightService, "preflight", network)
    monkeypatch.setattr(PublishExecutionService, "execute_publish", external_publish)

    report = await PublishExecutionService.validate_internal_publish_readiness(
        session,
        guardian_session_factory=MagicMock(),
        task_id=task_id,
        mission_id=mission_id,
        execution_id=execution_id,
        artifact_id=artifact_id,
        intent_id=intent_id,
    )

    assert report == {
        "status": "INTERNAL_READY",
        "artifact_verified": True,
        "guardian_valid": True,
        "account_valid": True,
        "privacy_valid": True,
        "external_ready": False,
        "validation_errors": [],
    }
    guardian.assert_awaited_once()
    network.assert_not_awaited()
    external_publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_readiness_missing_production_request_fails_closed(
    monkeypatch, tmp_path
) -> None:
    mission_id, execution_id, task_id, channel_id = uuid4(), uuid4(), uuid4(), uuid4()
    artifact_id, request_id, intent_id, account_id = uuid4(), uuid4(), uuid4(), uuid4()
    media = tmp_path / "orphaned-artifact.mp4"
    media.write_bytes(b"orphaned but otherwise valid artifact")
    artifact_hash = hashlib.sha256(media.read_bytes()).hexdigest()
    records = {
        (Task, task_id): SimpleNamespace(
            id=task_id, mission_id=mission_id, execution_id=execution_id
        ),
        (Mission, mission_id): SimpleNamespace(
            id=mission_id, state=MissionState.RUNNING.value, channel_id=channel_id
        ),
        (MissionExecution, execution_id): SimpleNamespace(
            id=execution_id, mission_id=mission_id
        ),
        (MediaArtifact, artifact_id): SimpleNamespace(
            id=artifact_id,
            production_request_id=request_id,
            storage_uri="artifacts/orphaned-artifact.mp4",
            content_hash=artifact_hash,
            version=1,
        ),
        (PublishIntent, intent_id): SimpleNamespace(
            id=intent_id,
            task_id=task_id,
            mission_id=mission_id,
            channel_id=channel_id,
            media_artifact_id=artifact_id,
            media_artifact_checksum=artifact_hash,
            platform_account_id=account_id,
            requested_privacy_status="PRIVATE",
            platform_custom_options={},
            state=PublishIntentState.APPROVED.value,
        ),
        (PlatformAccount, account_id): SimpleNamespace(
            channel_id=channel_id, status="ACTIVE"
        ),
    }
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model, key: records.get((model, key))))
    monkeypatch.setattr(
        LocalMediaStorageProvider, "resolve_artifact_path", MagicMock(return_value=media)
    )
    guardian = AsyncMock(side_effect=AssertionError("Guardian called for orphaned artifact"))
    network = AsyncMock(side_effect=AssertionError("network preflight called"))
    monkeypatch.setattr(GuardianEngine, "execute_check", guardian)
    monkeypatch.setattr(NetworkPreflightService, "preflight", network)

    report = await PublishExecutionService.validate_internal_publish_readiness(
        session,
        guardian_session_factory=MagicMock(),
        task_id=task_id,
        mission_id=mission_id,
        execution_id=execution_id,
        artifact_id=artifact_id,
        intent_id=intent_id,
    )

    assert report["status"] == "INTERNAL_NOT_READY"
    assert "MediaArtifact ProductionRequest parent is missing." in report["validation_errors"]
    guardian.assert_not_awaited()
    network.assert_not_awaited()


class Query:
    def __init__(self, record):
        self.record = record

    def filter(self, *_args):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.record


def test_execute_task_pending_publish_skips_generic_terminal_mutation(monkeypatch) -> None:
    mission_id, execution_id, task_id = uuid4(), uuid4(), uuid4()
    mission = SimpleNamespace(
        id=mission_id, state=MissionState.RUNNING.value, guardian_epoch=3
    )
    task = SimpleNamespace(
        id=task_id,
        mission_id=mission_id,
        execution_id=execution_id,
        task_type="publish",
        title="Publish",
        state=TaskState.QUEUED.value,
        dispatched_epoch=3,
        input={},
        output=None,
        retry_count=0,
        max_retries=0,
    )
    session = MagicMock()
    session.query.side_effect = [
        Query(SimpleNamespace(mission_id=mission_id)),
        Query(mission),
        Query(task),
    ]
    monkeypatch.setattr(database_sync, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(worker_tasks, "_load_dependency_outputs", MagicMock(return_value={}))
    monkeypatch.setattr(
        worker_tasks,
        "_execute_canonical_publish",
        MagicMock(return_value=worker_tasks._PendingPublishResult({"intent": "prepared"})),
    )
    monkeypatch.setattr(executor_module, "default_executor_registry", MagicMock())
    terminal_enqueue = MagicMock()
    monkeypatch.setattr(DurableDispatchService, "enqueue", terminal_enqueue)

    result = worker_tasks.execute_task.run(str(task_id))

    assert result == {"status": "pending", "task_id": str(task_id)}
    assert task.state == TaskState.RUNNING.value
    assert task.output is None
    assert session.commit.call_count == 1
    terminal_enqueue.assert_not_called()


def test_execute_task_internal_publish_reaches_succeeded(monkeypatch) -> None:
    mission_id, execution_id, task_id = uuid4(), uuid4(), uuid4()
    mission = SimpleNamespace(
        id=mission_id, state=MissionState.RUNNING.value, guardian_epoch=3
    )
    task = SimpleNamespace(
        id=task_id,
        mission_id=mission_id,
        execution_id=execution_id,
        task_type="publish",
        title="Publish",
        state=TaskState.QUEUED.value,
        dispatched_epoch=3,
        input={"canonical_publish": {"execution_mode": "INTERNAL_READINESS_ONLY"}},
        output=None,
        retry_count=0,
        max_retries=0,
        started_at=None,
        completed_at=None,
        updated_at=None,
    )
    session = MagicMock()
    session.query.side_effect = [
        Query(SimpleNamespace(mission_id=mission_id)),
        Query(mission),
        Query(task),
        Query(mission),
        Query(task),
    ]
    monkeypatch.setattr(database_sync, "SyncSessionLocal", lambda: session)
    monkeypatch.setattr(worker_tasks, "_load_dependency_outputs", MagicMock(return_value={}))
    monkeypatch.setattr(
        worker_tasks,
        "_execute_canonical_publish",
        MagicMock(return_value={"internal_readiness": {"status": "INTERNAL_READY"}}),
    )
    terminal_enqueue = MagicMock()
    monkeypatch.setattr(DurableDispatchService, "enqueue", terminal_enqueue)

    result = worker_tasks.execute_task.run(str(task_id))

    assert result["status"] == "success"
    assert task.state == TaskState.SUCCEEDED.value
    assert task.output["internal_readiness"]["status"] == "INTERNAL_READY"
    terminal_enqueue.assert_called_once()
    assert terminal_enqueue.call_args.kwargs["purpose"] == "MISSION_TASK_TERMINAL_EVALUATION"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_state", [TaskState.SUCCEEDED.value, TaskState.FAILED.value])
async def test_terminal_wakeup_contract_is_behavioral(monkeypatch, terminal_state) -> None:
    task = SimpleNamespace(id=uuid4(), mission_id=uuid4(), execution_id=uuid4())
    attempt = SimpleNamespace(id=uuid4())
    enqueue = AsyncMock()
    monkeypatch.setattr(DurableDispatchService, "enqueue_async", enqueue)

    await PublishExecutionService._enqueue_terminal_mission_evaluation(
        MagicMock(), task, attempt, terminal_state
    )

    enqueue.assert_awaited_once()
    call = enqueue.await_args.kwargs
    assert call["task_name"] == "omega.orchestrator.evaluate"
    assert call["args"] == [str(task.mission_id), str(task.execution_id)]
    assert call["purpose"] == "PUBLISH_TERMINAL_MISSION_EVALUATION"
    assert call["idempotency_key"] == (
        f"publisher-terminal-evaluation:{task.id}:{attempt.id}:{terminal_state}"
    )


class ReconciliationSession:
    def __init__(self, results, records, events):
        self.results = iter(results)
        self.records = records
        self.events = events
        self.commit = AsyncMock(side_effect=lambda: events.append("commit"))
        self.add = MagicMock()

    async def execute(self, statement):
        if not statement.is_update:
            return ScalarResult(next(self.results))
        values = statement.compile().params
        record = self.records[statement.table.name]
        for key in (
            "state",
            "provider_video_id",
            "provider_url",
            "reconciliation_status",
            "completed_at",
            "updated_at",
            "bytes_uploaded",
        ):
            if key in values:
                setattr(record, key, values[key])
        return ScalarResult(None)


def _reconciliation_records():
    mission_id, execution_id, task_id, intent_id, attempt_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    task = SimpleNamespace(
        id=task_id,
        mission_id=mission_id,
        execution_id=execution_id,
        state=TaskState.RUNNING.value,
    )
    intent = SimpleNamespace(
        id=intent_id,
        task_id=task_id,
        state=PublishIntentState.CLAIMED.value,
    )
    attempt = SimpleNamespace(
        id=attempt_id,
        publish_intent_id=intent_id,
        state=PublishAttemptState.UNKNOWN.value,
        reconciliation_status=ReconciliationStatus.PENDING.value,
    )
    upload = SimpleNamespace(
        id=uuid4(),
        publish_attempt_id=attempt_id,
        session_uri="https://upload.youtube.test/session/one",
        total_bytes=1000,
        bytes_uploaded=100,
    )
    return SimpleNamespace(task=task, intent=intent, attempt=attempt, upload=upload)


def _install_reconciliation(monkeypatch, data, result, events):
    permit = SimpleNamespace()
    preflight = AsyncMock(return_value=(SimpleNamespace(), permit))
    adapter = SimpleNamespace(reconcile_upload_session=AsyncMock(return_value=result))
    enqueue = AsyncMock(side_effect=lambda *_args, **_kwargs: events.append("enqueue"))
    monkeypatch.setattr(NetworkPreflightService, "preflight", preflight)
    monkeypatch.setattr(AdapterRegistry, "get", MagicMock(return_value=adapter))
    monkeypatch.setattr(DurableDispatchService, "enqueue_async", enqueue)
    records = {
        "publish_attempts": data.attempt,
        "publish_intents": data.intent,
        "tasks": data.task,
        "upload_sessions": data.upload,
    }
    return ReconciliationSession(
        [data.attempt, data.upload, data.intent, data.task], records, events
    ), enqueue


@pytest.mark.asyncio
async def test_reconcile_confirmed_success_terminalizes_and_enqueues_before_commit(
    monkeypatch,
) -> None:
    data = _reconciliation_records()
    events = []
    result = SimpleNamespace(
        is_confirmed_success=True,
        is_incomplete=False,
        provider_video_id="provider-video-1",
        provider_url="https://youtube.test/watch/provider-video-1",
        diagnostic_reason="provider confirmed upload",
    )
    session, enqueue = _install_reconciliation(monkeypatch, data, result, events)

    status = await ReconciliationService.reconcile_attempt(session, data.attempt.id)

    assert status is ReconciliationStatus.CONFIRMED_SUCCESS
    assert data.attempt.state == PublishAttemptState.SUCCEEDED.value
    assert data.attempt.reconciliation_status == ReconciliationStatus.CONFIRMED_SUCCESS.value
    assert data.intent.state == PublishIntentState.PUBLISHED.value
    assert data.task.state == TaskState.SUCCEEDED.value
    enqueue.assert_awaited_once()
    call = enqueue.await_args.kwargs
    assert call["task_name"] == "omega.orchestrator.evaluate"
    assert call["args"] == [str(data.task.mission_id), str(data.task.execution_id)]
    assert call["purpose"] == "PUBLISH_TERMINAL_MISSION_EVALUATION"
    assert call["idempotency_key"] == (
        f"publisher-terminal-evaluation:{data.task.id}:{data.attempt.id}:SUCCEEDED"
    )
    assert call["mission_id"] == data.task.mission_id
    assert call["mission_execution_id"] == data.task.execution_id
    assert call["mission_task_id"] == data.task.id
    assert events == ["enqueue", "commit"]


@pytest.mark.asyncio
async def test_reconcile_incomplete_remains_pending_without_terminal_wakeup(monkeypatch) -> None:
    data = _reconciliation_records()
    result = SimpleNamespace(
        is_confirmed_success=False,
        is_incomplete=True,
        bytes_received=640,
    )
    session, enqueue = _install_reconciliation(monkeypatch, data, result, [])

    status = await ReconciliationService.reconcile_attempt(session, data.attempt.id)

    assert status is ReconciliationStatus.PENDING
    assert data.upload.bytes_uploaded == 640
    assert data.attempt.reconciliation_status == ReconciliationStatus.PENDING.value
    assert data.task.state == TaskState.RUNNING.value
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_missing_upload_session_enters_manual_hold_without_terminal_wakeup(
    monkeypatch,
) -> None:
    data = _reconciliation_records()
    enqueue = AsyncMock()
    monkeypatch.setattr(DurableDispatchService, "enqueue_async", enqueue)
    session = ReconciliationSession(
        [data.attempt, None], {"publish_attempts": data.attempt}, []
    )

    status = await ReconciliationService.reconcile_attempt(session, data.attempt.id)

    assert status is ReconciliationStatus.MANUAL_HOLD
    assert data.attempt.reconciliation_status == ReconciliationStatus.MANUAL_HOLD.value
    assert data.task.state == TaskState.RUNNING.value
    enqueue.assert_not_awaited()
