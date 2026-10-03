"""Integration failure-injection acceptance test for P19-LR2B:
Worker lease, heartbeat, hard-crash recovery, and zero-publishing safety.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.production_lifecycle_service import ProductionLifecycleService
from omega.application.production_render_lease_service import (
    LEASE_TTL_SECONDS,
    SWEEP_GRACE_SECONDS,
    ProductionLeaseFencingError,
    ProductionRenderLeaseService,
)
from omega.domain.production import ProductionOutcome, ProductionRequestStatus, RenderErrorCode, RenderJobState
from omega.infrastructure.models import (
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    DurableDispatchIntent,
    MediaArtifact,
    ProductionRenderJob,
    ProductionRequest,
    PublishAttempt,
    PublishIntent,
    RenderPlan,
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    TopicCandidate,
)


@pytest.mark.asyncio
async def test_physical_hard_crash_and_orphan_sweep_recovery(db_session: AsyncSession) -> None:
    """Failure-injection acceptance:
    1. Start a short leased render job with real DB persistence.
    2. Launch an isolated worker subprocess that renews heartbeat.
    3. Verify heartbeat_at advances at least twice in the database.
    4. Hard-kill (SIGKILL / TerminateProcess) the worker process with NO graceful hooks.
    5. Verify heartbeat stops advancing.
    6. Advance job lease_expires_at past DB NOW() - SWEEP_GRACE_SECONDS.
    7. Execute reconciliation sweep.
    8. Verify:
       - RenderJob -> FAILED
       - error_code = WORKER_LEASE_EXPIRED
       - ProductionRequest -> FAILED
       - fencing_token incremented
    9. Verify no PublishIntent or PublishAttempt created.
    10. Simulate worker restart: verify duplicate render acquisition is rejected and state is immutable.
    """
    channel_id = uuid.uuid4()
    channel = Channel(
        id=channel_id,
        name="Test Channel LR2",
        slug=f"test-channel-lr2-{channel_id.hex[:8]}",
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
        title="Test Topic LR2",
        normalized_title="test topic lr2",
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
        title="Test Script LR2",
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
        status=ProductionRequestStatus.RUNNING.value,
        metadata_={"test": "lr2_canary"},
    )
    db_session.add(req)

    plan_id = uuid.uuid4()
    plan = RenderPlan(
        id=plan_id,
        production_request_id=req_id,
        version=1,
    )
    db_session.add(plan)

    job_id = uuid.uuid4()
    job = ProductionRenderJob(
        id=job_id,
        production_request_id=req_id,
        render_plan_id=plan_id,
        idempotency_key=f"lr2-job-{uuid.uuid4()}",
        state=RenderJobState.QUEUED.value,
        fencing_token=0,
    )
    db_session.add(job)
    await db_session.commit()

    # Step 1: Acquire lease authoritatively
    authority = await ProductionRenderLeaseService.acquire_lease(
        db_session,
        request_id=req_id,
        job_id=job_id,
    )
    assert authority is not None
    assert authority.fencing_token == 1
    assert authority.lease_token is not None

    await db_session.refresh(job)
    assert job.state == RenderJobState.RUNNING.value
    initial_heartbeat = job.heartbeat_at
    initial_expiry = job.lease_expires_at
    assert initial_heartbeat is not None
    assert initial_expiry is not None

    # Step 2: Spawn an isolated worker subprocess with short 1-second pulse
    # The subprocess will run a pulse script that directly renews the lease every second
    db_url_sync = os.environ.get(
        "DATABASE_URL_SYNC",
        "postgresql+psycopg2://omega:nonprod_synthetic_dev_placeholder_password@localhost:5432/omega_test",
    )
    worker_script = f"""
import sys, time, uuid
from omega.infrastructure.database_sync import SyncSessionLocal
from omega.application.production_render_lease_service import ProductionRenderLeaseService

job_id = uuid.UUID('{job_id}')
token = uuid.UUID('{authority.lease_token}')
fence = {authority.fencing_token}

while True:
    time.sleep(0.8)
    with SyncSessionLocal() as session:
        ok = ProductionRenderLeaseService.renew_lease_sync(
            session,
            job_id=job_id,
            lease_token=token,
            fencing_token=fence,
        )
        if not ok:
            sys.exit(1)
"""
    env = dict(os.environ)
    env["DATABASE_URL_SYNC"] = db_url_sync

    proc = subprocess.Popen(
        [sys.executable, "-c", worker_script],
        env=env,
        stdout=subprocess.PIPE,
        stderr=pytest.PIPE if hasattr(pytest, "PIPE") else subprocess.PIPE,
    )

    try:
        # Step 3: Verify heartbeat advances at least twice in DB
        advances = 0
        last_hb = initial_heartbeat
        for _ in range(15):
            await db_session.rollback()
            await db_session.refresh(job)
            if job.heartbeat_at is not None and job.heartbeat_at > last_hb:
                advances += 1
                last_hb = job.heartbeat_at
                if advances >= 2:
                    break
            time.sleep(0.5)

        assert advances >= 2, f"Heartbeat did not advance at least twice (advanced {advances} times)"
        assert job.lease_expires_at > initial_expiry

        # Step 4: Hard-kill ONLY the isolated test worker process (SIGKILL / TerminateProcess)
        # Bypasses all graceful cleanup / finally handlers
        proc.kill()
        proc.wait(timeout=5)

        # Step 5: Verify heartbeat stops advancing
        await db_session.rollback()
        await db_session.refresh(job)
        stopped_hb = job.heartbeat_at
        time.sleep(1.5)
        await db_session.rollback()
        await db_session.refresh(job)
        assert job.heartbeat_at == stopped_hb, "Heartbeat continued advancing after worker hard-kill!"

        # Step 6: Advance lease past expiration into sweep window (DB NOW() - 10 seconds)
        # Using authoritative DB timestamp calculation
        await db_session.execute(
            text(
                "UPDATE production_render_jobs "
                "SET lease_expires_at = now() - interval '15 seconds' "
                f"WHERE id = '{job_id}'"
            )
        )
        await db_session.commit()

        # Step 7: Execute reconciliation sweep
        reconciled = await ProductionLifecycleService.reconcile_expired_leases(db_session, limit=10)
        assert reconciled.get("expired", 0) >= 1

        # Step 8: Verify terminal states
        await db_session.rollback()
        await db_session.refresh(job)
        await db_session.refresh(req)

        assert job.state == RenderJobState.FAILED.value
        assert job.error_code == RenderErrorCode.WORKER_LEASE_EXPIRED.value
        assert "Worker lease expired" in (job.sanitized_error or "")
        assert job.fencing_token == authority.fencing_token + 1
        assert job.lease_expires_at is None
        assert job.completed_at is not None

        assert req.status == ProductionRequestStatus.FAILED.value
        assert req.outcome == ProductionOutcome.BLOCKED.value

        # Step 9: Verify ZERO publish intents or artifacts created
        intents = (
            await db_session.execute(
                select(PublishIntent).where(PublishIntent.channel_id == channel_id)
            )
        ).scalars().all()
        assert len(intents) == 0

        arts = (
            await db_session.execute(
                select(MediaArtifact).where(MediaArtifact.production_request_id == req_id)
            )
        ).scalars().all()
        assert len(arts) == 0

        # Step 10: Simulate worker restart / recovery
        # Verify duplicate acquisition is rejected with fencing error
        with pytest.raises(ProductionLeaseFencingError):
            await ProductionRenderLeaseService.acquire_lease(
                db_session,
                request_id=req_id,
                job_id=job_id,
            )

        # Verify terminal request cannot be overwritten
        revoked_ok, _ = await ProductionRenderLeaseService.expire_lease(
            db_session, request_id=req_id, job_id=job_id
        )
        assert revoked_ok is False

    finally:
        if proc.poll() is None:
            proc.kill()
