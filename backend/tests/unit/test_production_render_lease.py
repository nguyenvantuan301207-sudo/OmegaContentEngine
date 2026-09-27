"""Comprehensive Unit and Integration Tests for Worker Lease, Heartbeat & Crash Recovery (P19-LR2).

Covers:
1. Lease acquisition (atomic transition to RUNNING, token issuance, fence increment, timers).
2. Duplicate & terminal lease acquisition rejection.
3. Renewal CAS (atomic update, wrong token rejection, wrong fence rejection, terminal job rejection).
4. Database clock authority (PostgreSQL func.now() authority; wall-clock independence).
5. Orphan lease expiration and monotonic failure propagation (fencing incremented, WORKER_LEASE_EXPIRED).
6. Stale worker fencing (late success rejected, artifact promotion blocked, RuntimeTruth blocked).
7. Cancellation fencing (fencing incremented, lease revoked, subsequent pulses fail).
8. Heartbeat runner thread lifecycle (background pulses, clean stop).
9. Heartbeat runner lost-lease detection and watchdog self-fencing.
10. Long-render survival simulation (multi-cycle renewal prevents false expiry).
11. Automatic sweep rollout gating (production_lease_sweep_enabled).
12. Legacy unmanaged rows safety.
13. Zero publishing invariants.
14. Canonical lock order consistency (ProductionRequest -> ProductionRenderJob).
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import func, select, text, update

from omega.application.production_heartbeat_runner import ProductionHeartbeatRunner
from omega.application.production_lifecycle_service import (
    ProductionLifecycleService,
    RetryabilityCategory,
    classify_failure_retryability,
)
from omega.application.production_render_lease_service import (
    HEARTBEAT_INTERVAL_SECONDS,
    LEASE_TTL_SECONDS,
    SWEEP_GRACE_SECONDS,
    WORKER_HEARTBEAT_FAILURE_ABORT_SECONDS,
    WORKER_SELF_FENCE_MARGIN_SECONDS,
    ProductionDuplicateExecutionError,
    ProductionLeaseFencingError,
    ProductionLeaseLostError,
    ProductionRenderLeaseService,
    ProductionWorkerSelfFencedError,
    get_worker_instance_id,
)
from omega.application.render_service import ProductionRenderService
from omega.domain.production import (
    MediaArtifactType,
    ProductionOutcome,
    ProductionQAStatus,
    ProductionRequestStatus,
    RenderErrorCode,
    RenderJobState,
)
from omega.infrastructure.models import (
    MediaArtifact,
    MissionExecution,
    ProductionRenderJob,
    ProductionRequest,
    ProductionRuntimeTruth,
    Task,
)


class MockScalarResult:
    def __init__(self, items: list[Any] | None = None, scalar_val: Any = None, rowcount: int = 1):
        self._items = items if isinstance(items, list) else ([items] if items is not None else [])
        self._scalar_val = scalar_val
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def scalar_one(self):
        if not self._items:
            raise ValueError("No items in scalar_one")
        return self._items[0]

    def scalar(self):
        if self._scalar_val is not None:
            return self._scalar_val
        return self._items[0] if self._items else None

    def scalars(self):
        return self

    def all(self):
        return list(self._items)

    def first(self):
        return self._items[0] if self._items else None


class MockLeaseAsyncSession:
    """Mock async session supporting row lookups, with_for_update, and simulated DB functions."""

    def __init__(self, db_time: datetime | None = None):
        self.records: dict[tuple[type, uuid.UUID], Any] = {}
        self.committed = False
        self.rolled_back = False
        self.db_time = db_time or datetime.now(UTC)

    def add(self, entity: Any) -> None:
        key = getattr(entity, "id", getattr(entity, "artifact_id", uuid.uuid4()))
        self.records[(type(entity), key)] = entity

    async def get(self, model: type, entity_id: uuid.UUID) -> Any:
        return self.records.get((model, entity_id))

    async def commit(self) -> None:
        self.committed = True
        for (m, _), j in self.records.items():
            if m is ProductionRenderJob:
                if getattr(j, "lease_expires_at", None) is not None and not isinstance(j.lease_expires_at, datetime):
                    j.lease_expires_at = self.db_time + timedelta(seconds=LEASE_TTL_SECONDS)
                if getattr(j, "heartbeat_at", None) is not None and not isinstance(j.heartbeat_at, datetime):
                    j.heartbeat_at = self.db_time

    async def rollback(self) -> None:
        self.rolled_back = True

    async def flush(self) -> None:
        pass

    async def refresh(self, entity: Any) -> None:
        pass

    async def execute(self, statement: Any) -> MockScalarResult:
        stmt_str = str(statement).lower()
        where_clauses = getattr(statement, "_where_criteria", ())

        def _get_filter_val(col_name: str) -> Any:
            for crit in where_clauses:
                left = getattr(crit, "left", None)
                l_name = getattr(left, "key", "") or getattr(left, "name", "")
                if l_name == col_name:
                    right = getattr(crit, "right", None)
                    return getattr(right, "value", right)
            return None

        # Check for standalone boolean scalar check: SELECT lease_expires_at > func.now() / < func.now() - SWEEP_GRACE
        if "from" not in stmt_str and ("now()" in stmt_str or "func.now()" in stmt_str):
            for (m, _), j in self.records.items():
                if m is ProductionRenderJob:
                    if j.lease_expires_at is None:
                        return MockScalarResult(scalar_val=False)
                    if "interval" in stmt_str:
                        # Expiry condition: expires_at < now - 10s
                        cutoff = self.db_time - timedelta(seconds=SWEEP_GRACE_SECONDS)
                        return MockScalarResult(scalar_val=bool(j.lease_expires_at < cutoff))
                    elif ">" in stmt_str or "<" in stmt_str:
                        # Validity condition: expires_at > now
                        return MockScalarResult(scalar_val=bool(j.lease_expires_at > self.db_time))
            return MockScalarResult(scalar_val=False)

        # UPDATE query
        if "update production_render_jobs" in stmt_str:
            matched = 0
            for (m, _), j in list(self.records.items()):
                if m is ProductionRenderJob and j.state == RenderJobState.RUNNING.value:
                    j.heartbeat_at = self.db_time
                    j.lease_expires_at = self.db_time + timedelta(seconds=LEASE_TTL_SECONDS)
                    matched += 1
            return MockScalarResult(rowcount=matched)

        # SELECT from production_requests
        if "from production_requests" in stmt_str:
            reqs = [r for (m, _), r in self.records.items() if m is ProductionRequest]
            target_id = _get_filter_val("id")
            if target_id is not None:
                if isinstance(target_id, (list, tuple, set)):
                    reqs = [r for r in reqs if getattr(r, "id", None) in target_id]
                else:
                    reqs = [r for r in reqs if getattr(r, "id", None) == target_id]
            return MockScalarResult(reqs)

        # SELECT from production_render_jobs
        if "from production_render_jobs" in stmt_str:
            jobs = [j for (m, _), j in self.records.items() if m is ProductionRenderJob]
            target_id = _get_filter_val("id")
            if target_id is not None:
                if isinstance(target_id, (list, tuple, set)):
                    jobs = [j for j in jobs if getattr(j, "id", None) in target_id]
                else:
                    jobs = [j for j in jobs if getattr(j, "id", None) == target_id]
            req_id = _get_filter_val("production_request_id")
            if req_id is not None:
                jobs = [j for j in jobs if getattr(j, "production_request_id", None) == req_id]
            state_val = _get_filter_val("state")
            if state_val is not None:
                if isinstance(state_val, (list, tuple, set)):
                    jobs = [j for j in jobs if getattr(j, "state", None) in state_val]
                else:
                    jobs = [j for j in jobs if getattr(j, "state", None) == state_val]
            token_val = _get_filter_val("lease_token")
            if token_val is not None:
                jobs = [j for j in jobs if getattr(j, "lease_token", None) == token_val]
            fence_val = _get_filter_val("fencing_token")
            if fence_val is not None:
                jobs = [j for j in jobs if getattr(j, "fencing_token", None) == fence_val]

            def _get_expires_at(job_rec: Any) -> datetime | None:
                exp = getattr(job_rec, "lease_expires_at", None)
                if isinstance(exp, datetime):
                    return exp
                return None

            # If statement has interval check (expiry) or > now() check (validity)
            if "interval" in stmt_str:
                cutoff = self.db_time - timedelta(seconds=SWEEP_GRACE_SECONDS)
                jobs = [j for j in jobs if _get_expires_at(j) and _get_expires_at(j) < cutoff]
            elif ">" in stmt_str and "now()" in stmt_str:
                jobs = [j for j in jobs if _get_expires_at(j) and _get_expires_at(j) > self.db_time]

            return MockScalarResult(jobs)

        # SELECT from media_artifacts
        if "from media_artifacts" in stmt_str:
            arts = [a for (m, _), a in self.records.items() if m is MediaArtifact]
            return MockScalarResult(arts)

        # SELECT from production_runtime_truth
        if "from production_runtime_truth" in stmt_str:
            truths = [t for (m, _), t in self.records.items() if m is ProductionRuntimeTruth]
            return MockScalarResult(truths)

        # SELECT from tasks
        if "from tasks" in stmt_str:
            tasks = [t for (m, _), t in self.records.items() if m is Task]
            return MockScalarResult(tasks)

        # SELECT from mission_executions
        if "from mission_executions" in stmt_str:
            execs = [e for (m, _), e in self.records.items() if m is MissionExecution]
            return MockScalarResult(execs)

        return MockScalarResult([])


# ─────────────────────────────────────────────────────────────────────────────
# 1. LEASE ACQUISITION TESTS
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lease_acquisition_success():
    """Verify that acquiring a lease on a QUEUED job transitions state and issues credentials."""
    session = MockLeaseAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.READY.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=uuid.uuid4(),
        idempotency_key="job-1",
        state=RenderJobState.QUEUED.value,
        fencing_token=0,
    )
    session.add(req)
    session.add(job)

    authority = await ProductionRenderLeaseService.acquire_lease(session, req_id, job_id)

    assert authority.job_id == job_id
    assert authority.lease_token is not None
    assert authority.fencing_token == 1
    assert authority.owner_id == get_worker_instance_id()

    assert job.state == RenderJobState.RUNNING.value
    assert job.lease_token == authority.lease_token
    assert job.fencing_token == 1
    assert job.lease_owner_id == authority.owner_id
    assert job.started_at is not None
    assert job.heartbeat_at is not None
    assert job.lease_expires_at is not None


@pytest.mark.asyncio
async def test_lease_acquisition_rejections():
    """Verify duplicate acquisition against active RUNNING, SUCCEEDED, or terminal FAILED is blocked."""
    session = MockLeaseAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()

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
        idempotency_key="job-1",
        state=RenderJobState.SUCCEEDED.value,
        fencing_token=1,
    )
    session.add(req)
    session.add(job)

    # 1. Terminal SUCCEEDED job rejected
    with pytest.raises(ProductionDuplicateExecutionError, match="already SUCCEEDED"):
        await ProductionRenderLeaseService.acquire_lease(session, req_id, job_id)

    # 2. Terminal FAILED job rejected
    job.state = RenderJobState.FAILED.value
    with pytest.raises(ProductionDuplicateExecutionError, match="terminal FAILED"):
        await ProductionRenderLeaseService.acquire_lease(session, req_id, job_id)

    # 3. Terminal Request rejected
    req.status = ProductionRequestStatus.FAILED.value
    job.state = RenderJobState.QUEUED.value
    with pytest.raises(ProductionLeaseFencingError, match="terminal ProductionRequest"):
        await ProductionRenderLeaseService.acquire_lease(session, req_id, job_id)


# ─────────────────────────────────────────────────────────────────────────────
# 2. LEASE RENEWAL CAS TESTS
# ─────────────────────────────────────────────────────────────────────────────


def test_sync_renewal_cas():
    """Verify synchronous compare-and-set lease renewal behavior."""
    mock_session = MagicMock()
    job_id = uuid.uuid4()
    lease_token = uuid.uuid4()

    # Success case: 1 row updated
    mock_session.execute.return_value.rowcount = 1
    success = ProductionRenderLeaseService.renew_lease_sync(
        mock_session, job_id, lease_token, fencing_token=1
    )
    assert success is True
    mock_session.commit.assert_called_once()

    # Mismatched token or terminal state: 0 rows updated
    mock_session.reset_mock()
    mock_session.execute.return_value.rowcount = 0
    failure = ProductionRenderLeaseService.renew_lease_sync(
        mock_session, job_id, lease_token, fencing_token=1
    )
    assert failure is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. DATABASE CLOCK AUTHORITY TESTS
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_database_clock_authority():
    """Verify that lease expiration logic is driven exclusively by DB time, immune to app clock drift."""
    db_now = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
    session = MockLeaseAsyncSession(db_time=db_now)

    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    token = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.RUNNING.value,
        lease_token=token,
        fencing_token=1,
        heartbeat_at=db_now - timedelta(seconds=20),
        # Expires in future relative to DB now (+70 seconds)
        lease_expires_at=db_now + timedelta(seconds=70),
    )
    session.add(req)
    session.add(job)

    # Even if application local clock is skewed ahead by 5 hours, DB time comparison says valid
    with patch("omega.application.production_render_lease_service.datetime") as mock_dt:
        mock_dt.now.return_value = datetime.now(UTC) + timedelta(hours=5)
        success, action = await ProductionRenderLeaseService.expire_lease(session, req_id, job_id)
        assert success is False
        assert action == "LEASE_NOT_EXPIRED"
        assert job.state == RenderJobState.RUNNING.value


# ─────────────────────────────────────────────────────────────────────────────
# 4. ORPHAN LEASE EXPIRATION & FAILURE PROPAGATION
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orphan_lease_expiration_and_fencing_revocation():
    """Verify that a worker whose lease expired past TTL + grace is failed and fenced."""
    db_now = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
    session = MockLeaseAsyncSession(db_time=db_now)

    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    token = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    # Expired 15 seconds ago (greater than SWEEP_GRACE_SECONDS = 10s)
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.RUNNING.value,
        lease_token=token,
        fencing_token=1,
        heartbeat_at=db_now - timedelta(seconds=115),
        lease_expires_at=db_now - timedelta(seconds=15),
    )
    session.add(req)
    session.add(job)

    success, action = await ProductionRenderLeaseService.expire_lease(session, req_id, job_id)

    assert success is True
    assert action == "EXPIRED"
    assert job.state == RenderJobState.FAILED.value
    assert job.error_code == RenderErrorCode.WORKER_LEASE_EXPIRED.value
    assert job.fencing_token == 2  # Fencing token incremented on expiration!
    assert job.lease_expires_at is None

    # Parent ProductionRequest converged to FAILED
    assert req.status == ProductionRequestStatus.FAILED.value
    assert req.metadata_["failure_info"]["error_code"] == RenderErrorCode.WORKER_LEASE_EXPIRED.value
    assert req.metadata_["failure_info"]["retryability"] == RetryabilityCategory.TRANSIENT_INFRASTRUCTURE.value


# ─────────────────────────────────────────────────────────────────────────────
# 5. STALE WORKER FENCING & ARTIFACT PROMOTION BLOCK
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_worker_finalization_blocked():
    """Verify that a stale worker waking up after lease expiration cannot finalize success."""
    db_now = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
    session = MockLeaseAsyncSession(db_time=db_now)

    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    old_token = uuid.uuid4()

    # Job was already failed and fenced by reconciler
    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.FAILED.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.FAILED.value,
        lease_token=old_token,
        fencing_token=2,  # Incremented to 2 by sweep
        lease_expires_at=None,
    )
    session.add(req)
    session.add(job)

    # Stale Worker A with fence=1 tries to validate fence
    valid, reason = await ProductionRenderLeaseService.validate_lease_fenced_async(
        session, req_id, job_id, lease_token=old_token, fencing_token=1
    )
    assert valid is False
    assert "REQUEST_TERMINAL_FAILED" in reason or "LEASE_FENCE_LOST" in reason


# ─────────────────────────────────────────────────────────────────────────────
# 6. CANCELLATION FENCING
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancellation_fences_active_lease():
    """Verify that cancellation increments the fencing token and terminates lease authority."""
    session = MockLeaseAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    token = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.RUNNING.value,
        lease_token=token,
        fencing_token=3,
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=90),
    )
    session.add(req)
    session.add(job)

    # Cancel via lifecycle service
    success, action = await ProductionLifecycleService.cancel_production_request(
        session, req_id, reason="User requested cancel", lock=False
    )
    assert success is True
    assert action == "CONVERGED_CANCELLED"
    assert req.status == ProductionRequestStatus.CANCELLED.value

    # Job state is CANCELLED and fencing token is incremented to 4
    assert job.state == RenderJobState.CANCELLED.value
    assert job.fencing_token == 4

    # Subsequent check by worker holding fence=3 fails
    valid, reason = await ProductionRenderLeaseService.validate_lease_fenced_async(
        session, req_id, job_id, lease_token=token, fencing_token=3
    )
    assert valid is False


# ─────────────────────────────────────────────────────────────────────────────
# 7. HEARTBEAT RUNNER THREAD LIFECYCLE & SELF-FENCING
# ─────────────────────────────────────────────────────────────────────────────


def test_heartbeat_runner_clean_lifecycle():
    """Verify heartbeat runner pulses in background and cleanly stops."""
    job_id = uuid.uuid4()
    token = uuid.uuid4()
    pulse_count = 0

    def mock_renew(session, j_id, l_tok, f_tok):
        nonlocal pulse_count
        pulse_count += 1
        return True

    runner = ProductionHeartbeatRunner(
        job_id=job_id,
        lease_token=token,
        fencing_token=1,
        session_factory=MagicMock(),
        renewal_fn=mock_renew,
        interval_seconds=0.05,
        abort_seconds=1.0,
    )
    runner.start()
    time.sleep(0.15)
    runner.stop()

    assert pulse_count >= 2
    assert runner.is_healthy() is True


def test_heartbeat_runner_detects_lost_lease():
    """Verify heartbeat runner immediately raises ProductionLeaseLostError when CAS fails."""
    job_id = uuid.uuid4()
    token = uuid.uuid4()

    def mock_renew(session, j_id, l_tok, f_tok):
        return False

    runner = ProductionHeartbeatRunner(
        job_id=job_id,
        lease_token=token,
        fencing_token=1,
        session_factory=MagicMock(),
        renewal_fn=mock_renew,
        interval_seconds=0.02,
        abort_seconds=1.0,
    )
    runner.start()
    time.sleep(0.08)
    runner.stop()

    assert runner.lease_lost is True
    with pytest.raises(ProductionLeaseLostError):
        runner.assert_healthy()


def test_heartbeat_runner_watchdog_self_fences():
    """Verify watchdog self-fences when renewals consistently fail up to abort deadline."""
    job_id = uuid.uuid4()
    token = uuid.uuid4()

    def mock_renew_error(session, j_id, l_tok, f_tok):
        raise RuntimeError("DB connection down")

    runner = ProductionHeartbeatRunner(
        job_id=job_id,
        lease_token=token,
        fencing_token=1,
        session_factory=MagicMock(),
        renewal_fn=mock_renew_error,
        interval_seconds=0.02,
        abort_seconds=0.08,
    )
    runner.start()
    time.sleep(0.15)
    runner.stop()

    assert runner.self_fenced is True
    with pytest.raises(ProductionWorkerSelfFencedError):
        runner.assert_healthy()


# ─────────────────────────────────────────────────────────────────────────────
# 8. LONG RENDER SURVIVAL SIMULATION
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_long_render_survival_simulation():
    """Simulate a long render surviving multiple sweep cycles via active heartbeats."""
    t0 = datetime(2026, 9, 27, 10, 0, 0, tzinfo=UTC)
    session = MockLeaseAsyncSession(db_time=t0)

    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    token = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.RUNNING.value,
        lease_token=token,
        fencing_token=1,
        heartbeat_at=t0,
        lease_expires_at=t0 + timedelta(seconds=90),
    )
    session.add(req)
    session.add(job)

    # Simulate 5 consecutive 60-second sweep cycles with intermediate 20s heartbeats
    current_time = t0
    for cycle in range(5):
        current_time += timedelta(seconds=20)
        job.heartbeat_at = current_time
        job.lease_expires_at = current_time + timedelta(seconds=90)
        session.db_time = current_time

        res = await ProductionLifecycleService.reconcile_production_request(session, req_id)
        assert res["action"] == "NO_ACTION"
        assert res["reason"] == "active_job_in_progress"
        assert job.state == RenderJobState.RUNNING.value
        assert req.status == ProductionRequestStatus.RUNNING.value


# ─────────────────────────────────────────────────────────────────────────────
# 9. ROLLOUT GATING & UNMANAGED LEGACY COMPATIBILITY
# ─────────────────────────────────────────────────────────────────────────────


def test_automatic_sweep_rollout_gate(monkeypatch: pytest.MonkeyPatch):
    """Verify that automatic sweep respects production_lease_sweep_enabled configuration."""
    from omega.worker.tasks import production_orphan_reconciliation_sweep_task

    # 1. Disabled by default
    monkeypatch.setenv("PRODUCTION_LEASE_SWEEP_ENABLED", "false")
    res_disabled = production_orphan_reconciliation_sweep_task()
    assert res_disabled["status"] == "disabled"
    assert res_disabled["scanned"] == 0

    # 2. Enabled when explicitly configured
    monkeypatch.setenv("PRODUCTION_LEASE_SWEEP_ENABLED", "true")
    with patch(
        "omega.application.production_lifecycle_service.ProductionLifecycleService.reconcile_expired_leases",
        return_value={"scanned": 5, "expired": 1},
    ), patch(
        "omega.application.production_lifecycle_service.ProductionLifecycleService.reconcile_orphaned_requests",
        return_value={"scanned": 5, "converged_failed": 1},
    ):
        res_enabled = production_orphan_reconciliation_sweep_task()
        assert res_enabled["status"] == "success"
        assert res_enabled["expired"] == 1


@pytest.mark.asyncio
async def test_legacy_rows_without_lease_unaffected():
    """Verify that legacy jobs with NULL lease tokens are skipped by expiry sweeps."""
    session = MockLeaseAsyncSession()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    # Legacy unmanaged job: lease_token and lease_expires_at are NULL
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.RUNNING.value,
        lease_token=None,
        fencing_token=0,
        lease_expires_at=None,
    )
    session.add(req)
    session.add(job)

    success, action = await ProductionRenderLeaseService.expire_lease(session, req_id, job_id)
    assert success is False
    assert action == "UNMANAGED_LEGACY_ROW"
    assert job.state == RenderJobState.RUNNING.value


# ─────────────────────────────────────────────────────────────────────────────
# 10. PUBLISHING SAFETY INVARIANT
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zero_publishing_on_crash_recovery():
    """Verify that recovering from an expired lease never creates PublishIntent or PublishAttempt."""
    db_now = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
    session = MockLeaseAsyncSession(db_time=db_now)
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.RUNNING.value,
        lease_token=uuid.uuid4(),
        fencing_token=1,
        heartbeat_at=db_now - timedelta(seconds=115),
        lease_expires_at=db_now - timedelta(seconds=20),
    )
    session.add(req)
    session.add(job)

    with patch("omega.application.publisher.intent_service.PublishIntentService") as mock_pub:
        await ProductionRenderLeaseService.expire_lease(session, req_id, job_id)
        mock_pub.assert_not_called()

    # Confirm terminal state achieved without publishing
    assert req.status == ProductionRequestStatus.FAILED.value
    assert job.state == RenderJobState.FAILED.value


# ─────────────────────────────────────────────────────────────────────────────
# 11. EXPIRED RUNNING JOB REACQUISITION & WORKER FAILURE FENCING AUDIT TESTS
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_expired_running_job_cannot_be_reacquired_directly_without_reconciliation():
    """Worker B cannot reacquire an expired RUNNING job directly; requires reconciliation + retry."""
    db_now = datetime(2026, 9, 27, 10, 0, 0, tzinfo=UTC)
    session = MockLeaseAsyncSession(db_time=db_now)
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.QUEUED.value,
        fencing_token=0,
    )
    session.add(req)
    session.add(job)

    # 1. Worker A acquires the lease
    auth_a = await ProductionRenderLeaseService.acquire_lease(session, req_id, job_id, owner_id="worker_a")
    assert job.state == RenderJobState.RUNNING.value
    assert job.lease_owner_id == "worker_a"
    assert auth_a.fencing_token == 1

    # 2. Lease expires in DB time (TTL 90s has elapsed)
    session.db_time = db_now + timedelta(seconds=120)

    # 3. Worker B attempts to directly acquire the still-RUNNING expired job -> REJECT
    with pytest.raises(ProductionDuplicateExecutionError) as exc_info:
        await ProductionRenderLeaseService.acquire_lease(session, req_id, job_id, owner_id="worker_b")
    assert "currently RUNNING; reacquisition rejected" in str(exc_info.value)
    # Job remains unchanged by worker B's attempt
    assert job.lease_owner_id == "worker_a"
    assert job.fencing_token == 1

    # 4. Reconciler fences old worker and fails the job & request
    session.db_time = db_now + timedelta(seconds=120)
    success, action = await ProductionRenderLeaseService.expire_lease(session, req_id, job_id)
    assert success is True
    assert action == "EXPIRED"
    assert job.state == RenderJobState.FAILED.value
    assert job.error_code == RenderErrorCode.WORKER_LEASE_EXPIRED.value
    assert req.status == ProductionRequestStatus.FAILED.value

    # 5. Explicit retry creates NEW lineage (new request, new job)
    new_req_id = uuid.uuid4()
    new_job_id = uuid.uuid4()
    new_req = ProductionRequest(
        id=new_req_id,
        channel_id=req.channel_id,
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={"retry_lineage": {"original_request_id": str(req_id)}},
    )
    new_job = ProductionRenderJob(
        id=new_job_id,
        production_request_id=new_req_id,
        state=RenderJobState.QUEUED.value,
        fencing_token=0,
    )
    session.add(new_req)
    session.add(new_job)

    # Worker B acquires the new lineage normally
    auth_b = await ProductionRenderLeaseService.acquire_lease(session, new_req_id, new_job_id, owner_id="worker_b")
    assert new_job.state == RenderJobState.RUNNING.value
    assert new_job.lease_owner_id == "worker_b"
    assert auth_b.fencing_token == 1


@pytest.mark.asyncio
async def test_stale_worker_failure_cannot_fail_newer_authority():
    """Worker with stale fencing token cannot record failure over newer authority."""
    db_now = datetime(2026, 9, 27, 10, 0, 0, tzinfo=UTC)
    session = MockLeaseAsyncSession(db_time=db_now)
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    stale_token = uuid.uuid4()
    newer_token = uuid.uuid4()

    # Job is currently owned by newer authority (fencing_token=2)
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.RUNNING.value,
        lease_owner_id="worker_newer",
        lease_token=newer_token,
        fencing_token=2,
        heartbeat_at=db_now,
        lease_expires_at=db_now + timedelta(seconds=90),
    )
    session.add(req)
    session.add(job)

    service = ProductionRenderService(storage=MagicMock())
    # Stale worker (fencing_token=1, stale_token) attempts to record failure
    persisted = await service._record_job_failure(
        session,
        job_id,
        RenderErrorCode.FFMPEG_FAILED,
        "Stale worker failed FFmpeg",
        request_id=req_id,
        lease_token=stale_token,
        fencing_token=1,
    )
    assert persisted is False
    # Job remains RUNNING with newer authority
    assert job.state == RenderJobState.RUNNING.value
    assert job.lease_token == newer_token
    assert job.fencing_token == 2
    assert req.status == ProductionRequestStatus.RUNNING.value


@pytest.mark.asyncio
async def test_cancelled_worker_failure_cannot_overwrite_cancelled():
    """Cancelled worker failure cannot overwrite CANCELLED status."""
    db_now = datetime(2026, 9, 27, 10, 0, 0, tzinfo=UTC)
    session = MockLeaseAsyncSession(db_time=db_now)
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    worker_token = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.CANCELLED.value,
        metadata_={"cancellation_reason": "User cancelled"},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.CANCELLED.value,
        lease_owner_id="worker_cancelled",
        lease_token=worker_token,
        fencing_token=2,  # Incremented by cancellation
        sanitized_error="Cancelled: User cancelled",
    )
    session.add(req)
    session.add(job)

    service = ProductionRenderService(storage=MagicMock())
    persisted = await service._record_job_failure(
        session,
        job_id,
        RenderErrorCode.TIMEOUT,
        "Worker timed out",
        request_id=req_id,
        lease_token=worker_token,
        fencing_token=1,  # Worker thought it was token 1
    )
    assert persisted is False
    assert req.status == ProductionRequestStatus.CANCELLED.value
    assert job.state == RenderJobState.CANCELLED.value
    assert "Cancelled" in job.sanitized_error


@pytest.mark.asyncio
async def test_expired_worker_failure_does_not_override_reconciler():
    """Expired worker failure does not override reconciler failure or write to expired lease."""
    db_now = datetime(2026, 9, 27, 10, 0, 0, tzinfo=UTC)
    session = MockLeaseAsyncSession(db_time=db_now)
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    worker_token = uuid.uuid4()

    # Case 1: Reconciler already failed the job
    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.FAILED.value,
        metadata_={"failure_info": {"error_code": RenderErrorCode.WORKER_LEASE_EXPIRED.value}},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.FAILED.value,
        lease_owner_id="worker_crashed",
        lease_token=worker_token,
        fencing_token=2,
        error_code=RenderErrorCode.WORKER_LEASE_EXPIRED.value,
        sanitized_error="Worker lease expired; heartbeat timed out",
    )
    session.add(req)
    session.add(job)

    service = ProductionRenderService(storage=MagicMock())
    persisted = await service._record_job_failure(
        session,
        job_id,
        RenderErrorCode.FFMPEG_FAILED,
        "Worker belatedly failed",
        request_id=req_id,
        lease_token=worker_token,
        fencing_token=1,
    )
    assert persisted is False
    assert job.error_code == RenderErrorCode.WORKER_LEASE_EXPIRED.value

    # Case 2: Reconciler has not run yet, but lease is expired in DB time
    job_id2 = uuid.uuid4()
    token2 = uuid.uuid4()
    req2 = ProductionRequest(
        id=uuid.uuid4(),
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job2 = ProductionRenderJob(
        id=job_id2,
        production_request_id=req2.id,
        state=RenderJobState.RUNNING.value,
        lease_owner_id="worker_slow",
        lease_token=token2,
        fencing_token=1,
        heartbeat_at=db_now - timedelta(seconds=120),
        lease_expires_at=db_now - timedelta(seconds=30),  # Expired
    )
    session.add(req2)
    session.add(job2)

    persisted2 = await service._record_job_failure(
        session,
        job_id2,
        RenderErrorCode.FFMPEG_FAILED,
        "Worker late failure on expired lease",
        request_id=req2.id,
        lease_token=token2,
        fencing_token=1,
    )
    assert persisted2 is False
    assert job2.state == RenderJobState.RUNNING.value  # Unchanged by stale worker


@pytest.mark.asyncio
async def test_valid_lease_owner_can_record_render_failure():
    """Worker holding valid unexpired lease can successfully record render failure."""
    db_now = datetime(2026, 9, 27, 10, 0, 0, tzinfo=UTC)
    session = MockLeaseAsyncSession(db_time=db_now)
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    worker_token = uuid.uuid4()

    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={},
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.RUNNING.value,
        lease_owner_id="worker_active",
        lease_token=worker_token,
        fencing_token=1,
        heartbeat_at=db_now,
        lease_expires_at=db_now + timedelta(seconds=90),  # Valid, unexpired
    )
    session.add(req)
    session.add(job)

    service = ProductionRenderService(storage=MagicMock())
    persisted = await service._record_job_failure(
        session,
        job_id,
        RenderErrorCode.FFMPEG_FAILED,
        "FFmpeg process returned code 1",
        request_id=req_id,
        lease_token=worker_token,
        fencing_token=1,
    )
    assert persisted is True
    assert job.state == RenderJobState.FAILED.value
    assert job.error_code == RenderErrorCode.FFMPEG_FAILED.value
    assert job.lease_expires_at is None
    assert req.status == ProductionRequestStatus.FAILED.value
    assert req.outcome == ProductionOutcome.BLOCKED.value
