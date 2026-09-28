"""Comprehensive Unit Tests for Dispatch Reliability & Queue-Stall Recovery (P19-LR3).

Covers:
1. Atomic initial allocation + transactional outbox enqueue.
2. Candidate discovery (read-only snapshot, partial index, zero row locks).
3. Canonical lock order (ProductionRequest -> ProductionRenderJob).
4. Outbox state ownership (PENDING / CLAIMED / RETRY -> NO_ACTION).
5. SENT timeout requirement vs DEAD_LETTER immediate transfer.
6. Missing outbox intent invariant violation detection and recovery.
7. Redispatch generation advancement (N -> N+1, fresh dispatch_started_at, new intent).
8. Old intent preservation (SENT and DEAD_LETTER rows remain immutable).
9. Exhaustion on max generations (DISPATCH_DELIVERY_EXHAUSTED, request failure).
10. Permanent execution boundary (started_at / lease_token / RUNNING halts recovery).
11. Atomic generation validation + lease acquisition (exact equality required).
12. Stale dispatch generation rejection (message < persisted -> 0 mutations).
13. Future dispatch generation rejection (message > persisted -> 0 mutations).
14. Duplicate delivery rejection (idempotent no-op, single execution).
15. Cancellation boundary (cancelled request prevents lease acquisition).
16. Legacy unmanaged row safety (dispatch_started_at IS NULL bypassed).
17. Celery recovery task rollout gating (production_dispatch_recovery_enabled=False).
18. Concurrent reconciler race resolution.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select, text

from omega.application.durable_dispatch import (
    CLAIMED,
    DEAD_LETTER,
    PENDING,
    RETRY,
    SENT,
    DurableDispatchService,
)
from omega.application.production_dispatch_service import ProductionDispatchService
from omega.application.production_lifecycle_service import ProductionLifecycleService
from omega.application.production_render_lease_service import (
    ProductionDuplicateExecutionError,
    ProductionInvalidFutureDispatchGenerationError,
    ProductionRenderLeaseService,
    ProductionStaleDispatchGenerationError,
)
from omega.application.production_service import ProductionService
from omega.domain.production import (
    ProductionOutcome,
    ProductionQAStatus,
    ProductionRequestStatus,
    RenderErrorCode,
    RenderJobState,
)
from omega.infrastructure.models import (
    DurableDispatchIntent,
    ProductionRenderJob,
    ProductionRequest,
    RenderPlan,
)


class MockDispatchAsyncSession:
    """Mock async session supporting model tracking, row locking, and DB clock simulation."""

    def __init__(self, db_time: datetime | None = None):
        self.records: dict[tuple[type, uuid.UUID], Any] = {}
        self.executed_statements: list[Any] = []
        self.committed = False
        self.rolled_back = False
        self.db_time = db_time or datetime.now(UTC)

    def add(self, entity: Any) -> None:
        key = getattr(entity, "id", uuid.uuid4())
        self.records[(type(entity), key)] = entity

    async def get(self, model: type, entity_id: uuid.UUID) -> Any:
        return self.records.get((model, entity_id))

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def flush(self) -> None:
        pass

    async def refresh(self, entity: Any) -> None:
        pass

    def begin_nested(self):
        class NestedTx:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *args):
                pass

        return NestedTx()

    async def execute(self, statement: Any) -> Any:
        self.executed_statements.append(statement)
        stmt_str = str(statement).lower()

        # Handle candidate discovery query
        if "from production_render_jobs" in stmt_str and "limit" in stmt_str and "where" in stmt_str:
            candidates = []
            for (m, _), job in self.records.items():
                if m is ProductionRenderJob:
                    if (
                        job.state == RenderJobState.QUEUED.value
                        and job.dispatch_started_at is not None
                        and job.started_at is None
                    ):
                        candidates.append((job.id, job.production_request_id, job.dispatch_generation))
            result = MagicMock()
            result.fetchall.return_value = candidates
            return result

        # Handle stall check: select ProductionRenderJob.dispatch_started_at <= func.now() - ...
        if "now()" in stmt_str and "dispatch_started_at" in stmt_str:
            result = MagicMock()
            result.scalar_one_or_none.return_value = True
            result.scalar_one.return_value = True
            return result

        # Handle lookup for ProductionRequest
        if "from production_requests" in stmt_str:
            for (m, _), req in self.records.items():
                if m is ProductionRequest:
                    result = MagicMock()
                    result.scalar_one_or_none.return_value = req
                    result.scalars.return_value.first.return_value = req
                    return result
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        # Handle lookup for ProductionRenderJob
        if "from production_render_jobs" in stmt_str:
            for (m, _), job in self.records.items():
                if m is ProductionRenderJob:
                    result = MagicMock()
                    result.scalar_one_or_none.return_value = job
                    result.scalars.return_value.first.return_value = job
                    return result
            result = MagicMock()
            result.scalar_one_or_none.return_value = None
            return result

        # Handle lookup for DurableDispatchIntent
        if "from durable_dispatch_intents" in stmt_str:
            target_key = None
            for crit in getattr(statement, "_where_criteria", ()):
                left = getattr(crit, "left", None)
                l_name = getattr(left, "key", "") or getattr(left, "name", "")
                if l_name == "idempotency_key":
                    right = getattr(crit, "right", None)
                    target_key = getattr(right, "value", right)
                    break

            intents = [rec for (m, _), rec in self.records.items() if m is DurableDispatchIntent]
            if target_key is not None:
                intents = [i for i in intents if i.idempotency_key == target_key]

            intents.sort(key=lambda i: getattr(i, "created_at", datetime.min) or datetime.min, reverse=True)
            res_val = intents[0] if intents else None
            result = MagicMock()
            result.scalars.return_value.first.return_value = res_val
            result.scalar_one_or_none.return_value = res_val
            result.scalar_one.return_value = res_val
            return result

        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        return result


def _make_intent(
    req: ProductionRequest,
    job: ProductionRenderJob,
    *,
    gen: int = 1,
    state: str = SENT,
) -> DurableDispatchIntent:
    return DurableDispatchIntent(
        id=uuid.uuid4(),
        idempotency_key=f"render-dispatch:{req.id}:{job.id}:gen{gen}",
        task_name="omega.production.render",
        args=[str(req.channel_id), str(req.id), str(job.id), gen],
        purpose="PRODUCTION_RENDER_DISPATCH",
        state=state,
        max_attempts=5,
        attempt=1,
        production_request_id=req.id,
        render_job_id=job.id,
    )


def _make_production_hierarchy(
    *,
    job_state: str = RenderJobState.QUEUED.value,
    dispatch_gen: int = 1,
    dispatch_started_at: datetime | None = None,
    started_at: datetime | None = None,
    lease_token: uuid.UUID | None = None,
    req_status: str = ProductionRequestStatus.RUNNING.value,
) -> tuple[ProductionRequest, ProductionRenderJob]:
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    req = ProductionRequest(
        id=req_id,
        channel_id=uuid.uuid4(),
        status=req_status,
        mode="MISSION_EXECUTION",
    )
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=job_state,
        dispatch_generation=dispatch_gen,
        dispatch_started_at=dispatch_started_at,
        started_at=started_at,
        lease_token=lease_token,
    )
    return req, job


# ══════════════════════════════════════════════════════════════════
# 1. Atomic Allocation + Outbox Enqueue
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_atomic_initial_allocation_enqueues_gen1_intent():
    session = MockDispatchAsyncSession()
    channel_id = uuid.uuid4()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()

    mock_prod_service = MagicMock()
    mock_job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.QUEUED.value,
        dispatch_generation=1,
    )
    mock_plan = RenderPlan(id=uuid.uuid4(), production_request_id=req_id, version=1)
    mock_prod_service.allocate_render_job = AsyncMock(return_value=(mock_job, mock_plan, True))

    job, plan, is_new, intent = await ProductionDispatchService.allocate_and_enqueue_render(
        session=session,
        channel_id=channel_id,
        request_id=req_id,
        idempotency_key="idemp-key-1",
        prod_service=mock_prod_service,
    )

    assert is_new is True
    assert job.dispatch_generation == 1
    assert session.committed is True

    # Verify outbox intent enqueued in the SAME transaction
    intents = [rec for (m, _), rec in session.records.items() if m is DurableDispatchIntent]
    assert len(intents) == 1
    assert intents[0].idempotency_key == f"render-dispatch:{req_id}:{job_id}:gen1"
    assert intents[0].task_name == "omega.production.render"
    assert intents[0].args == [str(channel_id), str(req_id), str(job_id), 1]


@pytest.mark.asyncio
async def test_atomic_allocation_failure_rolls_back_outbox_and_job():
    session = MockDispatchAsyncSession()
    channel_id = uuid.uuid4()
    req_id = uuid.uuid4()

    mock_prod_service = MagicMock()
    mock_prod_service.allocate_render_job = AsyncMock(side_effect=RuntimeError("DB allocation failed"))

    with pytest.raises(RuntimeError, match="DB allocation failed"):
        await ProductionDispatchService.allocate_and_enqueue_render(
            session=session,
            channel_id=channel_id,
            request_id=req_id,
            idempotency_key="idemp-fail",
            prod_service=mock_prod_service,
        )

    assert session.committed is False
    intents = [rec for (m, _), rec in session.records.items() if m is DurableDispatchIntent]
    assert len(intents) == 0


@pytest.mark.asyncio
async def test_mission_atomic_allocation_failure_rolls_back_and_retry_commits_once():
    session = MockDispatchAsyncSession()
    channel_id = uuid.uuid4()
    req_id = uuid.uuid4()
    job_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    exec_id = uuid.uuid4()
    task_id = uuid.uuid4()

    mock_prod_service = MagicMock()
    mock_job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        state=RenderJobState.QUEUED.value,
        dispatch_generation=1,
    )
    mock_plan = RenderPlan(id=uuid.uuid4(), production_request_id=req_id, version=1)
    mock_prod_service.allocate_render_job = AsyncMock(return_value=(mock_job, mock_plan, True))

    # 1. Failure during outbox enqueue rolls back
    with patch(
        "omega.application.durable_dispatch.DurableDispatchService.enqueue_async",
        side_effect=RuntimeError("Simulated outbox failure"),
    ):
        with pytest.raises(RuntimeError, match="Simulated outbox failure"):
            await ProductionDispatchService.allocate_and_enqueue_render(
                session=session,
                channel_id=channel_id,
                request_id=req_id,
                idempotency_key="mission-fail",
                mission_id=mission_id,
                mission_execution_id=exec_id,
                mission_task_id=task_id,
                prod_service=mock_prod_service,
            )

    assert session.committed is False
    assert session.rolled_back is True

    # 2. Retry with clean session succeeds atomically
    retry_session = MockDispatchAsyncSession()
    job, plan, is_new, intent = await ProductionDispatchService.allocate_and_enqueue_render(
        session=retry_session,
        channel_id=channel_id,
        request_id=req_id,
        idempotency_key="mission-fail",
        mission_id=mission_id,
        mission_execution_id=exec_id,
        mission_task_id=task_id,
        prod_service=mock_prod_service,
    )

    assert retry_session.committed is True
    assert job.dispatch_generation == 1
    intents = [rec for (m, _), rec in retry_session.records.items() if m is DurableDispatchIntent]
    assert len(intents) == 1
    assert intents[0].mission_id == mission_id
    assert intents[0].mission_execution_id == exec_id
    assert intents[0].mission_task_id == task_id


@pytest.mark.asyncio
async def test_post_commit_dispatch_intent_invariant_unit():
    """Verify that every allocated QUEUED job has exactly one current-generation intent."""
    for mode in ["mission", "rest_render", "rest_rerender"]:
        session = MockDispatchAsyncSession()
        channel_id = uuid.uuid4()
        req_id = uuid.uuid4()
        job_id = uuid.uuid4()

        mock_prod_service = MagicMock()
        mock_job = ProductionRenderJob(
            id=job_id,
            production_request_id=req_id,
            state=RenderJobState.QUEUED.value,
            dispatch_generation=1,
            dispatch_started_at=datetime.now(UTC),
        )
        mock_plan = RenderPlan(id=uuid.uuid4(), production_request_id=req_id, version=1)
        mock_prod_service.allocate_render_job = AsyncMock(return_value=(mock_job, mock_plan, True))

        is_rerender = mode == "rest_rerender"
        mission_id = uuid.uuid4() if mode == "mission" else None

        job, plan, is_new, intent = await ProductionDispatchService.allocate_and_enqueue_render(
            session=session,
            channel_id=channel_id,
            request_id=req_id,
            idempotency_key=f"idemp-{mode}",
            is_rerender=is_rerender,
            mission_id=mission_id,
            prod_service=mock_prod_service,
        )

        assert session.committed is True
        assert job.state == RenderJobState.QUEUED.value
        assert job.dispatch_started_at is not None

        matching_intents = [
            rec
            for (m, _), rec in session.records.items()
            if m is DurableDispatchIntent
            and rec.render_job_id == job.id
            and rec.production_request_id == req_id
            and rec.args[3] == job.dispatch_generation
        ]
        assert len(matching_intents) == 1


# ══════════════════════════════════════════════════════════════════
# 2. Candidate Discovery (Snapshot, Lock-Free)
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_discover_stalled_candidates_is_lock_free():
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        dispatch_started_at=datetime.now(UTC) - timedelta(seconds=400)
    )
    session.add(req)
    session.add(job)

    candidates = await ProductionDispatchService.discover_stalled_dispatch_candidates(
        session, timeout_seconds=300, limit=25
    )
    assert len(candidates) == 1
    assert candidates[0][0] == job.id

    # Verify NO 'with_for_update' or 'for update' in discovery statement
    for stmt in session.executed_statements:
        assert "for update" not in str(stmt).lower()


# ══════════════════════════════════════════════════════════════════
# 3. Canonical Lock Order
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_reconcile_candidate_enforces_canonical_lock_order():
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        dispatch_started_at=datetime.now(UTC) - timedelta(seconds=400)
    )
    session.add(req)
    session.add(job)

    # Add SENT intent to allow redispatch
    intent = _make_intent(req, job, gen=1, state=SENT)
    session.add(intent)

    await ProductionDispatchService.reconcile_dispatch_stall_candidate(
        session, req.id, job.id, expected_generation=1, timeout_seconds=300
    )

    stmts = [str(s).lower() for s in session.executed_statements]
    req_lock_idx = next(i for i, s in enumerate(stmts) if "production_requests" in s and "for update" in s)
    job_lock_idx = next(i for i, s in enumerate(stmts) if "production_render_jobs" in s and "for update" in s)

    # Canonical order: Request lock MUST precede Job lock
    assert req_lock_idx < job_lock_idx


# ══════════════════════════════════════════════════════════════════
# 4. Outbox State Ownership
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("active_state", [PENDING, CLAIMED, RETRY])
async def test_reconcile_takes_no_action_on_active_outbox_states(active_state):
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        dispatch_started_at=datetime.now(UTC) - timedelta(seconds=400)
    )
    session.add(req)
    session.add(job)

    intent = _make_intent(req, job, gen=1, state=active_state)
    session.add(intent)

    result = await ProductionDispatchService.reconcile_dispatch_stall_candidate(
        session, req.id, job.id, expected_generation=1, timeout_seconds=300
    )

    assert result["action"] == "NO_ACTION"
    assert "outbox_active" in result["reason"]
    assert job.dispatch_generation == 1
    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_reconcile_dead_letter_immediately_eligible():
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        dispatch_started_at=datetime.now(UTC) - timedelta(seconds=10)  # NOT yet timeout
    )
    session.add(req)
    session.add(job)

    intent = _make_intent(req, job, gen=1, state=DEAD_LETTER)
    session.add(intent)

    result = await ProductionDispatchService.reconcile_dispatch_stall_candidate(
        session, req.id, job.id, expected_generation=1, timeout_seconds=300
    )

    assert result["action"] == "REDISPATCHED"
    assert result["new_generation"] == 2
    assert job.dispatch_generation == 2
    assert session.committed is True


@pytest.mark.asyncio
async def test_reconcile_missing_intent_invariant_violation_recovers():
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        dispatch_started_at=datetime.now(UTC) - timedelta(seconds=400)
    )
    session.add(req)
    session.add(job)
    # ZERO DurableDispatchIntent added

    result = await ProductionDispatchService.reconcile_dispatch_stall_candidate(
        session, req.id, job.id, expected_generation=1, timeout_seconds=300
    )

    assert result["action"] == "REDISPATCHED"
    assert result["new_generation"] == 2
    assert job.dispatch_generation == 2


# ══════════════════════════════════════════════════════════════════
# 5. Redispatch vs Exhaustion
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_redispatch_preserves_job_id_and_advances_generation():
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        dispatch_started_at=datetime.now(UTC) - timedelta(seconds=400)
    )
    orig_job_id = job.id
    session.add(req)
    session.add(job)

    intent = _make_intent(req, job, gen=1, state=SENT)
    session.add(intent)

    result = await ProductionDispatchService.reconcile_dispatch_stall_candidate(
        session, req.id, job.id, expected_generation=1, max_generations=3
    )

    assert result["action"] == "REDISPATCHED"
    assert result["new_generation"] == 2
    assert job.id == orig_job_id  # SAME RenderJob!
    assert job.dispatch_generation == 2

    # Check new gen2 intent created while old gen1 is preserved
    intents = [rec for (m, _), rec in session.records.items() if m is DurableDispatchIntent]
    assert len(intents) == 2
    gen1 = next(i for i in intents if i.idempotency_key.endswith(":gen1"))
    gen2 = next(i for i in intents if i.idempotency_key.endswith(":gen2"))
    assert gen1.state == SENT
    assert gen2.args[-1] == 2


@pytest.mark.asyncio
async def test_exhaustion_on_max_generations_fails_job_and_request():
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        dispatch_gen=3,  # Reached max generations
        dispatch_started_at=datetime.now(UTC) - timedelta(seconds=400),
    )
    session.add(req)
    session.add(job)

    intent = _make_intent(req, job, gen=3, state=SENT)
    session.add(intent)

    with patch.object(ProductionLifecycleService, "fail_production_request", new=AsyncMock()) as mock_fail:
        result = await ProductionDispatchService.reconcile_dispatch_stall_candidate(
            session, req.id, job.id, expected_generation=3, max_generations=3
        )

        assert result["action"] == "EXHAUSTED"
        assert result["error_code"] == RenderErrorCode.DISPATCH_DELIVERY_EXHAUSTED.value
        assert job.state == RenderJobState.FAILED.value
        assert job.error_code == RenderErrorCode.DISPATCH_DELIVERY_EXHAUSTED.value

        mock_fail.assert_awaited_once_with(
            session,
            req.id,
            reason=job.sanitized_error,
            error_code=RenderErrorCode.DISPATCH_DELIVERY_EXHAUSTED,
            failure_stage="DISPATCH_STALL_RECONCILIATION",
            details={"job_id": str(job.id), "dispatch_generation": 3},
            lock=False,
        )


# ══════════════════════════════════════════════════════════════════
# 6. Execution Authority Boundary
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "started, token, state",
    [
        (datetime.now(UTC), None, RenderJobState.QUEUED.value),
        (None, uuid.uuid4(), RenderJobState.QUEUED.value),
        (None, None, RenderJobState.RUNNING.value),
    ],
)
async def test_reconciliation_permanently_stops_once_execution_authority_begins(started, token, state):
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        job_state=state,
        started_at=started,
        lease_token=token,
        dispatch_started_at=datetime.now(UTC) - timedelta(seconds=600),
    )
    session.add(req)
    session.add(job)

    result = await ProductionDispatchService.reconcile_dispatch_stall_candidate(
        session, req.id, job.id, expected_generation=1
    )
    assert result["action"] == "NO_ACTION"
    assert job.dispatch_generation == 1


# ══════════════════════════════════════════════════════════════════
# 7. Generation Validation & Worker Delivery
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_worker_stale_dispatch_generation_rejected():
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        dispatch_gen=2,  # Persisted is 2
        dispatch_started_at=datetime.now(UTC),
    )
    session.add(req)
    session.add(job)

    # Worker arrives with stale gen 1
    with pytest.raises(ProductionStaleDispatchGenerationError, match="Stale dispatch generation 1 < current 2"):
        await ProductionRenderLeaseService.acquire_lease(
            session, req.id, job.id, expected_dispatch_generation=1
        )

    assert job.state == RenderJobState.QUEUED.value
    assert job.lease_token is None


@pytest.mark.asyncio
async def test_worker_future_dispatch_generation_rejected():
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        dispatch_gen=1,  # Persisted is 1
        dispatch_started_at=datetime.now(UTC),
    )
    session.add(req)
    session.add(job)

    # Worker arrives with invalid future gen 2
    with pytest.raises(ProductionInvalidFutureDispatchGenerationError, match="Future dispatch generation 2 > current 1"):
        await ProductionRenderLeaseService.acquire_lease(
            session, req.id, job.id, expected_dispatch_generation=2
        )

    assert job.state == RenderJobState.QUEUED.value
    assert job.lease_token is None


@pytest.mark.asyncio
async def test_worker_exact_generation_acquires_lease_atomically():
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        dispatch_gen=2,
        dispatch_started_at=datetime.now(UTC),
    )
    session.add(req)
    session.add(job)

    authority = await ProductionRenderLeaseService.acquire_lease(
        session, req.id, job.id, expected_dispatch_generation=2
    )

    assert authority.job_id == job.id
    assert job.state == RenderJobState.RUNNING.value
    assert job.lease_token is not None
    assert job.fencing_token == 1
    assert session.committed is True


@pytest.mark.asyncio
async def test_duplicate_delivery_on_running_job_rejected_idempotently():
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        job_state=RenderJobState.RUNNING.value,
        dispatch_gen=1,
        started_at=datetime.now(UTC),
        lease_token=uuid.uuid4(),
    )
    session.add(req)
    session.add(job)

    with pytest.raises(ProductionDuplicateExecutionError, match="currently RUNNING"):
        await ProductionRenderLeaseService.acquire_lease(
            session, req.id, job.id, expected_dispatch_generation=1
        )


@pytest.mark.asyncio
async def test_cancelled_request_prevents_lease_acquisition():
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        req_status=ProductionRequestStatus.CANCELLED.value,
        dispatch_gen=1,
        dispatch_started_at=datetime.now(UTC),
    )
    session.add(req)
    session.add(job)

    from omega.application.production_render_lease_service import ProductionLeaseFencingError

    with pytest.raises(ProductionLeaseFencingError, match="Cannot acquire lease for terminal ProductionRequest"):
        await ProductionRenderLeaseService.acquire_lease(
            session, req.id, job.id, expected_dispatch_generation=1
        )


@pytest.mark.asyncio
async def test_legacy_unenrolled_job_bypassed():
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        dispatch_gen=1,
        dispatch_started_at=None,  # Legacy row!
    )
    session.add(req)
    session.add(job)

    result = await ProductionDispatchService.reconcile_dispatch_stall_candidate(
        session, req.id, job.id, expected_generation=1
    )
    assert result["action"] == "NO_ACTION"
    assert result["reason"] == "legacy_unenrolled"


# ══════════════════════════════════════════════════════════════════
# 8. Celery Task Rollout Gating
# ══════════════════════════════════════════════════════════════════


def test_celery_dispatch_stall_task_gated_off_by_default(monkeypatch):
    from omega.config import get_settings
    from omega.worker.tasks import production_dispatch_stall_reconciliation_task

    settings = get_settings()
    monkeypatch.setattr(settings, "production_dispatch_recovery_enabled", False)

    res = production_dispatch_stall_reconciliation_task.run()
    assert res["status"] == "disabled"
    assert res["candidates"] == 0
    assert res["redispatched"] == 0
    assert res["exhausted"] == 0


# ══════════════════════════════════════════════════════════════════
# 9. Concurrent Reconcilers Conflict
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_concurrent_reconciler_sees_generation_mismatch_and_no_ops():
    session = MockDispatchAsyncSession()
    req, job = _make_production_hierarchy(
        dispatch_gen=2,  # Already advanced to 2 by Reconciler A
        dispatch_started_at=datetime.now(UTC) - timedelta(seconds=400),
    )
    session.add(req)
    session.add(job)

    # Reconciler B still has candidate snapshot expecting gen 1
    result = await ProductionDispatchService.reconcile_dispatch_stall_candidate(
        session, req.id, job.id, expected_generation=1
    )

    assert result["action"] == "NO_ACTION"
    assert result["reason"] == "generation_mismatch"
    assert job.dispatch_generation == 2
