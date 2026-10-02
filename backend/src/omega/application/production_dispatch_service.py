"""ProductionDispatchService (P19-LR3).

Authoritative pre-lease dispatch authority, atomic render allocation + outbox
enrollment, generation tracking, and queue-stall reconciliation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.durable_dispatch import (
    CLAIMED,
    DEAD_LETTER,
    PENDING,
    RETRY,
    SENT,
    DurableDispatchService,
)
from omega.application.production_service import ProductionService
from omega.domain.production import (
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

logger = logging.getLogger(__name__)


class ProductionDispatchService:
    """Pre-lease dispatch authority and queue-stall recovery service."""

    @classmethod
    async def allocate_and_enqueue_render(
        cls,
        session: AsyncSession,
        channel_id: uuid.UUID,
        request_id: uuid.UUID,
        idempotency_key: str,
        *,
        is_rerender: bool = False,
        prod_service: ProductionService | None = None,
        **correlations: Any,
    ) -> tuple[ProductionRenderJob, RenderPlan, bool, DurableDispatchIntent | None]:
        """Atomically allocate a ProductionRenderJob and enqueue its DurableDispatchIntent.

        Guarantees that `ProductionRenderJob(state=QUEUED, dispatch_generation=1)`
        and `DurableDispatchIntent` for generation 1 commit in ONE database transaction.
        """
        try:
            service = prod_service or ProductionService()
            job, render_plan, is_new = await service.allocate_render_job(
                session,
                channel_id,
                request_id,
                idempotency_key,
                is_rerender=is_rerender,
                commit=False,
                dispatch_generation=1,
                dispatch_started_at=func.now(),
            )

            intent: DurableDispatchIntent | None = None
            if is_new:
                intent = await DurableDispatchService.enqueue_async(
                    session,
                    idempotency_key=f"render-dispatch:{request_id}:{job.id}:gen1",
                    task_name="omega.production.render",
                    args=[str(channel_id), str(request_id), str(job.id), 1],
                    purpose="PRODUCTION_RENDER_DISPATCH",
                    production_request_id=request_id,
                    render_job_id=job.id,
                    **correlations,
                )

            await session.commit()
        except Exception:
            await session.rollback()
            raise

        if hasattr(session, "refresh"):
            try:
                await session.refresh(job)
            except Exception:
                pass

        if is_new:
            logger.info(
                "production_dispatch_intent_enqueued",
                extra={
                    "event": "production_dispatch_intent_enqueued",
                    "job_id": str(job.id),
                    "request_id": str(request_id),
                    "dispatch_generation": 1,
                    "intent_id": str(intent.id) if intent else None,
                },
            )

        return job, render_plan, is_new, intent

    @classmethod
    async def discover_stalled_dispatch_candidates(
        cls,
        session: AsyncSession,
        *,
        timeout_seconds: int = 300,
        limit: int = 25,
    ) -> list[tuple[uuid.UUID, uuid.UUID, int]]:
        """Phase A: Read-only snapshot discovery of stalled QUEUED candidates.

        Acquires ZERO row locks. Uses partial index `ix_production_render_jobs_dispatch_stall`.
        """
        cutoff = func.now() - text(f"interval '{int(timeout_seconds)} seconds'")
        stmt = (
            select(
                ProductionRenderJob.id,
                ProductionRenderJob.production_request_id,
                ProductionRenderJob.dispatch_generation,
            )
            .where(
                ProductionRenderJob.state == RenderJobState.QUEUED.value,
                ProductionRenderJob.dispatch_started_at.is_not(None),
                ProductionRenderJob.dispatch_started_at <= cutoff,
                ProductionRenderJob.started_at.is_(None),
            )
            .order_by(ProductionRenderJob.created_at.asc())
            .limit(limit)
        )
        res = await session.execute(stmt)
        return [(row[0], row[1], row[2]) for row in res.fetchall()]

    @classmethod
    async def reconcile_dispatch_stall_candidate(
        cls,
        session: AsyncSession,
        request_id: uuid.UUID,
        job_id: uuid.UUID,
        expected_generation: int,
        *,
        timeout_seconds: int = 300,
        max_generations: int = 3,
    ) -> dict[str, Any]:
        """Phase B: Reconcile one candidate under canonical row locks.

        Canonical lock order: ProductionRequest -> ProductionRenderJob.
        """
        # 1. Lock ProductionRequest FIRST
        req_res = await session.execute(
            select(ProductionRequest)
            .where(ProductionRequest.id == request_id)
            .with_for_update()
        )
        request = req_res.scalar_one_or_none()
        if not request or request.status != ProductionRequestStatus.RUNNING.value:
            await session.rollback()
            return {"action": "NO_ACTION", "reason": "request_not_running"}

        # 2. Lock ProductionRenderJob SECOND
        job_res = await session.execute(
            select(ProductionRenderJob)
            .where(
                ProductionRenderJob.id == job_id,
                ProductionRenderJob.production_request_id == request_id,
            )
            .with_for_update()
        )
        job = job_res.scalar_one_or_none()
        if not job:
            await session.rollback()
            return {"action": "NO_ACTION", "reason": "job_not_found"}

        # 3. Re-verify eligibility preconditions under lock
        job_state = job.state
        if job_state != RenderJobState.QUEUED.value:
            await session.rollback()
            return {"action": "NO_ACTION", "reason": f"job_state_{job_state}"}
        if job.started_at is not None or job.lease_token is not None:
            await session.rollback()
            return {"action": "NO_ACTION", "reason": "execution_authority_active"}
        if job.dispatch_started_at is None:
            await session.rollback()
            return {"action": "NO_ACTION", "reason": "legacy_unenrolled"}
        if job.dispatch_generation != expected_generation:
            await session.rollback()
            return {"action": "NO_ACTION", "reason": "generation_mismatch"}

        # 4. Inspect current-generation DurableDispatchIntent
        intent_stmt = (
            select(DurableDispatchIntent)
            .where(
                DurableDispatchIntent.render_job_id == job.id,
                or_(
                    DurableDispatchIntent.idempotency_key
                    == f"render-dispatch:{request_id}:{job.id}:gen{expected_generation}",
                    DurableDispatchIntent.idempotency_key
                    == f"render-dispatch:{request_id}:{job.id}",
                ),
            )
            .order_by(DurableDispatchIntent.created_at.desc())
        )
        intent = (await session.execute(intent_stmt)).scalars().first()

        can_redispatch = False
        if intent is None:
            logger.warning(
                "production_dispatch_missing_intent_invariant_violation",
                extra={
                    "event": "production_dispatch_missing_intent_invariant_violation",
                    "job_id": str(job.id),
                    "generation": expected_generation,
                },
            )
            can_redispatch = True
        elif intent.state in (PENDING, CLAIMED, RETRY):
            # Outbox relay owns active transport for this generation — NO_ACTION
            intent_state = intent.state
            await session.rollback()
            return {"action": "NO_ACTION", "reason": f"outbox_active_{intent_state.lower()}"}
        elif intent.state == DEAD_LETTER:
            # Relay exhausted all attempts for this generation — immediately eligible
            can_redispatch = True
        elif intent.state == SENT:
            # Verify DB generation age exceeds timeout using DB clock and persisted column
            stalled_stmt = (
                select(
                    ProductionRenderJob.dispatch_started_at
                    <= func.now() - text(f"interval '{int(timeout_seconds)} seconds'")
                )
                .where(ProductionRenderJob.id == job.id)
            )
            is_stalled = (await session.execute(stalled_stmt)).scalar_one_or_none()
            if not is_stalled:
                await session.rollback()
                return {"action": "NO_ACTION", "reason": "timeout_not_elapsed"}
            can_redispatch = True
        else:
            intent_state = intent.state
            await session.rollback()
            return {"action": "NO_ACTION", "reason": f"unknown_outbox_state_{intent_state}"}

        if not can_redispatch:
            await session.rollback()
            return {"action": "NO_ACTION", "reason": "not_eligible"}

        # 5. Redispatch vs Exhaustion
        if job.dispatch_generation < max_generations:
            new_gen = job.dispatch_generation + 1
            job.dispatch_generation = new_gen
            job.dispatch_started_at = func.now()

            new_intent = await DurableDispatchService.enqueue_async(
                session,
                idempotency_key=f"render-dispatch:{request_id}:{job.id}:gen{new_gen}",
                task_name="omega.production.render",
                args=[str(request.channel_id), str(request.id), str(job.id), new_gen],
                purpose="PRODUCTION_RENDER_DISPATCH",
                production_request_id=request.id,
                render_job_id=job.id,
                mission_id=getattr(intent, "mission_id", None) if intent else None,
                mission_execution_id=getattr(intent, "mission_execution_id", None) if intent else None,
                mission_task_id=getattr(intent, "mission_task_id", None) if intent else None,
            )
            await session.commit()

            logger.warning(
                "production_dispatch_redispatched",
                extra={
                    "event": "production_dispatch_redispatched",
                    "job_id": str(job.id),
                    "old_generation": expected_generation,
                    "new_generation": new_gen,
                    "intent_id": str(new_intent.id),
                },
            )
            try:
                from omega.application.observability.telemetry import increment_event_counter_async
                await increment_event_counter_async("omega_render_redispatches_total", 1)
            except Exception:
                pass
            return {"action": "REDISPATCHED", "new_generation": new_gen, "job_id": str(job.id)}

        # Budget exhausted -> Monotonically fail job and parent request
        job.state = RenderJobState.FAILED.value
        job.error_code = RenderErrorCode.DISPATCH_DELIVERY_EXHAUSTED.value
        job.sanitized_error = (
            f"Render dispatch delivery timed out after {max_generations} generations without worker pickup"
        )
        job.completed_at = func.now()

        from omega.application.production_lifecycle_service import (
            ProductionLifecycleService,
        )

        await ProductionLifecycleService.fail_production_request(
            session,
            request.id,
            reason=job.sanitized_error,
            error_code=RenderErrorCode.DISPATCH_DELIVERY_EXHAUSTED,
            failure_stage="DISPATCH_STALL_RECONCILIATION",
            details={"job_id": str(job.id), "dispatch_generation": job.dispatch_generation},
            lock=False,  # Already locked in canonical order above!
        )
        await session.commit()
        try:
            from omega.application.observability.telemetry import increment_event_counter_async
            await increment_event_counter_async("omega_render_dispatch_exhaustions_total", 1)
        except Exception:
            pass

        logger.error(
            "production_dispatch_delivery_exhausted",
            extra={
                "event": "production_dispatch_delivery_exhausted",
                "job_id": str(job.id),
                "request_id": str(request.id),
                "generation": job.dispatch_generation,
            },
        )
        return {"action": "EXHAUSTED", "error_code": "DISPATCH_DELIVERY_EXHAUSTED", "job_id": str(job.id)}

    @classmethod
    async def reconcile_all_dispatch_stalls(
        cls,
        session: AsyncSession,
        *,
        timeout_seconds: int = 300,
        max_generations: int = 3,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Discover and reconcile bounded batch of stalled render dispatches."""
        candidates = await cls.discover_stalled_dispatch_candidates(
            session,
            timeout_seconds=timeout_seconds,
            limit=limit,
        )
        results = {"candidates": len(candidates), "redispatched": 0, "exhausted": 0, "skipped": 0}
        for job_id, req_id, expected_gen in candidates:
            try:
                res = await cls.reconcile_dispatch_stall_candidate(
                    session,
                    req_id,
                    job_id,
                    expected_gen,
                    timeout_seconds=timeout_seconds,
                    max_generations=max_generations,
                )
                action = res.get("action")
                if action == "REDISPATCHED":
                    results["redispatched"] += 1
                elif action == "EXHAUSTED":
                    results["exhausted"] += 1
                else:
                    results["skipped"] += 1
            except Exception as exc:
                await session.rollback()
                logger.error(
                    "reconcile_dispatch_stall_candidate_failed",
                    exc_info=True,
                    extra={
                        "event": "reconcile_dispatch_stall_candidate_failed",
                        "job_id": str(job_id),
                        "error": str(exc),
                    },
                )
                results["skipped"] += 1
        return results
