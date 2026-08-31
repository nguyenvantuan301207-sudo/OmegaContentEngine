"""Background job coordination and lease fencing service for OMEGA-013 Learning Engine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.learning import LearningJobStatus, compute_job_dedupe_key
from omega.infrastructure.models import LearningJob


class LearningJobService:
    """Provides atomic lease claiming, renewal, and fencing for asynchronous learning workers."""

    @classmethod
    async def get_or_create_job(
        cls,
        session: AsyncSession,
        channel_id: UUID,
        job_type: str,
        execution_identity: str,
    ) -> tuple[LearningJob, bool]:
        """Idempotently retrieve or create a learning background job."""
        key = compute_job_dedupe_key(channel_id, job_type, execution_identity)

        stmt = select(LearningJob).where(LearningJob.job_dedupe_key == key)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing, False

        job = LearningJob(
            job_type=job_type,
            channel_id=channel_id,
            status=LearningJobStatus.PENDING.value,
            job_dedupe_key=key,
        )
        session.add(job)
        await session.flush()
        return job, True

    @classmethod
    async def claim_job(
        cls,
        session: AsyncSession,
        job_id: UUID,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> LearningJob | None:
        """Atomically claim a pending or expired job, incrementing generation counter."""
        stmt = select(LearningJob).where(LearningJob.id == job_id).with_for_update()
        job = (await session.execute(stmt)).scalar_one_or_none()
        if job is None:
            return None

        now_utc = datetime.now(UTC)

        is_pending = job.status == LearningJobStatus.PENDING.value
        is_expired = (
            job.status == LearningJobStatus.CLAIMED.value
            and job.lease_expires_at is not None
            and job.lease_expires_at < now_utc
        )

        if not (is_pending or is_expired):
            return None

        token = uuid.uuid4()
        job.claim_token = token
        job.claim_generation += 1
        job.claimed_by_worker_id = worker_id
        job.lease_expires_at = now_utc + timedelta(seconds=lease_seconds)
        job.status = LearningJobStatus.CLAIMED.value
        job.started_at = now_utc
        await session.flush()

        return job

    @classmethod
    async def verify_and_complete_job(
        cls,
        session: AsyncSession,
        job_id: UUID,
        claim_token: UUID,
        claim_generation: int,
    ) -> bool:
        """Verify fencing lease and mark job COMPLETED. Stale workers are fenced out."""
        stmt = (
            select(LearningJob)
            .where(
                LearningJob.id == job_id,
                LearningJob.claim_token == claim_token,
                LearningJob.claim_generation == claim_generation,
                LearningJob.lease_expires_at > func.now(),
            )
            .with_for_update()
        )
        job = (await session.execute(stmt)).scalar_one_or_none()
        if job is None:
            return False

        job.status = LearningJobStatus.COMPLETED.value
        job.completed_at = datetime.now(UTC)
        await session.flush()
        return True

    @classmethod
    async def verify_and_fail_job(
        cls,
        session: AsyncSession,
        job_id: UUID,
        claim_token: UUID,
        claim_generation: int,
        error_category: str,
        error_message: str,
    ) -> bool:
        """Mark job FAILED under active lease predicate."""
        stmt = (
            select(LearningJob)
            .where(
                LearningJob.id == job_id,
                LearningJob.claim_token == claim_token,
                LearningJob.claim_generation == claim_generation,
                LearningJob.lease_expires_at > func.now(),
            )
            .with_for_update()
        )
        job = (await session.execute(stmt)).scalar_one_or_none()
        if job is None:
            return False

        job.status = LearningJobStatus.FAILED.value
        job.error_category = error_category
        job.error_message = error_message
        job.completed_at = datetime.now(UTC)
        await session.flush()
        return True
