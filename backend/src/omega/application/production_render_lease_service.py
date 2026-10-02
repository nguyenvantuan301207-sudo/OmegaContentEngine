"""ProductionRenderLeaseService (P19-LR2).

Authoritative worker lease, heartbeat management, and fencing service for
ProductionRenderJob executions.
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from omega.domain.production import (
    ProductionRequestStatus,
    RenderErrorCode,
    RenderJobState,
)
from omega.infrastructure.models import ProductionRenderJob, ProductionRequest

logger = logging.getLogger(__name__)

# Canonical LR2 Lease Timers & Margins
HEARTBEAT_INTERVAL_SECONDS: int = 20
LEASE_TTL_SECONDS: int = 90
SWEEP_GRACE_SECONDS: int = 10
WORKER_SELF_FENCE_MARGIN_SECONDS: int = 20
WORKER_HEARTBEAT_FAILURE_ABORT_SECONDS: int = 70

# Startup-scoped worker identity
_WORKER_STARTUP_UUID: str = uuid.uuid4().hex[:8]
_WORKER_INSTANCE_ID: str = f"{socket.gethostname()}:{os.getpid()}:{_WORKER_STARTUP_UUID}"


def get_worker_instance_id() -> str:
    """Return stable startup-scoped worker instance identity."""
    return _WORKER_INSTANCE_ID


@dataclass(frozen=True)
class RenderLeaseAuthority:
    """Immutable credentials representing active lease ownership."""

    job_id: uuid.UUID
    lease_token: uuid.UUID
    fencing_token: int
    owner_id: str
    acquired_at: datetime
    lease_expires_at: datetime


class ProductionLeaseError(RuntimeError):
    """Base exception for lease-related failures."""


class ProductionLeaseLostError(ProductionLeaseError):
    """Raised when a worker detects it has lost its authoritative lease."""


class ProductionLeaseFencingError(ProductionLeaseError):
    """Raised when a stale worker fails fencing validation during terminal finalization."""


class ProductionWorkerSelfFencedError(ProductionLeaseError):
    """Raised when worker self-fences due to heartbeat DB communication failure."""


class ProductionDuplicateExecutionError(ProductionLeaseError):
    """Raised when an attempt is made to acquire a lease on an active or terminal job."""


class ProductionStaleDispatchGenerationError(ProductionLeaseError):
    """Raised when worker receives a stale dispatch generation."""


class ProductionInvalidFutureDispatchGenerationError(ProductionLeaseError):
    """Raised when worker receives an invalid future dispatch generation."""


class ProductionRenderLeaseService:
    """Authoritative service for managing render leases, heartbeats, and fencing."""

    @classmethod
    async def acquire_lease(
        cls,
        session: AsyncSession,
        request_id: uuid.UUID,
        job_id: uuid.UUID,
        owner_id: str | None = None,
        expected_dispatch_generation: int | None = None,
    ) -> RenderLeaseAuthority:
        """Atomically acquire exclusive execution lease on a ProductionRenderJob.

        Enforces canonical lock order: ProductionRequest -> ProductionRenderJob.
        """
        # 1. Lock ProductionRequest
        req_stmt = (
            select(ProductionRequest)
            .where(ProductionRequest.id == request_id)
            .with_for_update()
        )
        req = (await session.execute(req_stmt)).scalar_one_or_none()
        if req is None:
            raise ValueError(f"ProductionRequest {request_id} not found")

        if req.status in (
            ProductionRequestStatus.FAILED.value,
            ProductionRequestStatus.CANCELLED.value,
        ):
            raise ProductionLeaseFencingError(
                f"Cannot acquire lease for terminal ProductionRequest {request_id} ({req.status})"
            )

        # 2. Lock ProductionRenderJob
        job_stmt = (
            select(ProductionRenderJob)
            .where(
                ProductionRenderJob.id == job_id,
                ProductionRenderJob.production_request_id == request_id,
            )
            .with_for_update()
        )
        job = (await session.execute(job_stmt)).scalar_one_or_none()
        if job is None:
            raise ValueError(f"ProductionRenderJob {job_id} not found")

        if job.state == RenderJobState.SUCCEEDED.value:
            raise ProductionDuplicateExecutionError(
                f"Render job {job_id} is already SUCCEEDED; duplicate acquisition rejected"
            )
        if job.state in (RenderJobState.FAILED.value, RenderJobState.CANCELLED.value):
            raise ProductionDuplicateExecutionError(
                f"Render job {job_id} is terminal {job.state}; duplicate acquisition rejected"
            )

        if job.state == RenderJobState.RUNNING.value:
            raise ProductionDuplicateExecutionError(
                f"Render job {job_id} is currently RUNNING; reacquisition rejected. "
                "Expired jobs require reconciliation before explicit retry creates a new lineage."
            )
        if job.state not in (RenderJobState.QUEUED.value, RenderJobState.RETRY.value, RenderJobState.PENDING.value):
            raise ProductionDuplicateExecutionError(
                f"Render job {job_id} is in non-acquirable state {job.state}; acquisition rejected"
            )

        # Validate dispatch generation (P19-LR3)
        is_enrolled = getattr(job, "dispatch_started_at", None) is not None
        if is_enrolled or expected_dispatch_generation is not None:
            current_gen = getattr(job, "dispatch_generation", 1) or 1
            if expected_dispatch_generation is None:
                raise ProductionStaleDispatchGenerationError(
                    f"Render job {job_id} requires dispatch_generation but message contained None"
                )
            if expected_dispatch_generation < current_gen:
                logger.warning(
                    "production_dispatch_stale_generation_rejected",
                    extra={
                        "event": "production_dispatch_stale_generation_rejected",
                        "job_id": str(job_id),
                        "message_generation": expected_dispatch_generation,
                        "persisted_generation": current_gen,
                    },
                )
                raise ProductionStaleDispatchGenerationError(
                    f"Stale dispatch generation {expected_dispatch_generation} < current {current_gen} for job {job_id}"
                )
            if expected_dispatch_generation > current_gen:
                logger.warning(
                    "production_dispatch_future_generation_rejected",
                    extra={
                        "event": "production_dispatch_future_generation_rejected",
                        "job_id": str(job_id),
                        "message_generation": expected_dispatch_generation,
                        "persisted_generation": current_gen,
                    },
                )
                raise ProductionInvalidFutureDispatchGenerationError(
                    f"Future dispatch generation {expected_dispatch_generation} > current {current_gen} for job {job_id}"
                )

        # Increment fencing token and issue fresh lease
        owner = owner_id or get_worker_instance_id()
        token = uuid.uuid4()
        new_fence = (job.fencing_token or 0) + 1

        now = datetime.now(UTC)
        job.state = RenderJobState.RUNNING.value
        job.lease_owner_id = owner
        job.lease_token = token
        job.fencing_token = new_fence
        job.heartbeat_at = func.now()
        job.lease_expires_at = func.now() + text(f"interval '{LEASE_TTL_SECONDS} seconds'")
        if not job.started_at:
            job.started_at = func.now()

        await session.commit()

        logger.info(
            "render_lease_acquired",
            extra={
                "event": "render_lease_acquired",
                "job_id": str(job_id),
                "request_id": str(request_id),
                "fencing_token": new_fence,
                "owner_id": owner,
            },
        )

        return RenderLeaseAuthority(
            job_id=job_id,
            lease_token=token,
            fencing_token=new_fence,
            owner_id=owner,
            acquired_at=now,
            lease_expires_at=now,
        )

    @classmethod
    def renew_lease_sync(
        cls,
        session: Session,
        job_id: uuid.UUID,
        lease_token: uuid.UUID,
        fencing_token: int,
    ) -> bool:
        """Synchronously renew lease using an atomic compare-and-set query.

        Dedicated to ProductionHeartbeatRunner in worker background thread.
        """
        stmt = (
            update(ProductionRenderJob)
            .where(
                ProductionRenderJob.id == job_id,
                ProductionRenderJob.lease_token == lease_token,
                ProductionRenderJob.fencing_token == fencing_token,
                ProductionRenderJob.state == RenderJobState.RUNNING.value,
            )
            .values(
                heartbeat_at=func.now(),
                lease_expires_at=func.now() + text(f"interval '{LEASE_TTL_SECONDS} seconds'"),
            )
        )
        res = session.execute(stmt)
        session.commit()
        success = bool((res.rowcount or 0) > 0)
        if success:
            logger.debug(
                "render_lease_renewed",
                extra={"event": "render_lease_renewed", "job_id": str(job_id)},
            )
        else:
            logger.warning(
                "render_lease_lost",
                extra={
                    "event": "render_lease_lost",
                    "job_id": str(job_id),
                    "fencing_token": fencing_token,
                },
            )
        return success

    @classmethod
    async def renew_lease_async(
        cls,
        session: AsyncSession,
        job_id: uuid.UUID,
        lease_token: uuid.UUID,
        fencing_token: int,
    ) -> bool:
        """Asynchronously renew lease using an atomic compare-and-set query."""
        stmt = (
            update(ProductionRenderJob)
            .where(
                ProductionRenderJob.id == job_id,
                ProductionRenderJob.lease_token == lease_token,
                ProductionRenderJob.fencing_token == fencing_token,
                ProductionRenderJob.state == RenderJobState.RUNNING.value,
            )
            .values(
                heartbeat_at=func.now(),
                lease_expires_at=func.now() + text(f"interval '{LEASE_TTL_SECONDS} seconds'"),
            )
        )
        res = await session.execute(stmt)
        if hasattr(session, "commit"):
            await session.commit()
        success = bool((res.rowcount or 0) > 0)
        if success:
            logger.debug(
                "render_lease_renewed",
                extra={"event": "render_lease_renewed", "job_id": str(job_id)},
            )
        else:
            logger.warning(
                "render_lease_lost",
                extra={
                    "event": "render_lease_lost",
                    "job_id": str(job_id),
                    "fencing_token": fencing_token,
                },
            )
        return success

    @classmethod
    async def validate_lease_fenced_async(
        cls,
        session: AsyncSession,
        request_id: uuid.UUID,
        job_id: uuid.UUID,
        lease_token: uuid.UUID,
        fencing_token: int,
    ) -> tuple[bool, str]:
        """Validate that worker still owns active, valid, unexpired lease using DB clock.

        Enforces canonical lock order: ProductionRequest -> ProductionRenderJob.
        """
        # 1. Lock ProductionRequest
        req_stmt = (
            select(ProductionRequest)
            .where(ProductionRequest.id == request_id)
            .with_for_update()
        )
        req = (await session.execute(req_stmt)).scalar_one_or_none()
        if req is None:
            return False, "REQUEST_NOT_FOUND"
        if req.status in (
            ProductionRequestStatus.FAILED.value,
            ProductionRequestStatus.CANCELLED.value,
        ):
            return False, f"REQUEST_TERMINAL_{req.status}"

        # 2. Lock and verify ProductionRenderJob with SQL predicate using DB NOW()
        job_stmt = (
            select(ProductionRenderJob)
            .where(
                ProductionRenderJob.id == job_id,
                ProductionRenderJob.production_request_id == request_id,
                ProductionRenderJob.lease_token == lease_token,
                ProductionRenderJob.fencing_token == fencing_token,
                ProductionRenderJob.state == RenderJobState.RUNNING.value,
                ProductionRenderJob.lease_expires_at > func.now(),
            )
            .with_for_update()
        )
        job = (await session.execute(job_stmt)).scalar_one_or_none()
        if job is None:
            logger.warning(
                "stale_worker_fence_rejected",
                extra={
                    "event": "stale_worker_fence_rejected",
                    "job_id": str(job_id),
                    "request_id": str(request_id),
                    "fencing_token": fencing_token,
                },
            )
            try:
                from omega.application.observability.telemetry import (
                    increment_event_counter_async,
                )

                await increment_event_counter_async(
                    "omega_render_fence_rejections_total", 1
                )
            except Exception:
                pass
            return False, "LEASE_FENCE_LOST_OR_EXPIRED"

        return True, "VALID"

    @classmethod
    async def expire_lease(
        cls,
        session: AsyncSession,
        request_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> tuple[bool, str]:
        """Expire a stale RUNNING job if its lease exceeded TTL + SWEEP_GRACE_SECONDS.

        Atomically revokes fence and propagates failure to parent ProductionRequest.
        Enforces canonical lock order: ProductionRequest -> ProductionRenderJob.
        """
        # 1. Lock ProductionRequest
        req_stmt = (
            select(ProductionRequest)
            .where(ProductionRequest.id == request_id)
            .with_for_update()
        )
        req = (await session.execute(req_stmt)).scalar_one_or_none()
        if req is None:
            return False, "REQUEST_NOT_FOUND"

        # 2. Lock ProductionRenderJob
        job_stmt = (
            select(ProductionRenderJob)
            .where(
                ProductionRenderJob.id == job_id,
                ProductionRenderJob.production_request_id == request_id,
            )
            .with_for_update()
        )
        job = (await session.execute(job_stmt)).scalar_one_or_none()
        if job is None or job.state != RenderJobState.RUNNING.value:
            return False, "NOT_RUNNING"

        if job.lease_token is None or job.lease_expires_at is None:
            # Unmanaged legacy row without lease columns: safe skip
            return False, "UNMANAGED_LEGACY_ROW"

        # Check DB time: lease_expires_at < func.now() - SWEEP_GRACE_SECONDS
        check_stmt = select(ProductionRenderJob.id).where(
            ProductionRenderJob.id == job_id,
            ProductionRenderJob.lease_expires_at < func.now() - text(f"interval '{SWEEP_GRACE_SECONDS} seconds'"),
        )
        is_expired = (await session.execute(check_stmt)).scalar_one_or_none() is not None
        if not is_expired:
            return False, "LEASE_NOT_EXPIRED"

        # Atomically revoke fence and fail job
        now = datetime.now(UTC)
        job.fencing_token = (job.fencing_token or 0) + 1
        job.state = RenderJobState.FAILED.value
        job.error_code = RenderErrorCode.WORKER_LEASE_EXPIRED.value
        job.sanitized_error = "Worker lease expired; heartbeat timed out (hard crash or lost worker)"
        job.completed_at = now
        job.lease_expires_at = None

        logger.warning(
            "render_orphan_expired",
            extra={
                "event": "render_orphan_expired",
                "job_id": str(job_id),
                "request_id": str(request_id),
                "fencing_token": job.fencing_token,
            },
        )

        from omega.application.production_lifecycle_service import ProductionLifecycleService

        await ProductionLifecycleService.fail_production_request(
            session,
            request_id,
            reason="Worker lease expired; heartbeat timed out",
            error_code=RenderErrorCode.WORKER_LEASE_EXPIRED.value,
            failure_stage="WORKER_LEASE_EXPIRED",
            details={"job_id": str(job_id), "fencing_token": job.fencing_token},
            lock=False,  # Parent already locked above
        )

        return True, "EXPIRED"

    @classmethod
    async def cancel_job_lease(
        cls,
        session: AsyncSession,
        request_id: uuid.UUID,
        job_id: uuid.UUID,
        reason: str = "User cancelled",
    ) -> bool:
        """Revoke lease and increment fencing token upon cancellation."""
        job_stmt = (
            select(ProductionRenderJob)
            .where(
                ProductionRenderJob.id == job_id,
                ProductionRenderJob.production_request_id == request_id,
            )
            .with_for_update()
        )
        job = (await session.execute(job_stmt)).scalar_one_or_none()
        if job is None:
            return False

        now = datetime.now(UTC)
        job.fencing_token = (job.fencing_token or 0) + 1
        job.state = RenderJobState.CANCELLED.value
        job.lease_expires_at = func.now()
        job.completed_at = now
        job.sanitized_error = f"Cancelled: {reason}"[:1000]
        return True
