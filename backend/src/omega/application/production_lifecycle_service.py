"""Production Lifecycle Service.

State-machine-first, idempotent, failure-safe lifecycle authority and orphan
reconciliation for ProductionRequest, ProductionRenderJob, and related entities.

Authoritative Rules:
1. Terminal states (SUCCEEDED, FAILED, CANCELLED) are strictly monotonic and immutable.
2. A terminal FAILED authoritative RenderJob cannot leave its owning ProductionRequest
   indefinitely RUNNING.
3. Production success strictly requires an authoritative SUCCEEDED RenderJob, a physical
   MediaArtifact with non-zero size and verified SHA-256 hash, and ProductionRuntimeTruth.
4. Valid non-terminal approval states (e.g. Retry-6: ProductionRequest SUCCEEDED,
   Task(production) SUCCEEDED, Task(qa) WAITING_APPROVAL, Task(publish) PENDING) are
   legitimate workflow states and MUST NOT be misclassified as orphaned or failed.
5. Lifecycle recovery and reconciliation NEVER trigger or create publishing intents/attempts.
6. All transitions and reconciliation actions are idempotent and safe for concurrent/repeated execution.
"""

from __future__ import annotations

import enum
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from omega.application.durable_dispatch import DurableDispatchService
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

logger = logging.getLogger(__name__)

SHA256_HEX_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")
SWEEP_GRACE_SECONDS = 10


class LifecycleTransitionError(Exception):
    """Raised on illegal lifecycle state transitions."""

    pass


class LifecycleInvariantError(Exception):
    """Raised when an entity fails required terminal invariants."""

    pass


class RetryabilityCategory(enum.StrEnum):
    """Deterministic classification of failure retryability."""

    TRANSIENT_INFRASTRUCTURE = "TRANSIENT_INFRASTRUCTURE"
    ASSET_PROVIDER_TRANSIENT = "ASSET_PROVIDER_TRANSIENT"
    FFMPEG_TIMEOUT = "FFMPEG_TIMEOUT"
    BROWSER_RUNTIME_FAILURE = "BROWSER_RUNTIME_FAILURE"
    INVALID_INPUT = "INVALID_INPUT"
    QA_BLOCKER = "QA_BLOCKER"
    PROVENANCE_FAILURE = "PROVENANCE_FAILURE"
    INTERNAL_INVARIANT_FAILURE = "INTERNAL_INVARIANT_FAILURE"


def classify_failure_retryability(
    error_code: str | RenderErrorCode | None,
    reason: str | None = None,
) -> RetryabilityCategory:
    """Classify failure error code and message into a deterministic retryability category."""
    code_str = ""
    if isinstance(error_code, RenderErrorCode):
        code_str = error_code.value
    elif error_code:
        code_str = str(error_code)

    reason_lower = (reason or "").lower()

    if code_str == RenderErrorCode.TIMEOUT.value or "timed out" in reason_lower or "timeout" in reason_lower:
        if "ffmpeg" in reason_lower:
            return RetryabilityCategory.FFMPEG_TIMEOUT
        return RetryabilityCategory.TRANSIENT_INFRASTRUCTURE

    if (
        code_str == RenderErrorCode.WORKER_LEASE_EXPIRED.value
        or "lease expired" in reason_lower
        or "heartbeat" in reason_lower
    ):
        return RetryabilityCategory.TRANSIENT_INFRASTRUCTURE

    if code_str == RenderErrorCode.STORAGE_FAILED.value or "storage" in reason_lower or "disk" in reason_lower:
        return RetryabilityCategory.TRANSIENT_INFRASTRUCTURE

    if code_str == RenderErrorCode.INPUT_INVALID.value or "template" in reason_lower or "payload" in reason_lower:
        return RetryabilityCategory.INVALID_INPUT

    if code_str == RenderErrorCode.RIGHTS_BLOCKED.value or "rights" in reason_lower:
        return RetryabilityCategory.QA_BLOCKER

    if "browser" in reason_lower or "playwright" in reason_lower:
        return RetryabilityCategory.BROWSER_RUNTIME_FAILURE

    if "asset" in reason_lower or code_str == RenderErrorCode.ASSET_MISSING.value:
        return RetryabilityCategory.ASSET_PROVIDER_TRANSIENT

    if "provenance" in reason_lower or "truth" in reason_lower or "snapshot" in reason_lower:
        return RetryabilityCategory.PROVENANCE_FAILURE

    if code_str == RenderErrorCode.FFMPEG_FAILED.value:
        return RetryabilityCategory.TRANSIENT_INFRASTRUCTURE

    return RetryabilityCategory.INTERNAL_INVARIANT_FAILURE


class ProductionLifecycleService:
    """Authoritative lifecycle and reconciliation service for production execution."""

    TERMINAL_REQUEST_STATUSES = frozenset({
        ProductionRequestStatus.SUCCEEDED.value,
        ProductionRequestStatus.FAILED.value,
        ProductionRequestStatus.CANCELLED.value,
    })

    TERMINAL_JOB_STATES = frozenset({
        RenderJobState.SUCCEEDED.value,
        RenderJobState.FAILED.value,
        RenderJobState.CANCELLED.value,
    })

    ACTIVE_JOB_STATES = frozenset({
        RenderJobState.PENDING.value,
        RenderJobState.QUEUED.value,
        RenderJobState.RUNNING.value,
        RenderJobState.RETRY.value,
    })

    # ──────────────────────────────────────────────────────────────────────
    # ASYNC TRANSITION METHODS
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    async def fail_production_request(
        cls,
        session: AsyncSession,
        request_id: uuid.UUID,
        *,
        reason: str,
        error_code: str | RenderErrorCode | None = None,
        failure_stage: str | None = None,
        details: dict[str, Any] | None = None,
        lock: bool = True,
    ) -> tuple[bool, str]:
        """Atomically and idempotently mark a ProductionRequest as FAILED.

        Monotonic rule:
        - If already FAILED: idempotent no-op (preserves original failure timestamp & reason).
        - If SUCCEEDED or CANCELLED: illegal transition, rejected to preserve monotonic terminal status.
        - If DRAFT, READY, RUNNING: transitions to FAILED, sets outcome to BLOCKED,
          records structured failure metadata, and notifies orchestrator via DurableDispatch.
        """
        request = None
        if hasattr(session, "get"):
            try:
                request = await session.get(ProductionRequest, request_id)
            except Exception:
                pass
        if request is None and hasattr(session, "execute"):
            stmt = select(ProductionRequest).where(ProductionRequest.id == request_id)
            if lock:
                stmt = stmt.with_for_update()
            try:
                res = await session.execute(stmt)
                if hasattr(res, "scalar_one_or_none"):
                    request = res.scalar_one_or_none()
                elif hasattr(res, "scalars"):
                    request = res.scalars().first()
            except Exception:
                pass

        if request is None:
            logger.warning("fail_production_request: request not found", extra={"request_id": str(request_id)})
            return False, "NOT_FOUND"

        current_status = getattr(request, "status", None)
        if current_status == ProductionRequestStatus.FAILED.value:
            logger.info(
                "fail_production_request: already FAILED (idempotent)",
                extra={"request_id": str(request_id)},
            )
            return True, "ALREADY_FAILED"

        if current_status in (ProductionRequestStatus.SUCCEEDED.value, ProductionRequestStatus.CANCELLED.value):
            logger.warning(
                "fail_production_request: rejected attempt to fail terminal request",
                extra={"request_id": str(request_id), "current_status": current_status},
            )
            return False, "TERMINAL_IMMUTABLE"

        now = datetime.now(UTC)
        retryability = classify_failure_retryability(error_code, reason)
        error_code_val = (
            error_code.value if isinstance(error_code, RenderErrorCode) else str(error_code or "UNKNOWN")
        )

        raw_meta = getattr(request, "metadata_", None)
        metadata = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        metadata["failure_info"] = {
            "error_code": error_code_val,
            "failure_stage": failure_stage or "PRODUCTION",
            "reason": str(reason)[:1000],
            "retryability": retryability.value,
            "failed_at": now.isoformat(),
            "details": details or {},
        }

        setattr(request, "status", ProductionRequestStatus.FAILED.value)
        setattr(request, "outcome", ProductionOutcome.BLOCKED.value)
        setattr(request, "failed_at", now)
        setattr(request, "metadata_", metadata)

        await cls._enqueue_terminal_evaluation_async(session, request)
        logger.info(
            "fail_production_request: converged to FAILED",
            extra={
                "request_id": str(request_id),
                "error_code": error_code_val,
                "retryability": retryability.value,
            },
        )
        return True, "CONVERGED_FAILED"

    @classmethod
    async def succeed_production_request(
        cls,
        session: AsyncSession,
        request_id: uuid.UUID,
        *,
        media_artifact_id: uuid.UUID,
        qa_status: ProductionQAStatus,
        render_provenance: Any = None,
        lock: bool = True,
    ) -> tuple[bool, str]:
        """Atomically and idempotently mark a ProductionRequest as SUCCEEDED.

        Validates all production success invariants:
        - MediaArtifact exists, matches request_id, type is VIDEO.
        - File size > 0 and content hash is valid SHA-256.
        - Linked ProductionRenderJob is SUCCEEDED (cannot succeed referencing a FAILED job).
        - ProductionRuntimeTruth is recorded for the artifact.

        Monotonic rule:
        - If already SUCCEEDED: idempotent no-op.
        - If FAILED or CANCELLED: illegal transition, rejected.
        """
        stmt = select(ProductionRequest).where(ProductionRequest.id == request_id)
        if lock:
            stmt = stmt.with_for_update()

        request = (await session.execute(stmt)).scalar_one_or_none()
        if request is None:
            return False, "NOT_FOUND"

        current_status = getattr(request, "status", None)
        if current_status == ProductionRequestStatus.SUCCEEDED.value:
            return True, "ALREADY_SUCCEEDED"

        if current_status in (ProductionRequestStatus.FAILED.value, ProductionRequestStatus.CANCELLED.value):
            logger.warning(
                "succeed_production_request: rejected attempt to succeed terminal request",
                extra={"request_id": str(request_id), "current_status": current_status},
            )
            return False, "TERMINAL_IMMUTABLE"

        # Validate Invariant: MediaArtifact
        artifact = (
            await session.execute(
                select(MediaArtifact).where(MediaArtifact.id == media_artifact_id)
            )
        ).scalar_one_or_none()
        if artifact is None:
            raise LifecycleInvariantError(f"MediaArtifact {media_artifact_id} does not exist")
        if artifact.production_request_id != request_id:
            raise LifecycleInvariantError("MediaArtifact does not match ProductionRequest ID")
        if artifact.artifact_type != MediaArtifactType.VIDEO.value:
            raise LifecycleInvariantError(f"MediaArtifact type is not VIDEO: {artifact.artifact_type}")
        if getattr(artifact, "is_current", None) is False:
            raise LifecycleInvariantError("MediaArtifact must be marked as current")
        if artifact.file_size_bytes is None or artifact.file_size_bytes <= 0:
            raise LifecycleInvariantError("MediaArtifact file_size_bytes must be strictly positive")
        if not artifact.content_hash or not SHA256_HEX_PATTERN.match(artifact.content_hash):
            raise LifecycleInvariantError(
                "MediaArtifact content_hash must be valid SHA-256 (canonical 64 hexadecimal characters)"
            )

        # Validate Invariant: Physical file existence and size agreement (for local paths)
        if artifact.storage_uri:
            uri_str = str(artifact.storage_uri)
            if uri_str.startswith("file://") or uri_str.startswith(("/", "\\")) or (len(uri_str) > 2 and uri_str[1] == ":"):
                file_path = uri_str[7:] if uri_str.startswith("file://") else uri_str
                p = Path(file_path)
                if not p.exists() or not p.is_file():
                    raise LifecycleInvariantError(f"Authoritative physical artifact file missing at {file_path}")
                actual_size = p.stat().st_size
                if actual_size <= 0:
                    raise LifecycleInvariantError(f"Authoritative physical artifact file has zero size: {file_path}")
                if artifact.file_size_bytes and actual_size != artifact.file_size_bytes:
                    raise LifecycleInvariantError(
                        f"Physical artifact size ({actual_size} bytes) does not match persisted authority ({artifact.file_size_bytes} bytes)"
                    )

        # Validate Invariant: Linked RenderJob must be SUCCEEDED
        if artifact.render_job_id:
            job = (
                await session.execute(
                    select(ProductionRenderJob).where(ProductionRenderJob.id == artifact.render_job_id)
                )
            ).scalar_one_or_none()
            if job and job.state != RenderJobState.SUCCEEDED.value:
                raise LifecycleInvariantError(
                    f"Authoritative RenderJob {job.id} is not SUCCEEDED: {job.state}"
                )

        # Validate Invariant: ProductionRuntimeTruth v4 & hash matching
        truth = (
            await session.execute(
                select(ProductionRuntimeTruth).where(ProductionRuntimeTruth.artifact_id == media_artifact_id)
            )
        ).scalar_one_or_none()
        if truth is None:
            raise LifecycleInvariantError(
                f"ProductionRuntimeTruth record is missing for MediaArtifact {media_artifact_id}"
            )
        if truth.artifact_id != media_artifact_id:
            raise LifecycleInvariantError("ProductionRuntimeTruth artifact_id does not match MediaArtifact ID")
        truth_schema = getattr(truth, "schema_version", None)
        try:
            truth_schema_int = int(truth_schema)
        except (ValueError, TypeError):
            truth_schema_int = 0
        if truth_schema_int != 4:
            raise LifecycleInvariantError(
                f"ProductionRuntimeTruth schema_version must be 4, got {truth_schema}"
            )
        payload = getattr(truth, "payload", {}) or {}
        truth_hash = payload.get("artifact_sha256") or payload.get("artifact_hash")
        if truth_hash and str(truth_hash).lower() != str(artifact.content_hash).lower():
            raise LifecycleInvariantError(
                f"ProductionRuntimeTruth artifact hash mismatch: payload has '{truth_hash}' but artifact has '{artifact.content_hash}'"
            )

        now = datetime.now(UTC)
        outcome = (
            ProductionOutcome.BLOCKED.value
            if qa_status == ProductionQAStatus.BLOCKED
            else ProductionOutcome.RENDERED.value
        )
        raw_meta = getattr(request, "metadata_", None)
        metadata = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        if render_provenance is not None:
            metadata["render_provenance"] = render_provenance

        setattr(request, "status", ProductionRequestStatus.SUCCEEDED.value)
        setattr(request, "outcome", outcome)
        setattr(request, "completed_at", now)
        setattr(request, "metadata_", metadata)

        await cls._enqueue_terminal_evaluation_async(session, request)
        logger.info(
            "succeed_production_request: converged to SUCCEEDED",
            extra={"request_id": str(request_id), "outcome": outcome},
        )
        return True, "CONVERGED_SUCCEEDED"

    @classmethod
    async def cancel_production_request(
        cls,
        session: AsyncSession,
        request_id: uuid.UUID,
        *,
        reason: str = "User cancelled",
        lock: bool = True,
    ) -> tuple[bool, str]:
        """Atomically and idempotently cancel a non-terminal ProductionRequest and its active jobs."""
        stmt = select(ProductionRequest).where(ProductionRequest.id == request_id)
        if lock:
            stmt = stmt.with_for_update()

        request = (await session.execute(stmt)).scalar_one_or_none()
        if request is None:
            return False, "NOT_FOUND"

        current_status = getattr(request, "status", None)
        if current_status == ProductionRequestStatus.CANCELLED.value:
            return True, "ALREADY_CANCELLED"

        if current_status in (ProductionRequestStatus.SUCCEEDED.value, ProductionRequestStatus.FAILED.value):
            return False, "TERMINAL_IMMUTABLE"

        now = datetime.now(UTC)
        setattr(request, "status", ProductionRequestStatus.CANCELLED.value)
        setattr(request, "outcome", ProductionOutcome.BLOCKED.value)
        setattr(request, "completed_at", now)
        raw_meta = getattr(request, "metadata_", None)
        metadata = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        metadata["cancellation_reason"] = str(reason)[:500]
        setattr(request, "metadata_", metadata)

        # Cancel any active render jobs
        active_jobs = (
            await session.execute(
                select(ProductionRenderJob).where(
                    ProductionRenderJob.production_request_id == request_id,
                    ProductionRenderJob.state.in_(cls.ACTIVE_JOB_STATES),
                )
            )
        ).scalars().all()
        for j in active_jobs:
            j.state = RenderJobState.CANCELLED.value
            j.fencing_token = (j.fencing_token or 0) + 1
            j.lease_expires_at = now
            j.completed_at = now
            j.sanitized_error = f"Cancelled with ProductionRequest: {reason}"[:1000]

        await cls._enqueue_terminal_evaluation_async(session, request)
        return True, "CONVERGED_CANCELLED"

    # ──────────────────────────────────────────────────────────────────────
    # RECONCILIATION METHODS
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    async def reconcile_production_request(
        cls,
        session: AsyncSession,
        request_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Reconcile a single ProductionRequest against authoritative child and parent facts.

        Identifies and deterministically repairs:
        - Orphaned RUNNING requests whose authoritative RenderJobs are terminal FAILED/CANCELLED.
        - Orphaned RUNNING requests whose owning Task or MissionExecution is terminal FAILED/CANCELLED.
        - Unpropagated SUCCEEDED states where RenderJob and MediaArtifact are complete.
        - Hard-crashed RUNNING render jobs whose worker lease expired past TTL + grace.

        Guarantees:
        - Valid non-terminal states (e.g. Retry-6 QA WAITING_APPROVAL) are NEVER modified or marked failed.
        - Actively executing render jobs are NOT interrupted or cancelled.
        - Publishing is NEVER dispatched.
        """
        request = (
            await session.execute(
                select(ProductionRequest)
                .where(ProductionRequest.id == request_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if request is None:
            return {"status": "NOT_FOUND", "action": "NO_ACTION"}

        # 1. If request is already terminal, no convergence needed
        if request.status in cls.TERMINAL_REQUEST_STATUSES:
            return {
                "status": request.status,
                "action": "NO_ACTION",
                "reason": "already_terminal",
            }

        # 2. Query all associated jobs
        jobs = list(
            (
                await session.execute(
                    select(ProductionRenderJob)
                    .where(ProductionRenderJob.production_request_id == request_id)
                    .order_by(ProductionRenderJob.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

        # Check for expired worker leases on RUNNING jobs (P19-LR2)
        expired_leased_jobs = []
        for j in jobs:
            if (
                j.state == RenderJobState.RUNNING.value
                and j.lease_token is not None
                and j.lease_expires_at is not None
            ):
                check_stmt = select(ProductionRenderJob.id).where(
                    ProductionRenderJob.id == j.id,
                    ProductionRenderJob.lease_expires_at < func.now() - text(f"interval '{SWEEP_GRACE_SECONDS} seconds'"),
                )
                is_expired = (await session.execute(check_stmt)).scalar_one_or_none() is not None
                if is_expired:
                    expired_leased_jobs.append(j)

        if expired_leased_jobs:
            from omega.application.production_render_lease_service import (
                ProductionRenderLeaseService,
            )

            for ej in expired_leased_jobs:
                await ProductionRenderLeaseService.expire_lease(session, request_id, ej.id)

            # Re-fetch jobs to reflect newly converged terminal states
            jobs = list(
                (
                    await session.execute(
                        select(ProductionRenderJob)
                        .where(ProductionRenderJob.production_request_id == request_id)
                        .order_by(ProductionRenderJob.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )

        has_active_job = any(j.state in cls.ACTIVE_JOB_STATES for j in jobs)
        succeeded_job = next((j for j in jobs if j.state == RenderJobState.SUCCEEDED.value), None)

        # 3. Inspect parent task / mission execution state if linked
        owning_task_terminal_failed = False
        if request.mission_execution_id:
            tasks = list(
                (
                    await session.execute(
                        select(Task).where(
                            Task.execution_id == request.mission_execution_id,
                            Task.task_type == "production",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for t in tasks:
                if t.state in ("FAILED", "CANCELLED"):
                    owning_task_terminal_failed = True

        # CASE A: An active render job is in progress, and owning task is not terminal failed
        if has_active_job and not owning_task_terminal_failed:
            return {
                "status": request.status,
                "action": "NO_ACTION",
                "reason": "active_job_in_progress",
            }

        # CASE B: Authoritative job succeeded with complete artifact
        if succeeded_job is not None:
            artifact = (
                await session.execute(
                    select(MediaArtifact).where(
                        MediaArtifact.production_request_id == request.id,
                        MediaArtifact.render_job_id == succeeded_job.id,
                        MediaArtifact.artifact_type == MediaArtifactType.VIDEO.value,
                    )
                )
            ).scalar_one_or_none()
            if artifact and artifact.file_size_bytes and artifact.file_size_bytes > 0:
                truth = (
                    await session.execute(
                        select(ProductionRuntimeTruth).where(
                            ProductionRuntimeTruth.artifact_id == artifact.id
                        )
                    )
                ).scalar_one_or_none()
                if truth is not None:
                    # Safe to converge to SUCCEEDED
                    success, action = await cls.succeed_production_request(
                        session,
                        request.id,
                        media_artifact_id=artifact.id,
                        qa_status=ProductionQAStatus.PASSED,
                        lock=False,
                    )
                    return {
                        "status": request.status,
                        "action": action,
                        "reason": "converged_from_succeeded_job",
                    }

        # CASE C: All existing jobs are terminal FAILED or CANCELLED (Historical Retry-5 Defect)
        # OR owning task is permanently terminal failed without active work
        all_jobs_failed_or_cancelled = len(jobs) > 0 and all(
            j.state in (RenderJobState.FAILED.value, RenderJobState.CANCELLED.value) for j in jobs
        )

        if all_jobs_failed_or_cancelled or owning_task_terminal_failed:
            latest_job = jobs[0] if jobs else None
            error_code = latest_job.error_code if latest_job else RenderErrorCode.UNKNOWN.value
            err_msg = (
                latest_job.sanitized_error
                if latest_job and latest_job.sanitized_error
                else "Authoritative render jobs terminated without success"
            )
            success, action = await cls.fail_production_request(
                session,
                request.id,
                reason=err_msg,
                error_code=error_code,
                failure_stage="LIFECYCLE_RECONCILIATION",
                details={
                    "job_id": str(latest_job.id) if latest_job else None,
                    "owning_task_terminal_failed": owning_task_terminal_failed,
                },
                lock=False,
            )
            return {
                "status": request.status,
                "action": action,
                "reason": "orphaned_running_with_terminal_jobs",
            }

        # CASE D: Request has no jobs and no active work, but is RUNNING
        if len(jobs) == 0 and request.status == ProductionRequestStatus.RUNNING.value:
            success, action = await cls.fail_production_request(
                session,
                request.id,
                reason="Orphaned RUNNING ProductionRequest without allocated render jobs",
                error_code=RenderErrorCode.UNKNOWN.value,
                failure_stage="ORPHAN_SWEEP",
                lock=False,
            )
            return {
                "status": request.status,
                "action": action,
                "reason": "orphaned_without_jobs",
            }

        return {"status": request.status, "action": "NO_ACTION", "reason": "consistent"}

    @classmethod
    async def reconcile_orphaned_requests(
        cls,
        session: AsyncSession,
        channel_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Scan and reconcile orphaned RUNNING production requests.

        Safety:
        - NEVER creates PublishIntent or PublishAttempt.
        - NEVER creates duplicate RenderJobs.
        - Monotonic and idempotent.
        """
        stmt = (
            select(ProductionRequest.id)
            .where(ProductionRequest.status == ProductionRequestStatus.RUNNING.value)
            .order_by(ProductionRequest.created_at.asc())
            .limit(limit)
        )
        if channel_id:
            stmt = stmt.where(ProductionRequest.channel_id == channel_id)

        candidate_ids = (await session.execute(stmt)).scalars().all()

        scanned = 0
        converged_failed = 0
        converged_succeeded = 0
        no_action = 0

        for item in candidate_ids:
            r_id = getattr(item, "id", item)
            scanned += 1
            try:
                res = await cls.reconcile_production_request(session, r_id)
                action = res.get("action")
                if action == "CONVERGED_FAILED":
                    converged_failed += 1
                elif action == "CONVERGED_SUCCEEDED":
                    converged_succeeded += 1
                else:
                    no_action += 1
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.error(
                    "Reconciliation error on request",
                    extra={"request_id": str(r_id), "error": str(exc)},
                    exc_info=True,
                )

        return {
            "scanned": scanned,
            "converged_failed": converged_failed,
            "converged_succeeded": converged_succeeded,
            "no_action": no_action,
        }

    @classmethod
    async def reconcile_expired_leases(
        cls,
        session: AsyncSession,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Scan and expire RUNNING render jobs with expired worker leases.

        Leverages the partial composite index `idx_production_render_jobs_lease`.
        """
        stmt = (
            select(ProductionRenderJob.id, ProductionRenderJob.production_request_id)
            .where(
                ProductionRenderJob.state == RenderJobState.RUNNING.value,
                ProductionRenderJob.lease_token.isnot(None),
                ProductionRenderJob.lease_expires_at.isnot(None),
                ProductionRenderJob.lease_expires_at < func.now() - text("interval '10 seconds'"),
            )
            .order_by(ProductionRenderJob.lease_expires_at.asc())
            .limit(limit)
        )
        candidates = (await session.execute(stmt)).all()

        scanned = 0
        expired_count = 0
        from omega.application.production_render_lease_service import ProductionRenderLeaseService

        for job_id, req_id in candidates:
            scanned += 1
            try:
                success, action = await ProductionRenderLeaseService.expire_lease(
                    session, req_id, job_id
                )
                if success:
                    expired_count += 1
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.error(
                    "Error expiring stale lease",
                    extra={"job_id": str(job_id), "error": str(exc)},
                    exc_info=True,
                )

        return {"scanned": scanned, "expired": expired_count}

    # ──────────────────────────────────────────────────────────────────────
    # SYNC ADAPTER METHODS (FOR CELERY WORKER EXECUTION)
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    def fail_production_request_sync(
        cls,
        session: Session,
        request_id: uuid.UUID,
        *,
        reason: str,
        error_code: str | RenderErrorCode | None = None,
        failure_stage: str | None = None,
        details: dict[str, Any] | None = None,
        lock: bool = True,
    ) -> tuple[bool, str]:
        """Synchronous version of fail_production_request for Celery task workers."""
        query = session.query(ProductionRequest).filter(ProductionRequest.id == request_id)
        if lock:
            query = query.with_for_update()

        request = query.first()
        if request is None:
            return False, "NOT_FOUND"

        current_status = getattr(request, "status", None)
        if current_status == ProductionRequestStatus.FAILED.value:
            return True, "ALREADY_FAILED"

        if current_status in (ProductionRequestStatus.SUCCEEDED.value, ProductionRequestStatus.CANCELLED.value):
            return False, "TERMINAL_IMMUTABLE"

        now = datetime.now(UTC)
        retryability = classify_failure_retryability(error_code, reason)
        error_code_val = (
            error_code.value if isinstance(error_code, RenderErrorCode) else str(error_code or "UNKNOWN")
        )

        raw_meta = getattr(request, "metadata_", None)
        metadata = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        metadata["failure_info"] = {
            "error_code": error_code_val,
            "failure_stage": failure_stage or "PRODUCTION",
            "reason": str(reason)[:1000],
            "retryability": retryability.value,
            "failed_at": now.isoformat(),
            "details": details or {},
        }

        setattr(request, "status", ProductionRequestStatus.FAILED.value)
        setattr(request, "outcome", ProductionOutcome.BLOCKED.value)
        setattr(request, "failed_at", now)
        setattr(request, "metadata_", metadata)

        cls._enqueue_terminal_evaluation_sync(session, request)
        return True, "CONVERGED_FAILED"

    @classmethod
    def reconcile_production_request_sync(
        cls,
        session: Session,
        request_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Synchronous version of reconcile_production_request for Celery task workers."""
        request = (
            session.query(ProductionRequest)
            .filter(ProductionRequest.id == request_id)
            .with_for_update()
            .first()
        )
        if request is None:
            return {"status": "NOT_FOUND", "action": "NO_ACTION"}

        if request.status in cls.TERMINAL_REQUEST_STATUSES:
            return {
                "status": request.status,
                "action": "NO_ACTION",
                "reason": "already_terminal",
            }

        jobs = (
            session.query(ProductionRenderJob)
            .filter(ProductionRenderJob.production_request_id == request_id)
            .order_by(ProductionRenderJob.created_at.desc())
            .all()
        )

        has_active_job = any(j.state in cls.ACTIVE_JOB_STATES for j in jobs)

        owning_task_terminal_failed = False
        if request.mission_execution_id:
            tasks = (
                session.query(Task)
                .filter(
                    Task.execution_id == request.mission_execution_id,
                    Task.task_type == "production",
                )
                .all()
            )
            for t in tasks:
                if t.state in ("FAILED", "CANCELLED"):
                    owning_task_terminal_failed = True

        if has_active_job and not owning_task_terminal_failed:
            return {
                "status": request.status,
                "action": "NO_ACTION",
                "reason": "active_job_in_progress",
            }

        all_jobs_failed_or_cancelled = len(jobs) > 0 and all(
            j.state in (RenderJobState.FAILED.value, RenderJobState.CANCELLED.value) for j in jobs
        )

        if all_jobs_failed_or_cancelled or owning_task_terminal_failed:
            latest_job = jobs[0] if jobs else None
            error_code = latest_job.error_code if latest_job else RenderErrorCode.UNKNOWN.value
            err_msg = (
                latest_job.sanitized_error
                if latest_job and latest_job.sanitized_error
                else "Authoritative render jobs terminated without success"
            )
            success, action = cls.fail_production_request_sync(
                session,
                request.id,
                reason=err_msg,
                error_code=error_code,
                failure_stage="LIFECYCLE_RECONCILIATION",
                details={
                    "job_id": str(latest_job.id) if latest_job else None,
                    "owning_task_terminal_failed": owning_task_terminal_failed,
                },
                lock=False,
            )
            return {
                "status": request.status,
                "action": action,
                "reason": "orphaned_running_with_terminal_jobs",
            }

        return {"status": request.status, "action": "NO_ACTION", "reason": "consistent"}

    # ──────────────────────────────────────────────────────────────────────
    # INTERNAL HELPERS
    # ──────────────────────────────────────────────────────────────────────

    @classmethod
    async def _enqueue_terminal_evaluation_async(
        cls,
        session: AsyncSession,
        request: ProductionRequest,
    ) -> None:
        """Wake orchestrator evaluation if linked to a MissionExecution."""
        execution_id = request.mission_execution_id
        if execution_id is None:
            return

        try:
            mission_id = (
                await session.execute(
                    select(MissionExecution.mission_id).where(MissionExecution.id == execution_id)
                )
            ).scalar_one_or_none()
            if mission_id is None:
                return

            await DurableDispatchService.enqueue_async(
                session,
                idempotency_key=f"production-terminal-evaluation:{request.id}:{request.status}",
                task_name="omega.orchestrator.evaluate",
                args=[str(mission_id), str(execution_id)],
                purpose="PRODUCTION_REQUEST_TERMINAL_EVALUATION",
                mission_id=mission_id,
                mission_execution_id=execution_id,
                production_request_id=request.id,
            )
        except Exception:
            pass

    @classmethod
    def _enqueue_terminal_evaluation_sync(
        cls,
        session: Session,
        request: ProductionRequest,
    ) -> None:
        """Synchronously wake orchestrator evaluation if linked to a MissionExecution."""
        execution_id = request.mission_execution_id
        if execution_id is None:
            return

        execution = session.query(MissionExecution).filter(MissionExecution.id == execution_id).first()
        if execution is None or execution.mission_id is None:
            return

        DurableDispatchService.enqueue(
            session,
            idempotency_key=f"production-terminal-evaluation:{request.id}:{request.status}",
            task_name="omega.orchestrator.evaluate",
            args=[str(execution.mission_id), str(execution_id)],
            purpose="PRODUCTION_REQUEST_TERMINAL_EVALUATION",
            mission_id=execution.mission_id,
            mission_execution_id=execution_id,
            production_request_id=request.id,
        )
