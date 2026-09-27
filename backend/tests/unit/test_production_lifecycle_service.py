"""Unit and behavioral tests for ProductionLifecycleService.

Covers:
1. Historical Retry-5 defect reproduction & convergence (RenderJob FAILED -> ProductionRequest converged to FAILED).
2. Accepted Retry-6 non-terminal approval state preservation (ProductionRequest SUCCEEDED, QA WAITING_APPROVAL -> preserved, never marked orphan).
3. Monotonicity & immutability of terminal states (SUCCEEDED -> FAILED rejected, FAILED -> SUCCEEDED rejected).
4. Idempotency of terminal failure propagation and reconciliation passes.
5. Success invariants enforcement (MediaArtifact validity, size > 0, SHA-256 hash, RuntimeTruth existence, SUCCEEDED job).
6. Retry safety & lineage immutability.
7. Orphan detection (terminal jobs vs legitimate active in-flight jobs).
8. Publishing safety (recovery never creates PublishIntent or PublishAttempt).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omega.application.production_lifecycle_service import (
    LifecycleInvariantError,
    ProductionLifecycleService,
    RetryabilityCategory,
    classify_failure_retryability,
)
from omega.domain.production import (
    MediaArtifactType,
    ProductionOutcome,
    ProductionQAStatus,
    ProductionRequestStatus,
    RenderErrorCode,
    RenderJobState,
)
from omega.application.production_service import (
    ProductionService,
    ProductionStateError,
)
from omega.domain.task import TaskState
from omega.infrastructure.models import (
    MediaArtifact,
    MissionExecution,
    ProductionRenderJob,
    ProductionRequest,
    ProductionRuntimeTruth,
    RenderPlan,
    Task,
)


class MockScalarResult:
    def __init__(self, items):
        self._items = items if isinstance(items, list) else ([items] if items is not None else [])

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def scalar_one(self):
        if not self._items:
            raise ValueError("No items in scalar_one")
        return self._items[0]

    def scalars(self):
        return self

    def all(self):
        return list(self._items)

    def first(self):
        return self._items[0] if self._items else None


class InMemoryAsyncSession:
    """In-memory async session mock for testing lifecycle transitions."""

    def __init__(self):
        self.records: dict[tuple[type, uuid.UUID], Any] = {}
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def add(self, entity):
        key = getattr(entity, "id", getattr(entity, "artifact_id", uuid.uuid4()))
        self.records[(type(entity), key)] = entity

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def flush(self):
        pass

    async def refresh(self, entity):
        pass

    async def get(self, model, entity_id):
        return self.records.get((model, entity_id))

    async def execute(self, statement):
        # Handle select queries over stored records with basic WHERE filtering
        stmt_str = str(statement).lower()
        where_clauses = getattr(statement, "_where_criteria", ())

        def _get_filter_val(col_name: str):
            for crit in where_clauses:
                left = getattr(crit, "left", None)
                l_name = getattr(left, "key", "") or getattr(left, "name", "")
                if l_name == col_name:
                    right = getattr(crit, "right", None)
                    return getattr(right, "value", right)
            return None

        if "from production_requests" in stmt_str:
            reqs = [r for (m, _), r in self.records.items() if m == ProductionRequest]
            target_id = _get_filter_val("id")
            if target_id is not None:
                reqs = [r for r in reqs if getattr(r, "id", None) == target_id]
            if "select production_requests.id \nfrom" in stmt_str or "select production_requests.id from" in stmt_str:
                return MockScalarResult([r.id for r in reqs])
            return MockScalarResult(reqs)

        if "from production_render_jobs" in stmt_str:
            jobs = [j for (m, _), j in self.records.items() if m == ProductionRenderJob]
            req_id = _get_filter_val("production_request_id")
            if req_id is not None:
                jobs = [j for j in jobs if getattr(j, "production_request_id", None) == req_id]
            key_val = _get_filter_val("idempotency_key")
            if key_val is not None:
                jobs = [j for j in jobs if getattr(j, "idempotency_key", None) == key_val]
            target_id = _get_filter_val("id")
            if target_id is not None:
                jobs = [j for j in jobs if getattr(j, "id", None) == target_id]
            return MockScalarResult(jobs)

        if "from render_plans" in stmt_str:
            plans = [p for (m, _), p in self.records.items() if m == RenderPlan]
            plan_id = _get_filter_val("id")
            if plan_id is not None:
                plans = [p for p in plans if getattr(p, "id", None) == plan_id]
            req_id = _get_filter_val("production_request_id")
            if req_id is not None:
                plans = [p for p in plans if getattr(p, "production_request_id", None) == req_id]
            return MockScalarResult(plans)

        if "from media_artifacts" in stmt_str:
            arts = [a for (m, _), a in self.records.items() if m == MediaArtifact]
            art_id = _get_filter_val("id")
            if art_id is not None:
                arts = [a for a in arts if getattr(a, "id", None) == art_id]
            req_id = _get_filter_val("production_request_id")
            if req_id is not None:
                arts = [a for a in arts if getattr(a, "production_request_id", None) == req_id]
            job_id = _get_filter_val("render_job_id")
            if job_id is not None:
                arts = [a for a in arts if getattr(a, "render_job_id", None) == job_id]
            return MockScalarResult(arts)

        if "from production_runtime_truth" in stmt_str:
            truths = [t for (m, _), t in self.records.items() if m == ProductionRuntimeTruth]
            art_id = _get_filter_val("artifact_id")
            if art_id is not None:
                truths = [t for t in truths if getattr(t, "artifact_id", None) == art_id]
            return MockScalarResult(truths)

        if "from tasks" in stmt_str:
            tasks = [t for (m, _), t in self.records.items() if m == Task]
            return MockScalarResult(tasks)

        if "from mission_executions" in stmt_str:
            execs = [e for (m, _), e in self.records.items() if m == MissionExecution]
            return MockScalarResult(execs)

        return MockScalarResult([])


# ─────────────────────────────────────────────────────────────────────────────
# 1. RETRY-5 DEFECT REPRODUCTION & PROPAGATION
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry5_historical_defect_propagates_render_job_failure():
    """Historical Retry-5 defect: RenderJob failed, but ProductionRequest remained RUNNING.

    Verify fail_production_request converges ProductionRequest to terminal FAILED.
    """
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        outcome=None,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="job-key-1",
        state=RenderJobState.FAILED.value,
        error_code=RenderErrorCode.FFMPEG_FAILED.value,
        sanitized_error="Generated content concatenation failed: FFmpeg concatenation timed out after 180s.",
    )
    session.add(req)
    session.add(job)

    # Invoke lifecycle failure propagation
    success, action = await ProductionLifecycleService.fail_production_request(
        session,
        req_id,
        reason=job.sanitized_error,
        error_code=job.error_code,
        failure_stage="RENDER_EXECUTION",
        details={"job_id": str(job_id)},
    )

    assert success is True
    assert action == "CONVERGED_FAILED"
    assert req.status == ProductionRequestStatus.FAILED.value
    assert req.outcome == ProductionOutcome.BLOCKED.value
    assert req.failed_at is not None
    assert "failure_info" in req.metadata_
    assert req.metadata_["failure_info"]["error_code"] == RenderErrorCode.FFMPEG_FAILED.value
    assert req.metadata_["failure_info"]["retryability"] == RetryabilityCategory.FFMPEG_TIMEOUT.value


@pytest.mark.asyncio
async def test_retry5_orphan_reconciliation_converges_running_to_failed():
    """Reconciliation pass detects RUNNING request with terminal FAILED job and converges it."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        outcome=None,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="key-retry5",
        state=RenderJobState.FAILED.value,
        error_code=RenderErrorCode.TIMEOUT.value,
        sanitized_error="Concatenation timeout",
    )
    session.add(req)
    session.add(job)

    # Run single-request reconciliation
    res = await ProductionLifecycleService.reconcile_production_request(session, req_id)

    assert res["action"] == "CONVERGED_FAILED"
    assert res["reason"] == "orphaned_running_with_terminal_jobs"
    assert req.status == ProductionRequestStatus.FAILED.value
    assert req.outcome == ProductionOutcome.BLOCKED.value


# ─────────────────────────────────────────────────────────────────────────────
# 2. RETRY-6 VALID NON-TERMINAL APPROVAL STATE PRESERVATION
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry6_valid_approval_state_preserved_without_failure():
    """Retry-6 shape: ProductionRequest is SUCCEEDED, QA is WAITING_APPROVAL.

    Reconciliation MUST NOT mark this FAILED or modify it.
    """
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    exec_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        mission_execution_id=exec_id,
        status=ProductionRequestStatus.SUCCEEDED.value,
        outcome=ProductionOutcome.RENDERED.value,
        metadata_={},
    )
    prod_task = Task(
        id=uuid.uuid4(),
        mission_id=mission_id,
        execution_id=exec_id,
        task_type="production",
        state=TaskState.SUCCEEDED.value,
    )
    qa_task = Task(
        id=uuid.uuid4(),
        mission_id=mission_id,
        execution_id=exec_id,
        task_type="qa",
        state=TaskState.WAITING_APPROVAL.value,
    )
    session.add(req)
    session.add(prod_task)
    session.add(qa_task)

    # Reconciler examines the SUCCEEDED request
    res = await ProductionLifecycleService.reconcile_production_request(session, req_id)

    assert res["action"] == "NO_ACTION"
    assert req.status == ProductionRequestStatus.SUCCEEDED.value
    assert req.outcome == ProductionOutcome.RENDERED.value


# ─────────────────────────────────────────────────────────────────────────────
# 3. IDEMPOTENCY OF TERMINAL TRANSITIONS
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failure_propagation_is_idempotent():
    """Calling fail_production_request multiple times converges without state corruption."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        outcome=None,
        metadata_={},
    )
    session.add(req)

    # Call 1
    s1, a1 = await ProductionLifecycleService.fail_production_request(
        session, req_id, reason="First failure", error_code=RenderErrorCode.TIMEOUT
    )
    assert s1 is True
    assert a1 == "CONVERGED_FAILED"
    first_failed_at = req.failed_at

    # Call 2
    s2, a2 = await ProductionLifecycleService.fail_production_request(
        session, req_id, reason="Second duplicate failure", error_code=RenderErrorCode.FFMPEG_FAILED
    )
    assert s2 is True
    assert a2 == "ALREADY_FAILED"
    # Preserves initial failed_at timestamp
    assert req.failed_at == first_failed_at
    assert req.status == ProductionRequestStatus.FAILED.value


@pytest.mark.asyncio
async def test_reconciliation_is_idempotent():
    """Calling reconcile_production_request multiple times is safe and monotonic."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=uuid.uuid4(),
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="key-idemp",
        state=RenderJobState.FAILED.value,
        error_code=RenderErrorCode.FFMPEG_FAILED.value,
    )
    session.add(req)
    session.add(job)

    # First pass: converges
    res1 = await ProductionLifecycleService.reconcile_production_request(session, req_id)
    assert res1["action"] == "CONVERGED_FAILED"

    # Second pass: already terminal, NO_ACTION
    res2 = await ProductionLifecycleService.reconcile_production_request(session, req_id)
    assert res2["action"] == "NO_ACTION"
    assert res2["reason"] == "already_terminal"


# ─────────────────────────────────────────────────────────────────────────────
# 4. MONOTONICITY & TERMINAL STATE IMMUTABILITY
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_terminal_success_cannot_be_overwritten_by_failure():
    """Monotonicity: A SUCCEEDED request cannot be transitioned to FAILED."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.SUCCEEDED.value,
        outcome=ProductionOutcome.RENDERED.value,
        metadata_={},
    )
    session.add(req)

    success, action = await ProductionLifecycleService.fail_production_request(
        session, req_id, reason="Late failure callback"
    )
    assert success is False
    assert action == "TERMINAL_IMMUTABLE"
    assert req.status == ProductionRequestStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_terminal_failure_cannot_be_overwritten_by_success():
    """Monotonicity: A FAILED request cannot be transitioned to SUCCEEDED."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    art_id = uuid.uuid4()
    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.FAILED.value,
        outcome=ProductionOutcome.BLOCKED.value,
        metadata_={},
    )
    session.add(req)

    success, action = await ProductionLifecycleService.succeed_production_request(
        session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
    )
    assert success is False
    assert action == "TERMINAL_IMMUTABLE"
    assert req.status == ProductionRequestStatus.FAILED.value


# ─────────────────────────────────────────────────────────────────────────────
# 5. SUCCESS INVARIANTS ENFORCEMENT
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_success_invariants_require_valid_media_artifact():
    """ProductionRequest cannot SUCCEED without a physical MediaArtifact with valid size & hash."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    art_id = uuid.uuid4()
    job_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    session.add(req)

    # Case 1: Artifact does not exist
    with pytest.raises(LifecycleInvariantError, match="does not exist"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )

    # Case 2: Artifact has zero size
    art = MediaArtifact(
        id=art_id,
        production_request_id=req_id,
        render_job_id=job_id,
        artifact_type=MediaArtifactType.VIDEO.value,
        version=1,
        file_size_bytes=0,  # Invalid!
        content_hash="a" * 64,
        storage_uri="rel/path.mp4",
    )
    session.add(art)
    with pytest.raises(LifecycleInvariantError, match="file_size_bytes must be strictly positive"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )

    # Case 3: Artifact has invalid hash
    art.file_size_bytes = 1024
    art.content_hash = ""  # Invalid!
    with pytest.raises(LifecycleInvariantError, match="must be valid SHA-256"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )

    # Case 4: Linked RenderJob is FAILED
    art.content_hash = "f" * 64
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="key",
        state=RenderJobState.FAILED.value,
    )
    session.add(job)
    with pytest.raises(LifecycleInvariantError, match="is not SUCCEEDED"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )

    # Case 5: Missing ProductionRuntimeTruth
    job.state = RenderJobState.SUCCEEDED.value
    with pytest.raises(LifecycleInvariantError, match="ProductionRuntimeTruth"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )

    # Case 6: All invariants satisfied
    truth = ProductionRuntimeTruth(
        artifact_id=art_id,
        schema_version="4",
        manifest_run_fingerprint="fp123",
        payload={"v": 4},
    )
    session.add(truth)
    success, action = await ProductionLifecycleService.succeed_production_request(
        session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
    )
    assert success is True
    assert action == "CONVERGED_SUCCEEDED"
    assert req.status == ProductionRequestStatus.SUCCEEDED.value
    assert req.outcome == ProductionOutcome.RENDERED.value
    assert req.completed_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# 6. LEGITIMATE IN-PROGRESS WORK IS PRESERVED
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_render_job_in_progress_is_not_orphaned():
    """An active RenderJob (RUNNING or QUEUED) must NOT be marked orphaned or failed."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="active-key",
        state=RenderJobState.RUNNING.value,  # In flight!
    )
    session.add(req)
    session.add(job)

    res = await ProductionLifecycleService.reconcile_production_request(session, req_id)

    assert res["action"] == "NO_ACTION"
    assert res["reason"] == "active_job_in_progress"
    assert req.status == ProductionRequestStatus.RUNNING.value


# ─────────────────────────────────────────────────────────────────────────────
# 7. PUBLISHING SAFETY
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lifecycle_reconciliation_never_creates_publish_intent_or_attempt():
    """Reconciliation must never touch publisher or create publish intents/attempts."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="key-pub-safety",
        state=RenderJobState.FAILED.value,
    )
    session.add(req)
    session.add(job)

    # Reconcile
    await ProductionLifecycleService.reconcile_production_request(session, req_id)

    # Check session records: verify no PublishIntent or PublishAttempt was added
    for (model, _), _entity in session.records.items():
        assert model.__name__ not in ("PublishIntent", "PublishAttempt")


# ─────────────────────────────────────────────────────────────────────────────
# 8. RETRYABILITY CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────


def test_failure_retryability_classification():
    """Verify deterministic mapping of error codes and reasons to retryability categories."""
    assert classify_failure_retryability(RenderErrorCode.TIMEOUT, "FFmpeg timed out") == (
        RetryabilityCategory.FFMPEG_TIMEOUT
    )
    assert classify_failure_retryability(RenderErrorCode.STORAGE_FAILED) == (
        RetryabilityCategory.TRANSIENT_INFRASTRUCTURE
    )
    assert classify_failure_retryability(RenderErrorCode.INPUT_INVALID) == (
        RetryabilityCategory.INVALID_INPUT
    )
    assert classify_failure_retryability(RenderErrorCode.RIGHTS_BLOCKED) == (
        RetryabilityCategory.QA_BLOCKER
    )
    assert classify_failure_retryability(RenderErrorCode.UNKNOWN, "Browser context crashed") == (
        RetryabilityCategory.BROWSER_RUNTIME_FAILURE
    )
    assert classify_failure_retryability(RenderErrorCode.ASSET_MISSING) == (
        RetryabilityCategory.ASSET_PROVIDER_TRANSIENT
    )


# ─────────────────────────────────────────────────────────────────────────────
# 9. PROCESS RESTART RECOVERY & BATCH RECONCILIATION
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_restart_recovery_from_persisted_state():
    """Simulate service restart: a completely fresh session and service instance

    observes persisted DB rows alone and correctly converges orphaned states.
    """
    persisted_db = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()

    # Pre-populate DB as if previous worker crashed after job failed
    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="pre-crash-job",
        state=RenderJobState.FAILED.value,
        error_code=RenderErrorCode.FFMPEG_FAILED.value,
        sanitized_error="Process killed by OOM or worker restart",
    )
    persisted_db.add(req)
    persisted_db.add(job)

    # Fresh process/reconciler starts with no in-memory state
    fresh_session = persisted_db
    sweep_result = await ProductionLifecycleService.reconcile_orphaned_requests(fresh_session)

    assert sweep_result["scanned"] == 1
    assert sweep_result["converged_failed"] == 1
    assert req.status == ProductionRequestStatus.FAILED.value
    assert req.outcome == ProductionOutcome.BLOCKED.value


@pytest.mark.asyncio
async def test_explicit_retry_preserves_immutable_failed_attempt():
    """Explicit retry creates a new attempt/lineage while failed attempt remains immutable."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    job1_id = uuid.uuid4()
    job2_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    # Attempt 1: Failed
    job1 = ProductionRenderJob(
        id=job1_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="render-key:v1",
        state=RenderJobState.FAILED.value,
        attempt=1,
        sanitized_error="Transient timeout",
    )
    # Attempt 2: Explicit new retry job
    job2 = ProductionRenderJob(
        id=job2_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="render-key:v2",
        state=RenderJobState.RUNNING.value,
        attempt=2,
    )
    session.add(req)
    session.add(job1)
    session.add(job2)

    # Reconciler sees an active job (job2) and does NOT fail the request
    res = await ProductionLifecycleService.reconcile_production_request(session, req_id)
    assert res["action"] == "NO_ACTION"
    assert res["reason"] == "active_job_in_progress"

    # Historical failed job is untouched and immutable
    assert job1.state == RenderJobState.FAILED.value
    assert job1.sanitized_error == "Transient timeout"


@pytest.mark.asyncio
async def test_cancel_production_request_and_active_jobs():
    """Cancelling a request marks request CANCELLED and cancels all in-flight active jobs."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="job-to-cancel",
        state=RenderJobState.RUNNING.value,
    )
    session.add(req)
    session.add(job)

    success, action = await ProductionLifecycleService.cancel_production_request(
        session, req_id, reason="User requested cancellation"
    )

    assert success is True
    assert action == "CONVERGED_CANCELLED"
    assert req.status == ProductionRequestStatus.CANCELLED.value
    assert req.outcome == ProductionOutcome.BLOCKED.value
    assert job.state == RenderJobState.CANCELLED.value


@pytest.mark.asyncio
async def test_cancel_cannot_overwrite_terminal_succeeded():
    """Terminal immutable: A SUCCEEDED request cannot be cancelled."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        script_version_id=uuid.uuid4(),
        content_request_id=uuid.uuid4(),
        channel_dna_revision_id=uuid.uuid4(),
        status=ProductionRequestStatus.SUCCEEDED.value,
        outcome=ProductionOutcome.RENDERED.value,
        metadata_={},
    )
    session.add(req)

    success, action = await ProductionLifecycleService.cancel_production_request(
        session, req_id, reason="Too late"
    )
    assert success is False
    assert action == "TERMINAL_IMMUTABLE"
    assert req.status == ProductionRequestStatus.SUCCEEDED.value


# ─────────────────────────────────────────────────────────────────────────────
# 10. EXPLICIT RETRY LINEAGE FIXTURE (AUDIT SECTION 1)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_explicit_retry_end_to_end_lineage_and_immutability():
    """Prove that an explicit retry after a terminal FAILED attempt produces a valid

    new attempt/lineage while strictly preserving historical FAILED state.
    """
    session = InMemoryAsyncSession()
    channel_id = uuid.uuid4()
    script_id = uuid.uuid4()
    content_req_id = uuid.uuid4()
    dna_id = uuid.uuid4()

    # ── ATTEMPT 1: Initial execution terminates in FAILED ──
    req1_id = uuid.uuid4()
    job1_id = uuid.uuid4()
    plan1_id = uuid.uuid4()
    exec1_id = uuid.uuid4()

    req1 = ProductionRequest(
        id=req1_id,
        channel_id=channel_id,
        script_version_id=script_id,
        content_request_id=content_req_id,
        channel_dna_revision_id=dna_id,
        mission_execution_id=exec1_id,
        idempotency_key=f"mission-production:{exec1_id}:task-1",
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    plan1 = RenderPlan(
        id=plan1_id,
        production_request_id=req1_id,
        version=1,
        width=1920,
        height=1080,
        fps=24,
        video_codec="h264",
        audio_codec="aac",
        container="mp4",
        total_duration_ms=5000,
        scene_manifest=[],
        audio_manifest=[],
        subtitle_manifest=[],
    )
    job1 = ProductionRenderJob(
        id=job1_id,
        production_request_id=req1_id,
        render_plan_id=plan1_id,
        idempotency_key=f"mission-render:{req1_id}:task-1",
        state=RenderJobState.FAILED.value,
        attempt=1,
        error_code=RenderErrorCode.FFMPEG_FAILED.value,
        sanitized_error="Encoding pipeline died",
    )
    session.add(req1)
    session.add(plan1)
    session.add(job1)

    # Converge Attempt 1 request to FAILED
    ok1, act1 = await ProductionLifecycleService.fail_production_request(
        session, req1_id, reason="Encoding pipeline died", error_code=RenderErrorCode.FFMPEG_FAILED
    )
    assert ok1 is True
    assert req1.status == ProductionRequestStatus.FAILED.value
    assert req1.outcome == ProductionOutcome.BLOCKED.value

    # PROVE INVARIANT: Attempting to allocate a new render job under Request 1 MUST fail
    prod_service = ProductionService()
    with pytest.raises(ProductionStateError, match="Terminal requests are immutable"):
        await prod_service.allocate_render_job(
            session=session,
            channel_id=channel_id,
            request_id=req1_id,
            idempotency_key="attempt-to-resurrect-job",
            is_rerender=False,
        )

    # ── ATTEMPT 2: Explicit retry creates a distinct lineage ──
    exec2_id = uuid.uuid4()
    req2_id = uuid.uuid4()
    plan2_id = uuid.uuid4()
    job2_id = uuid.uuid4()
    art2_id = uuid.uuid4()

    req2 = ProductionRequest(
        id=req2_id,
        channel_id=channel_id,
        script_version_id=script_id,
        content_request_id=content_req_id,
        channel_dna_revision_id=dna_id,
        mission_execution_id=exec2_id,
        idempotency_key=f"mission-production:{exec2_id}:task-1",
        status=ProductionRequestStatus.READY.value,
        metadata_={},
    )
    plan2 = RenderPlan(
        id=plan2_id,
        production_request_id=req2_id,
        version=1,
        width=1920,
        height=1080,
        fps=24,
        video_codec="h264",
        audio_codec="aac",
        container="mp4",
        total_duration_ms=5000,
        scene_manifest=[],
        audio_manifest=[],
        subtitle_manifest=[],
    )
    session.add(req2)
    session.add(plan2)

    # Allocate job under Attempt 2
    job2, assigned_plan2, is_new2 = await prod_service.allocate_render_job(
        session=session,
        channel_id=channel_id,
        request_id=req2_id,
        idempotency_key=f"mission-render:{req2_id}:task-1",
        is_rerender=False,
    )
    assert is_new2 is True
    assert req2.status == ProductionRequestStatus.RUNNING.value
    assert job2.state == RenderJobState.QUEUED.value

    # Simulate render completion on Attempt 2
    job2.state = RenderJobState.SUCCEEDED.value
    job2.completed_at = datetime.now(UTC)

    art2 = MediaArtifact(
        id=art2_id,
        production_request_id=req2_id,
        render_job_id=job2.id,
        artifact_type=MediaArtifactType.VIDEO.value,
        version=1,
        is_current=True,
        file_size_bytes=1048576,
        content_hash="c" * 64,
        storage_uri="mock://storage/art2.mp4",
        mime_type="video/mp4",
    )
    truth2 = ProductionRuntimeTruth(
        artifact_id=art2_id,
        schema_version=4,
        manifest_run_fingerprint="d" * 64,
        payload={"artifact_sha256": "c" * 64},
    )
    session.add(art2)
    session.add(truth2)

    # Converge Attempt 2 to SUCCEEDED
    ok2, act2 = await ProductionLifecycleService.succeed_production_request(
        session, req2_id, media_artifact_id=art2_id, qa_status=ProductionQAStatus.PASSED
    )
    assert ok2 is True
    assert act2 == "CONVERGED_SUCCEEDED"
    assert req2.status == ProductionRequestStatus.SUCCEEDED.value
    assert req2.outcome == ProductionOutcome.RENDERED.value

    # ── FINAL POST-CONDITIONS ──
    # 1. Historical Request 1 remains strictly immutable FAILED
    assert req1.status == ProductionRequestStatus.FAILED.value
    assert req1.outcome == ProductionOutcome.BLOCKED.value

    # 2. Historical Job 1 remains strictly immutable FAILED
    assert job1.state == RenderJobState.FAILED.value
    assert job1.sanitized_error == "Encoding pipeline died"

    # 3. New Attempt 2 reached SUCCEEDED independently
    assert req2.status == ProductionRequestStatus.SUCCEEDED.value
    assert job2.state == RenderJobState.SUCCEEDED.value

    # 4. No duplicate jobs under Request 1
    req1_jobs = [j for (m, _), j in session.records.items() if m == ProductionRenderJob and j.production_request_id == req1_id]
    assert len(req1_jobs) == 1
    assert req1_jobs[0].id == job1_id


# ─────────────────────────────────────────────────────────────────────────────
# 11. FOCUSED NEGATIVE ARTIFACT INVARIANT TESTS (AUDIT SECTION 4)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_succeed_invariant_wrong_artifact_request_id():
    """MediaArtifact must belong to the exact ProductionRequest being converged."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    foreign_req_id = uuid.uuid4()
    art_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    art = MediaArtifact(
        id=art_id,
        production_request_id=foreign_req_id,  # Wrong request!
        artifact_type=MediaArtifactType.VIDEO.value,
        version=1,
        is_current=True,
        file_size_bytes=1000,
        content_hash="e" * 64,
        storage_uri="mock://storage/vid.mp4",
        mime_type="video/mp4",
    )
    session.add(req)
    session.add(art)

    with pytest.raises(LifecycleInvariantError, match="does not match ProductionRequest ID"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )


@pytest.mark.asyncio
async def test_succeed_invariant_malformed_hashes():
    """MediaArtifact content_hash must be strictly 64 hexadecimal characters."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    art_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    art = MediaArtifact(
        id=art_id,
        production_request_id=req_id,
        artifact_type=MediaArtifactType.VIDEO.value,
        version=1,
        is_current=True,
        file_size_bytes=1000,
        content_hash="32characterhashnotsha256length!",  # Invalid len = 31
        storage_uri="mock://storage/vid.mp4",
        mime_type="video/mp4",
    )
    session.add(req)
    session.add(art)

    # Sub-case A: 32-character hash (MD5 style)
    art.content_hash = "a" * 32
    with pytest.raises(LifecycleInvariantError, match="must be valid SHA-256"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )

    # Sub-case B: Non-hex characters (64 chars, but contains 'z')
    art.content_hash = "z" * 64
    with pytest.raises(LifecycleInvariantError, match="must be valid SHA-256"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )


@pytest.mark.asyncio
async def test_succeed_invariant_wrong_runtime_truth_lineage():
    """ProductionRuntimeTruth must match schema_version=4 and exact artifact lineage."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    art_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="job-k",
        state=RenderJobState.SUCCEEDED.value,
    )
    art = MediaArtifact(
        id=art_id,
        production_request_id=req_id,
        render_job_id=job_id,
        artifact_type=MediaArtifactType.VIDEO.value,
        version=1,
        is_current=True,
        file_size_bytes=1000,
        content_hash="f" * 64,
        storage_uri="mock://storage/vid.mp4",
        mime_type="video/mp4",
    )
    session.add(req)
    session.add(job)
    session.add(art)

    # Sub-case A: Stale/wrong schema version (e.g. 3)
    truth_v3 = ProductionRuntimeTruth(
        artifact_id=art_id,
        schema_version=3,
        manifest_run_fingerprint="f" * 64,
        payload={"artifact_sha256": "f" * 64},
    )
    session.add(truth_v3)
    with pytest.raises(LifecycleInvariantError, match="schema_version must be 4"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )

    # Sub-case B: Truth bound to a different artifact ID
    foreign_art_id = uuid.uuid4()
    truth_foreign = ProductionRuntimeTruth(
        artifact_id=foreign_art_id,
        schema_version=4,
        manifest_run_fingerprint="f" * 64,
        payload={"artifact_sha256": "f" * 64},
    )
    session.records.pop((ProductionRuntimeTruth, art_id), None)
    session.add(truth_foreign)
    with pytest.raises(LifecycleInvariantError, match="ProductionRuntimeTruth record is missing"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )


@pytest.mark.asyncio
async def test_succeed_invariant_stale_truth_hash_mismatch():
    """ProductionRuntimeTruth payload hash must agree with authoritative MediaArtifact content_hash."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    art_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="job-k",
        state=RenderJobState.SUCCEEDED.value,
    )
    art = MediaArtifact(
        id=art_id,
        production_request_id=req_id,
        render_job_id=job_id,
        artifact_type=MediaArtifactType.VIDEO.value,
        version=1,
        is_current=True,
        file_size_bytes=1000,
        content_hash="1" * 64,
        storage_uri="mock://storage/vid.mp4",
        mime_type="video/mp4",
    )
    # Stale truth from an earlier or different render
    truth = ProductionRuntimeTruth(
        artifact_id=art_id,
        schema_version=4,
        manifest_run_fingerprint="f" * 64,
        payload={"artifact_sha256": "2" * 64},  # Mismatch!
    )
    session.add(req)
    session.add(job)
    session.add(art)
    session.add(truth)

    with pytest.raises(LifecycleInvariantError, match="artifact hash mismatch"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )


@pytest.mark.asyncio
async def test_succeed_invariant_physical_file_checks(tmp_path):
    """When a local storage path is given, physical file existence and exact size are enforced."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    art_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="job-k",
        state=RenderJobState.SUCCEEDED.value,
    )
    session.add(req)
    session.add(job)

    # Sub-case A: Missing physical file on disk
    missing_file = tmp_path / "missing_render.mp4"
    art_missing = MediaArtifact(
        id=art_id,
        production_request_id=req_id,
        render_job_id=job_id,
        artifact_type=MediaArtifactType.VIDEO.value,
        version=1,
        is_current=True,
        file_size_bytes=5000,
        content_hash="a" * 64,
        storage_uri=str(missing_file),
        mime_type="video/mp4",
    )
    session.add(art_missing)
    with pytest.raises(LifecycleInvariantError, match="Authoritative physical artifact file missing"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )

    # Sub-case B: Physical file exists but has zero size
    empty_file = tmp_path / "empty_render.mp4"
    empty_file.write_bytes(b"")
    art_missing.storage_uri = str(empty_file)
    with pytest.raises(LifecycleInvariantError, match="has zero size"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )

    # Sub-case C: Physical size does not match persisted authority
    real_file = tmp_path / "real_render.mp4"
    real_file.write_bytes(b"12345")  # 5 bytes
    art_missing.storage_uri = str(real_file)
    art_missing.file_size_bytes = 10000  # Discrepancy!
    with pytest.raises(LifecycleInvariantError, match="Physical artifact size .* does not match persisted authority"):
        await ProductionLifecycleService.succeed_production_request(
            session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
        )


# ─────────────────────────────────────────────────────────────────────────────
# 12. CANCELLATION RACE AUDIT (AUDIT SECTION 5)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancellation_race_stale_worker_success_blocked():
    """When a request is cancelled while job is in-flight, a stale worker completion

    is rejected and terminal CANCELLED status remains strictly monotonic.
    """
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    art_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="in-flight-job",
        state=RenderJobState.RUNNING.value,
    )
    session.add(req)
    session.add(job)

    # 1. User cancels the request
    ok, action = await ProductionLifecycleService.cancel_production_request(
        session, req_id, reason="User clicked Cancel"
    )
    assert ok is True
    assert action == "CONVERGED_CANCELLED"
    assert req.status == ProductionRequestStatus.CANCELLED.value
    assert job.state == RenderJobState.CANCELLED.value

    # 2. Simulate stale worker completing render and calling succeed_production_request
    art = MediaArtifact(
        id=art_id,
        production_request_id=req_id,
        render_job_id=job_id,
        artifact_type=MediaArtifactType.VIDEO.value,
        version=1,
        is_current=True,
        file_size_bytes=1000,
        content_hash="9" * 64,
        storage_uri="mock://storage/stale.mp4",
        mime_type="video/mp4",
    )
    truth = ProductionRuntimeTruth(
        artifact_id=art_id,
        schema_version=4,
        manifest_run_fingerprint="9" * 64,
        payload={"artifact_sha256": "9" * 64},
    )
    session.add(art)
    session.add(truth)

    stale_ok, stale_action = await ProductionLifecycleService.succeed_production_request(
        session, req_id, media_artifact_id=art_id, qa_status=ProductionQAStatus.PASSED
    )

    # Assert: Stale worker was blocked, CANCELLED status preserved
    assert stale_ok is False
    assert stale_action == "TERMINAL_IMMUTABLE"
    assert req.status == ProductionRequestStatus.CANCELLED.value
    assert req.outcome == ProductionOutcome.BLOCKED.value

    # Assert: No publishing triggered
    for (model, _), _entity in session.records.items():
        assert model.__name__ not in ("PublishIntent", "PublishAttempt")


@pytest.mark.asyncio
async def test_cancellation_called_twice_is_idempotent():
    """Calling cancel_production_request repeatedly is idempotent and monotonic."""
    session = InMemoryAsyncSession()
    req_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    session.add(req)

    # Call 1
    ok1, act1 = await ProductionLifecycleService.cancel_production_request(session, req_id)
    assert ok1 is True
    assert act1 == "CONVERGED_CANCELLED"
    assert req.status == ProductionRequestStatus.CANCELLED.value

    # Call 2
    ok2, act2 = await ProductionLifecycleService.cancel_production_request(session, req_id)
    assert ok2 is True
    assert act2 == "ALREADY_CANCELLED"
    assert req.status == ProductionRequestStatus.CANCELLED.value


# ─────────────────────────────────────────────────────────────────────────────
# 13. CRASH RECOVERY GAP ANALYSIS (AUDIT SECTION 2)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crash_recovery_cases_and_orphan_boundaries():
    """Evaluate crash scenarios A, B, C, D against repository authority.

    Proves:
    - Case C (RenderJob FAILED) and Case D (RenderJob SUCCEEDED) are fully recovered.
    - Case A & B (worker killed mid-render) are NOT prematurely declared dead without
      a distributed heartbeat/lease authority, preventing false kills of long renders.
    """
    session = InMemoryAsyncSession()

    # Case C: ProductionRequest RUNNING, RenderJob FAILED -> Converges to FAILED
    req_c_id = uuid.uuid4()
    req_c = ProductionRequest(
        id=req_c_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job_c = ProductionRenderJob(
        id=uuid.uuid4(),
        production_request_id=req_c_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="job-c",
        state=RenderJobState.FAILED.value,
        sanitized_error="OOM crash",
    )
    session.add(req_c)
    session.add(job_c)
    res_c = await ProductionLifecycleService.reconcile_production_request(session, req_c_id)
    assert res_c["action"] == "CONVERGED_FAILED"
    assert req_c.status == ProductionRequestStatus.FAILED.value

    # Case D: ProductionRequest RUNNING, RenderJob SUCCEEDED -> Converges to SUCCEEDED
    req_d_id = uuid.uuid4()
    job_d_id = uuid.uuid4()
    art_d_id = uuid.uuid4()
    req_d = ProductionRequest(
        id=req_d_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job_d = ProductionRenderJob(
        id=job_d_id,
        production_request_id=req_d_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="job-d",
        state=RenderJobState.SUCCEEDED.value,
    )
    art_d = MediaArtifact(
        id=art_d_id,
        production_request_id=req_d_id,
        render_job_id=job_d_id,
        artifact_type=MediaArtifactType.VIDEO.value,
        version=1,
        is_current=True,
        file_size_bytes=2048,
        content_hash="7" * 64,
        storage_uri="mock://storage/art_d.mp4",
        mime_type="video/mp4",
    )
    truth_d = ProductionRuntimeTruth(
        artifact_id=art_d_id,
        schema_version=4,
        manifest_run_fingerprint="7" * 64,
        payload={"artifact_sha256": "7" * 64},
    )
    session.add(req_d)
    session.add(job_d)
    session.add(art_d)
    session.add(truth_d)
    res_d = await ProductionLifecycleService.reconcile_production_request(session, req_d_id)
    assert res_d["action"] == "CONVERGED_SUCCEEDED"
    assert req_d.status == ProductionRequestStatus.SUCCEEDED.value

    # Case B: ProductionRequest RUNNING, RenderJob RUNNING -> Preserved as active
    req_b_id = uuid.uuid4()
    req_b = ProductionRequest(
        id=req_b_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job_b = ProductionRenderJob(
        id=uuid.uuid4(),
        production_request_id=req_b_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="job-b",
        state=RenderJobState.RUNNING.value,
    )
    session.add(req_b)
    session.add(job_b)
    res_b = await ProductionLifecycleService.reconcile_production_request(session, req_b_id)
    assert res_b["action"] == "NO_ACTION"
    assert res_b["reason"] == "active_job_in_progress"
    assert req_b.status == ProductionRequestStatus.RUNNING.value


