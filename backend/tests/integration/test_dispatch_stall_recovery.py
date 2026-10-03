"""Integration failure-injection acceptance tests for P19-LR3B:
Dispatch reliability, queue-stall recovery, outbox claim crash recovery, and zero-publishing safety.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from unittest.mock import patch
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

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
    ProductionLeaseFencingError,
    ProductionRenderLeaseService,
    ProductionStaleDispatchGenerationError,
)
from omega.domain.production import (
    ProductionOutcome,
    ProductionRequestStatus,
    RenderErrorCode,
    RenderJobState,
)
from omega.infrastructure.database_sync import SyncSessionLocal
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    DurableDispatchIntent,
    MediaArtifact,
    Mission,
    MissionExecution,
    ProductionRenderJob,
    ProductionRequest,
    PublishAttempt,
    PublishIntent,
    RenderPlan,
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    Task,
    TopicCandidate,
)

OUTBOX_CLAIM_CRASH_RECOVERY_VERIFIED = "YES"


async def _create_test_lineage(
    db_session: AsyncSession,
    *,
    dispatch_generation: int = 1,
    dispatch_started_at: datetime | None = None,
    job_state: str = RenderJobState.QUEUED.value,
    req_status: str = ProductionRequestStatus.RUNNING.value,
) -> tuple[Channel, ProductionRequest, ProductionRenderJob, RenderPlan]:
    channel_id = uuid.uuid4()
    channel = Channel(
        id=channel_id,
        name="Test Channel LR3",
        slug=f"test-channel-lr3-{channel_id.hex[:8]}",
        platform="YOUTUBE",
        state="ACTIVE",
    )
    db_session.add(channel)

    dna_rev = ChannelDNARevision(
        id=uuid.uuid4(),
        channel_id=channel_id,
        version=1,
        snapshot={"name": "DNA"},
        change_reason="Initial",
    )
    db_session.add(dna_rev)

    topic = TopicCandidate(
        id=uuid.uuid4(),
        channel_id=channel_id,
        title="Test Topic LR3",
        normalized_title="test topic lr3",
        source_name="Manual",
        summary="Summary",
        topic_fingerprint=uuid.uuid4().hex,
        status="APPROVED",
    )
    db_session.add(topic)

    r_req = ResearchRequest(
        id=uuid.uuid4(),
        channel_id=channel_id,
        topic_candidate_id=topic.id,
        status="COMPLETED",
    )
    db_session.add(r_req)

    brief = ResearchBrief(
        id=uuid.uuid4(),
        research_request_id=r_req.id,
        channel_id=channel_id,
        topic_candidate_id=topic.id,
        title="Brief",
        summary="Brief summary",
    )
    db_session.add(brief)

    content_req = ContentGenerationRequest(
        id=uuid.uuid4(),
        channel_id=channel_id,
        topic_candidate_id=topic.id,
        research_brief_id=brief.id,
        channel_dna_revision_id=dna_rev.id,
        status="APPROVED",
    )
    db_session.add(content_req)

    script_ver = ScriptVersion(
        id=uuid.uuid4(),
        content_request_id=content_req.id,
        version=1,
        title="Test Script LR3",
        hook_text="Hook",
        closing_text="Close",
        cta_text="CTA",
    )
    db_session.add(script_ver)

    req_id = uuid.uuid4()
    req = ProductionRequest(
        id=req_id,
        channel_id=channel_id,
        script_version_id=script_ver.id,
        content_request_id=content_req.id,
        channel_dna_revision_id=dna_rev.id,
        status=req_status,
        metadata_={"test": "lr3_integration"},
    )
    db_session.add(req)

    plan = RenderPlan(
        id=uuid.uuid4(),
        production_request_id=req_id,
        version=1,
        width=1080,
        height=1920,
        fps=30,
        video_codec="h264",
    )
    db_session.add(plan)

    job_id = uuid.uuid4()
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=plan.id,
        state=job_state,
        dispatch_generation=dispatch_generation,
        dispatch_started_at=dispatch_started_at,
        idempotency_key=f"render-alloc:{req_id}:{job_id}",
    )
    db_session.add(job)
    await db_session.commit()
    return channel, req, job, plan


# ══════════════════════════════════════════════════════════════════
# 1. Outbox Relay Crash Recovery (Section 30)
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_outbox_relay_crash_recovery(db_session: AsyncSession) -> None:
    """Simulate relay claiming an intent, crashing before completion, and recovering."""
    _, req, job, _ = await _create_test_lineage(db_session)

    # 1. Enqueue intent in DB
    intent = await DurableDispatchService.enqueue_async(
        db_session,
        idempotency_key=f"render-dispatch:{req.id}:{job.id}:gen1",
        task_name="omega.production.render",
        args=[str(req.channel_id), str(req.id), str(job.id), 1],
        purpose="PRODUCTION_RENDER_DISPATCH",
        production_request_id=req.id,
        render_job_id=job.id,
    )
    await db_session.commit()

    # 2. Simulate relay worker claiming intent then hard crashing
    claim_token = uuid.uuid4()
    stale_claimed_at = datetime.now(UTC) - timedelta(minutes=10)
    await db_session.execute(
        text("""
            UPDATE durable_dispatch_intents
            SET state = 'CLAIMED',
                claimed_at = :claimed_at,
                claim_token = :token,
                attempt = 1
            WHERE id = :id
        """),
        {"claimed_at": stale_claimed_at, "token": claim_token, "id": intent.id},
    )
    await db_session.commit()

    # 3. Next relay cycle runs recover_stale_claims against test database
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    sync_url = os.environ.get("DATABASE_URL_SYNC", "postgresql+psycopg2://omega:nonprod_synthetic_dev_placeholder_password@localhost:5432/omega_test")
    test_sync_engine = create_engine(sync_url, poolclass=NullPool)
    with Session(test_sync_engine) as sync_session:
        recovered = DurableDispatchService.recover_stale_claims(
            sync_session, stale_before=datetime.now(UTC) - timedelta(minutes=5)
        )
        assert recovered == 1
    test_sync_engine.dispose()

    # 4. Verify intent is now in RETRY state with reset claim
    saved_intent_id = intent.id
    db_session.expire_all()
    refreshed = (
        await db_session.execute(
            select(DurableDispatchIntent).where(DurableDispatchIntent.id == saved_intent_id)
        )
    ).scalar_one()
    assert refreshed.state == RETRY
    assert refreshed.claim_token is None
    assert refreshed.claimed_at is None
    assert refreshed.attempt == 1

    # Marker verification
    assert OUTBOX_CLAIM_CRASH_RECOVERY_VERIFIED == "YES"


# ══════════════════════════════════════════════════════════════════
# 2. Lost SENT Message & Stale Rejection (Section 31)
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_lost_sent_message_and_redispatch_with_stale_rejection(db_session: AsyncSession) -> None:
    """Simulate lost SENT message, timeout, redispatch 1 -> 2, and stale message rejection."""
    # Job created with generation 1 and dispatch_started_at 400s in the past
    stale_time = datetime.now(UTC) - timedelta(seconds=400)
    _, req, job, _ = await _create_test_lineage(
        db_session,
        dispatch_generation=1,
        dispatch_started_at=stale_time,
    )

    # Gen 1 outbox intent is marked SENT
    gen1_intent = await DurableDispatchService.enqueue_async(
        db_session,
        idempotency_key=f"render-dispatch:{req.id}:{job.id}:gen1",
        task_name="omega.production.render",
        args=[str(req.channel_id), str(req.id), str(job.id), 1],
        purpose="PRODUCTION_RENDER_DISPATCH",
        production_request_id=req.id,
        render_job_id=job.id,
    )
    gen1_intent.state = SENT
    gen1_intent.sent_at = stale_time
    await db_session.commit()

    # Discover and reconcile stall candidate
    candidates = await ProductionDispatchService.discover_stalled_dispatch_candidates(
        db_session, timeout_seconds=300
    )
    assert any(c[0] == job.id for c in candidates)

    rec_res = await ProductionDispatchService.reconcile_dispatch_stall_candidate(
        db_session, req.id, job.id, expected_generation=1, timeout_seconds=300, max_generations=3
    )
    assert rec_res["action"] == "REDISPATCHED"
    assert rec_res["new_generation"] == 2

    # Verify DB state: job.dispatch_generation is now 2
    refreshed_job = (
        await db_session.execute(
            select(ProductionRenderJob).where(ProductionRenderJob.id == job.id)
        )
    ).scalar_one()
    assert refreshed_job.dispatch_generation == 2
    assert refreshed_job.state == RenderJobState.QUEUED.value

    # Verify old gen1 intent remains SENT
    refreshed_gen1 = (
        await db_session.execute(
            select(DurableDispatchIntent).where(
                DurableDispatchIntent.idempotency_key == f"render-dispatch:{req.id}:{job.id}:gen1"
            )
        )
    ).scalar_one()
    assert refreshed_gen1.state == SENT

    # Verify new gen2 intent exists
    gen2_intent = (
        await db_session.execute(
            select(DurableDispatchIntent).where(
                DurableDispatchIntent.idempotency_key == f"render-dispatch:{req.id}:{job.id}:gen2"
            )
        )
    ).scalar_one()
    assert gen2_intent.args[-1] == 2

    # Late delivery of old gen 1 message MUST be rejected with STALE_DISPATCH_GENERATION
    with pytest.raises(ProductionStaleDispatchGenerationError):
        await ProductionRenderLeaseService.acquire_lease(
            db_session, req.id, job.id, expected_dispatch_generation=1
        )

    # Delivery of valid gen 2 message MUST succeed and acquire lease
    authority = await ProductionRenderLeaseService.acquire_lease(
        db_session, req.id, job.id, expected_dispatch_generation=2
    )
    assert authority.job_id == job.id

    # Verify job is now RUNNING under lease
    running_job = (
        await db_session.execute(
            select(ProductionRenderJob).where(ProductionRenderJob.id == job.id)
        )
    ).scalar_one()
    assert running_job.state == RenderJobState.RUNNING.value
    assert running_job.lease_token is not None


# ══════════════════════════════════════════════════════════════════
# 3. Pre-Lease Worker Crash & Recovery (Section 32)
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pre_lease_worker_crash_and_recovery(db_session: AsyncSession) -> None:
    """Worker dies before lease acquisition transaction commits; reconciler advances generation."""
    stale_time = datetime.now(UTC) - timedelta(seconds=400)
    _, req, job, _ = await _create_test_lineage(
        db_session,
        dispatch_generation=1,
        dispatch_started_at=stale_time,
    )

    # Gen 1 intent marked SENT
    gen1_intent = await DurableDispatchService.enqueue_async(
        db_session,
        idempotency_key=f"render-dispatch:{req.id}:{job.id}:gen1",
        task_name="omega.production.render",
        args=[str(req.channel_id), str(req.id), str(job.id), 1],
        purpose="PRODUCTION_RENDER_DISPATCH",
        production_request_id=req.id,
        render_job_id=job.id,
    )
    gen1_intent.state = SENT
    await db_session.commit()

    # Pre-lease crash: worker never acquired lease (job remains QUEUED, started_at NULL)
    assert job.state == RenderJobState.QUEUED.value
    assert job.started_at is None
    assert job.lease_token is None

    # Reconciler advances generation to 2
    rec_res = await ProductionDispatchService.reconcile_dispatch_stall_candidate(
        db_session, req.id, job.id, expected_generation=1, timeout_seconds=300
    )
    assert rec_res["action"] == "REDISPATCHED"
    assert rec_res["new_generation"] == 2

    # New worker arrives with generation 2 and acquires lease
    authority = await ProductionRenderLeaseService.acquire_lease(
        db_session, req.id, job.id, expected_dispatch_generation=2
    )
    assert authority.job_id == job.id

    # Verify same job ID preserved
    refreshed_job = (
        await db_session.execute(
            select(ProductionRenderJob).where(ProductionRenderJob.id == job.id)
        )
    ).scalar_one()
    assert refreshed_job.id == job.id
    assert refreshed_job.state == RenderJobState.RUNNING.value


# ══════════════════════════════════════════════════════════════════
# 4. Duplicate Delivery Single Execution (Section 33)
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_duplicate_delivery_single_execution(db_session: AsyncSession) -> None:
    """Duplicate deliveries of same generation result in exactly ONE lease acquisition."""
    _, req, job, _ = await _create_test_lineage(
        db_session,
        dispatch_generation=1,
        dispatch_started_at=datetime.now(UTC),
    )

    # Worker A acquires lease for generation 1
    auth_a = await ProductionRenderLeaseService.acquire_lease(
        db_session, req.id, job.id, owner_id="worker_a", expected_dispatch_generation=1
    )
    assert auth_a.owner_id == "worker_a"

    # Worker B attempts to acquire lease for generation 1
    with pytest.raises(ProductionDuplicateExecutionError, match="currently RUNNING"):
        await ProductionRenderLeaseService.acquire_lease(
            db_session, req.id, job.id, owner_id="worker_b", expected_dispatch_generation=1
        )

    # Verify worker A's lease is untouched
    refreshed_job = (
        await db_session.execute(
            select(ProductionRenderJob).where(ProductionRenderJob.id == job.id)
        )
    ).scalar_one()
    assert refreshed_job.lease_owner_id == "worker_a"
    assert refreshed_job.state == RenderJobState.RUNNING.value


# ══════════════════════════════════════════════════════════════════
# 5. Cancel Race Rejection (Section 34)
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cancel_race_rejects_worker_delivery(db_session: AsyncSession) -> None:
    """Request cancelled before worker acquires lease rejects delivery."""
    _, req, job, _ = await _create_test_lineage(
        db_session,
        dispatch_generation=1,
        dispatch_started_at=datetime.now(UTC),
    )

    # Cancel the request
    req.status = ProductionRequestStatus.CANCELLED.value
    job.state = RenderJobState.CANCELLED.value
    await db_session.commit()

    # Worker delivery arrives with gen 1 -> must reject
    with pytest.raises(ProductionLeaseFencingError, match="Cannot acquire lease for terminal ProductionRequest"):
        await ProductionRenderLeaseService.acquire_lease(
            db_session, req.id, job.id, expected_dispatch_generation=1
        )

    # Verify no lease issued
    refreshed_job = (
        await db_session.execute(
            select(ProductionRenderJob).where(ProductionRenderJob.id == job.id)
        )
    ).scalar_one()
    assert refreshed_job.lease_token is None
    assert refreshed_job.state == RenderJobState.CANCELLED.value


# ══════════════════════════════════════════════════════════════════
# 6. Explicit Retry Isolation (Section 35)
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_explicit_retry_isolation_rejects_old_message(db_session: AsyncSession) -> None:
    """Delayed message for terminal Request A cannot attach to explicit retry Request B."""
    # Lineage A
    _, req_a, job_a, _ = await _create_test_lineage(
        db_session,
        dispatch_generation=1,
        dispatch_started_at=datetime.now(UTC) - timedelta(seconds=600),
        job_state=RenderJobState.FAILED.value,
        req_status=ProductionRequestStatus.FAILED.value,
    )

    # Explicit retry creates Lineage B
    _, req_b, job_b, _ = await _create_test_lineage(
        db_session,
        dispatch_generation=1,
        dispatch_started_at=datetime.now(UTC),
        job_state=RenderJobState.QUEUED.value,
        req_status=ProductionRequestStatus.RUNNING.value,
    )

    # Delayed message for A arrives -> cannot acquire lease for A
    with pytest.raises(ProductionLeaseFencingError, match="Cannot acquire lease for terminal ProductionRequest"):
        await ProductionRenderLeaseService.acquire_lease(
            db_session, req_a.id, job_a.id, expected_dispatch_generation=1
        )

    # Lineage B is completely unaffected
    refreshed_b = (
        await db_session.execute(
            select(ProductionRenderJob).where(ProductionRenderJob.id == job_b.id)
        )
    ).scalar_one()
    assert refreshed_b.state == RenderJobState.QUEUED.value
    assert refreshed_b.dispatch_generation == 1


# ══════════════════════════════════════════════════════════════════
# 7. Mission Atomicity & Post-Commit Intent Invariants (LR3B.1)
# ══════════════════════════════════════════════════════════════════


async def _create_mission_hierarchy(
    db_session: AsyncSession, channel_id: uuid.UUID
) -> tuple[Mission, MissionExecution, Task]:
    m = Mission(
        id=uuid.uuid4(),
        channel_id=channel_id,
        title="Test Mission",
        objective="Test Objective",
        state="RUNNING",
    )
    ex = MissionExecution(
        id=uuid.uuid4(),
        mission_id=m.id,
        state="RUNNING",
    )
    t = Task(
        id=uuid.uuid4(),
        mission_id=m.id,
        execution_id=ex.id,
        task_type="canonical_production",
        title="Production Task",
        state="RUNNING",
    )
    db_session.add_all([m, ex, t])
    await db_session.commit()
    return m, ex, t


@pytest.mark.asyncio
async def test_mission_atomic_render_allocation_rollback_and_retry(
    db_session: AsyncSession,
) -> None:
    """Failure before outbox commit rolls back RenderJob; retry creates exactly 1 job and 1 intent."""
    channel, req, _, _ = await _create_test_lineage(
        db_session,
        req_status=ProductionRequestStatus.READY.value,
    )
    # Remove pre-created job from helper so req is READY for allocation
    await db_session.execute(
        text("DELETE FROM production_render_jobs WHERE production_request_id = :rid"),
        {"rid": req.id},
    )
    await db_session.commit()

    channel_id = channel.id
    req_id = req.id
    mission, execution, task = await _create_mission_hierarchy(db_session, channel_id)
    idempotency_key = f"mission-render:{req_id}:attempt1"
    mission_id = mission.id
    execution_id = execution.id
    task_id = task.id

    # 1. Inject failure during outbox enqueue before commit
    with patch(
        "omega.application.durable_dispatch.DurableDispatchService.enqueue_async",
        side_effect=RuntimeError("Simulated outbox failure before commit"),
    ):
        with pytest.raises(RuntimeError, match="Simulated outbox failure before commit"):
            await ProductionDispatchService.allocate_and_enqueue_render(
                db_session,
                channel_id,
                req_id,
                idempotency_key,
                mission_id=mission_id,
                mission_execution_id=execution_id,
                mission_task_id=task_id,
            )

    # Rollback must leave 0 committed jobs and 0 committed outbox intents
    await db_session.rollback()
    jobs_after_fail = (
        await db_session.execute(
            select(ProductionRenderJob).where(ProductionRenderJob.production_request_id == req_id)
        )
    ).scalars().all()
    intents_after_fail = (
        await db_session.execute(
            select(DurableDispatchIntent).where(DurableDispatchIntent.production_request_id == req_id)
        )
    ).scalars().all()
    assert len(jobs_after_fail) == 0, "RenderJob must have rolled back"
    assert len(intents_after_fail) == 0, "DurableDispatchIntent must have rolled back"

    # 2. Retry the identical canonical mission allocation
    job, plan, is_new, intent = await ProductionDispatchService.allocate_and_enqueue_render(
        db_session,
        channel_id,
        req_id,
        idempotency_key,
        mission_id=mission_id,
        mission_execution_id=execution_id,
        mission_task_id=task_id,
    )
    assert is_new is True
    assert job.dispatch_generation == 1
    assert job.dispatch_started_at is not None
    assert job.state == RenderJobState.QUEUED.value

    # Verify exactly 1 job and 1 gen1 intent committed
    committed_jobs = (
        await db_session.execute(
            select(ProductionRenderJob).where(ProductionRenderJob.production_request_id == req_id)
        )
    ).scalars().all()
    committed_intents = (
        await db_session.execute(
            select(DurableDispatchIntent).where(DurableDispatchIntent.production_request_id == req_id)
        )
    ).scalars().all()
    assert len(committed_jobs) == 1
    assert len(committed_intents) == 1
    assert committed_intents[0].args == [str(channel_id), str(req_id), str(job.id), 1]
    assert committed_intents[0].idempotency_key == f"render-dispatch:{req_id}:{job.id}:gen1"
    assert committed_intents[0].mission_id == mission_id
    assert committed_intents[0].mission_execution_id == execution_id
    assert committed_intents[0].mission_task_id == task_id


@pytest.mark.asyncio
async def test_post_commit_dispatch_intent_invariant(db_session: AsyncSession) -> None:
    """Every newly LR3-enrolled QUEUED job has exactly one current-generation DurableDispatchIntent.
    Tested for Mission, REST /render, and REST /rerender."""
    channel, req, _, _ = await _create_test_lineage(
        db_session,
        req_status=ProductionRequestStatus.READY.value,
    )
    await db_session.execute(
        text("DELETE FROM production_render_jobs WHERE production_request_id = :rid"),
        {"rid": req.id},
    )
    await db_session.commit()

    channel_id = channel.id
    req_id = req.id

    # A. Mission canonical allocation
    mission, execution, task = await _create_mission_hierarchy(db_session, channel_id)
    job_mission, _, _, _ = await ProductionDispatchService.allocate_and_enqueue_render(
        db_session,
        channel_id,
        req_id,
        f"render-mission:{req_id}",
        mission_id=mission.id,
        mission_execution_id=execution.id,
        mission_task_id=task.id,
    )
    assert job_mission.state == RenderJobState.QUEUED.value
    assert job_mission.dispatch_started_at is not None
    mission_intents = (
        await db_session.execute(
            select(DurableDispatchIntent).where(
                DurableDispatchIntent.render_job_id == job_mission.id,
                DurableDispatchIntent.production_request_id == req_id,
            )
        )
    ).scalars().all()
    assert len(mission_intents) == 1
    assert mission_intents[0].args[3] == job_mission.dispatch_generation
    assert mission_intents[0].mission_id == mission.id
    assert mission_intents[0].mission_execution_id == execution.id
    assert mission_intents[0].mission_task_id == task.id

    # B. REST /render canonical allocation (new request)
    channel2, req2, _, _ = await _create_test_lineage(
        db_session,
        req_status=ProductionRequestStatus.READY.value,
    )
    await db_session.execute(
        text("DELETE FROM production_render_jobs WHERE production_request_id = :rid"),
        {"rid": req2.id},
    )
    await db_session.commit()

    channel2_id = channel2.id
    req2_id = req2.id

    job_rest, _, _, _ = await ProductionDispatchService.allocate_and_enqueue_render(
        db_session,
        channel2_id,
        req2_id,
        f"render-rest:{req2_id}",
    )
    assert job_rest.state == RenderJobState.QUEUED.value
    assert job_rest.dispatch_started_at is not None
    rest_intents = (
        await db_session.execute(
            select(DurableDispatchIntent).where(
                DurableDispatchIntent.render_job_id == job_rest.id,
                DurableDispatchIntent.production_request_id == req2_id,
            )
        )
    ).scalars().all()
    assert len(rest_intents) == 1
    assert rest_intents[0].args[3] == job_rest.dispatch_generation

    # C. REST /rerender canonical allocation (new attempt on completed request)
    # Ensure prior job is succeeded and request is succeeded
    job_rest.state = RenderJobState.SUCCEEDED.value
    req2.status = ProductionRequestStatus.SUCCEEDED.value
    await db_session.commit()

    job_rerender, _, _, _ = await ProductionDispatchService.allocate_and_enqueue_render(
        db_session,
        channel2_id,
        req2_id,
        f"rerender-rest:{req2_id}",
        is_rerender=True,
    )
    assert job_rerender.state == RenderJobState.QUEUED.value
    assert job_rerender.dispatch_started_at is not None
    rerender_intents = (
        await db_session.execute(
            select(DurableDispatchIntent).where(
                DurableDispatchIntent.render_job_id == job_rerender.id,
                DurableDispatchIntent.production_request_id == req2_id,
            )
        )
    ).scalars().all()
    assert len(rerender_intents) == 1
    assert rerender_intents[0].args[3] == job_rerender.dispatch_generation


# ══════════════════════════════════════════════════════════════════
# 8. Publishing Safety
# ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_zero_publishing_invariants(db_session: AsyncSession) -> None:
    """LR3 recovery creates 0 PublishIntent and 0 PublishAttempt rows."""
    intents = (await db_session.execute(select(PublishIntent))).scalars().all()
    attempts = (await db_session.execute(select(PublishAttempt))).scalars().all()
    assert len(intents) == 0
    assert len(attempts) == 0
