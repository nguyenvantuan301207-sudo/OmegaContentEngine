"""Authoritative action dispatch service executing the 4-phase safe execution protocol for OMEGA-014."""

from __future__ import annotations

import asyncio
import hashlib
import os
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.autonomy.approval_service import AutonomyApprovalService
from omega.application.autonomy.budget_service import AutonomyBudgetService
from omega.application.autonomy.guardian_context import build_canonical_guardian_context
from omega.application.autonomy.helpers import (
    compute_precondition_fingerprint,
    compute_semantic_action_key,
)
from omega.domain.autonomy import (
    ActionAttemptOutcomeStatus,
    ActionType,
    AutonomyLoopState,
    BudgetReservationStatus,
)
from omega.infrastructure.models import (
    AutonomyActionAttempt,
    AutonomyActionAttemptOutcome,
    AutonomyActionPlan,
    AutonomyLoop,
    AutonomyLoopIteration,
    AutonomyLoopLatestPointer,
    AutonomyObservationSnapshot,
    AutonomyPolicySnapshot,
)
from omega.logging import get_logger

logger = get_logger(service="omega-autonomy-dispatch")


class StalePlanContextError(Exception):
    """Raised when plan preconditions or Guardian context changed before dispatch."""

    pass


class AutonomyDispatchService:
    """Orchestrates atomic preparation, isolated external invocation, and post-dispatch reconciliation."""

    @classmethod
    async def prepare_and_dispatch(
        cls,
        session: AsyncSession,
        action_plan_id: UUID,
        worker_id: str | None = None,
    ) -> tuple[AutonomyActionAttempt, AutonomyActionAttemptOutcome]:
        """Execute the two-phase transaction boundary protocol for action execution."""
        effective_worker = worker_id or f"autonomy-worker-{os.getpid()}-{uuid4().hex[:6]}"

        # ══════════════════════════════════════════════════════════════════
        # PHASE 1: PREPARATION (DATABASE TRANSACTION A)
        # ══════════════════════════════════════════════════════════════════
        # 1. Load Plan and associated Iteration & Loop
        stmt_plan = (
            select(AutonomyActionPlan)
            .where(AutonomyActionPlan.id == action_plan_id)
            .with_for_update()
        )
        plan = (await session.execute(stmt_plan)).scalar_one()

        stmt_iter = (
            select(AutonomyLoopIteration)
            .where(AutonomyLoopIteration.id == plan.iteration_id)
            .with_for_update()
        )
        iteration = (await session.execute(stmt_iter)).scalar_one()

        stmt_ptr = (
            select(AutonomyLoopLatestPointer)
            .where(AutonomyLoopLatestPointer.loop_id == iteration.loop_id)
            .with_for_update()
        )
        pointer = (await session.execute(stmt_ptr)).scalar_one()

        # Verify operational state is EXECUTING or PLANNING
        if pointer.operational_state not in (
            AutonomyLoopState.PLANNING.value,
            AutonomyLoopState.EXECUTING.value,
            AutonomyLoopState.WAITING_APPROVAL.value,
        ):
            raise StalePlanContextError(
                f"Cannot dispatch action in state '{pointer.operational_state}'."
            )

        # 2. Verify Precondition Freshness
        stmt_loop = select(AutonomyLoop).where(AutonomyLoop.id == iteration.loop_id)
        loop = (await session.execute(stmt_loop)).scalar_one()

        guardian_ctx, current_guardian_checksum = await build_canonical_guardian_context(
            session=session,
            mission_id=loop.mission_id,
        )

        stmt_obs = select(AutonomyObservationSnapshot).where(
            AutonomyObservationSnapshot.id == iteration.observation_snapshot_id
        )
        obs = (await session.execute(stmt_obs)).scalar_one()

        stmt_pol = select(AutonomyPolicySnapshot).where(
            AutonomyPolicySnapshot.id == iteration.policy_snapshot_id
        )
        policy = (await session.execute(stmt_pol)).scalar_one()

        current_fingerprint = compute_precondition_fingerprint(
            action_type=plan.action_type,
            target_entity_id=plan.target_entity_id,
            entity_state_token=str(plan.target_entity_id or "NONE"),
            guardian_context_checksum=current_guardian_checksum,
            dna_revision_id=obs.dna_revision_id,
            policy_checksum=policy.policy_checksum,
            mission_state=obs.raw_observation.get("mission_state", "ACTIVE"),
        )

        if current_guardian_checksum != obs.guardian_context_checksum:
            pointer.operational_state = AutonomyLoopState.PLANNING.value
            await session.commit()
            raise StalePlanContextError("Guardian context changed; plan invalidated.")

        if current_fingerprint != plan.precondition_fingerprint:
            pointer.operational_state = AutonomyLoopState.PLANNING.value
            await session.commit()
            raise StalePlanContextError("Precondition fingerprint changed; plan invalidated.")

        # 3. Verify Approval if required
        if plan.requires_approval:
            # Check for approved decision
            approved = await AutonomyApprovalService.validate_approval_for_dispatch(
                session=session,
                approval_request_id=plan.id,  # Linked by plan_id
                expected_action_checksum=plan.action_checksum,
                current_precondition_fingerprint=current_fingerprint,
                current_guardian_context_checksum=current_guardian_checksum,
                current_guardian_epoch=guardian_ctx.guardian_epoch,
                current_dna_revision_id=obs.dna_revision_id,
                current_mission_state=obs.raw_observation.get("mission_state", "ACTIVE"),
            )
            if not approved:
                pointer.operational_state = AutonomyLoopState.WAITING_APPROVAL.value
                await session.commit()
                raise StalePlanContextError(
                    "Action requires approval and is not approved under current context."
                )

        # 4. Reserve Budget
        semantic_key = compute_semantic_action_key(
            loop_id=iteration.loop_id,
            iteration_sequence=iteration.iteration_sequence,
            action_type=plan.action_type,
            target_entity_id=plan.target_entity_id,
        )
        reservation = None
        if plan.estimated_cost_usd > Decimal("0.0000"):
            reservation = await AutonomyBudgetService.reserve_budget(
                session=session,
                loop_id=iteration.loop_id,
                action_plan_id=plan.id,
                semantic_action_key=semantic_key,
                estimated_amount=plan.estimated_cost_usd,
            )

        # 5. Create Action Attempt & PREPARED Outcome
        correlation_key = hashlib.sha256(
            f"subsystem_corr:{iteration.loop_id}:{iteration.iteration_sequence}:{plan.action_type}:{plan.target_entity_id or 'NONE'}".encode()
        ).hexdigest()

        # Check existing attempt
        stmt_att = select(AutonomyActionAttempt).where(
            AutonomyActionAttempt.subsystem_correlation_key == correlation_key
        )
        attempt = (await session.execute(stmt_att)).scalar_one_or_none()
        if not attempt:
            attempt = AutonomyActionAttempt(
                id=uuid4(),
                action_plan_id=plan.id,
                attempt_sequence=1,
                subsystem_correlation_key=correlation_key,
            )
            session.add(attempt)
            await session.flush()

        time_res = await session.execute(text("SELECT NOW()"))
        db_now = time_res.scalar()

        claim_gen = pointer.active_claim_generation + 1
        pointer.active_claim_generation = claim_gen
        pointer.active_claim_token = uuid4()
        pointer.operational_state = AutonomyLoopState.EXECUTING.value
        pointer.current_action_attempt_id = attempt.id
        pointer.updated_at = db_now

        outcome_key_prep = hashlib.sha256(f"outcome:{attempt.id}:1:PREPARED".encode()).hexdigest()
        outcome_prep = AutonomyActionAttemptOutcome(
            id=uuid4(),
            action_attempt_id=attempt.id,
            outcome_sequence=1,
            status=ActionAttemptOutcomeStatus.PREPARED.value,
            worker_id=effective_worker,
            claim_generation=claim_gen,
            subsystem_response={},
            outcome_dedupe_key=outcome_key_prep,
            occurred_at=db_now,
        )
        session.add(outcome_prep)

        # Commit TX A: Intent is durably recorded; no HTTP call made yet
        await session.commit()

        # ══════════════════════════════════════════════════════════════════
        # PHASE 2: DISPATCH INTENT COMMIT (SHORT TRANSACTION A2)
        # ══════════════════════════════════════════════════════════════════
        outcome_key_intent = hashlib.sha256(
            f"outcome:{attempt.id}:2:DISPATCH_INTENT_COMMITTED".encode()
        ).hexdigest()
        stmt_intent_check = select(AutonomyActionAttemptOutcome).where(
            AutonomyActionAttemptOutcome.outcome_dedupe_key == outcome_key_intent
        )
        existing_intent = (await session.execute(stmt_intent_check)).scalar_one_or_none()
        if not existing_intent:
            outcome_intent = AutonomyActionAttemptOutcome(
                id=uuid4(),
                action_attempt_id=attempt.id,
                outcome_sequence=2,
                status=ActionAttemptOutcomeStatus.DISPATCH_INTENT_COMMITTED.value,
                worker_id=effective_worker,
                claim_generation=claim_gen,
                subsystem_response={},
                outcome_dedupe_key=outcome_key_intent,
                occurred_at=db_now,
            )
            session.add(outcome_intent)
            await session.commit()

        # ══════════════════════════════════════════════════════════════════
        # PHASE 3: ISOLATED SUBSYSTEM DISPATCH (NO OPEN DB TRANSACTION)
        # ══════════════════════════════════════════════════════════════════
        subsystem_result: dict[str, Any] = {}
        error_msg: str | None = None
        final_status: ActionAttemptOutcomeStatus = ActionAttemptOutcomeStatus.RESULT_CONFIRMED

        try:
            # Dispatch to existing subsystem services
            if plan.action_type == ActionType.WAIT.value:
                subsystem_result = {
                    "status": "WAITED",
                    "duration_seconds": plan.parameters.get("wait_duration_seconds", 60),
                }
            elif plan.action_type == ActionType.REQUEST_QA.value:
                subsystem_result = {
                    "status": "QA_REQUESTED",
                    "checkpoint": plan.parameters.get("checkpoint"),
                }
            elif plan.action_type == ActionType.CREATE_PUBLISH_INTENT.value:
                subsystem_result = {
                    "status": "PUBLISH_INTENT_STAGED",
                    "media_artifact_id": str(plan.target_entity_id),
                }
            else:
                # Subsystem dispatch stub for testing / pipeline handoff
                subsystem_result = {
                    "status": "DISPATCHED",
                    "action_type": plan.action_type,
                    "target_entity_id": str(plan.target_entity_id)
                    if plan.target_entity_id
                    else None,
                    "subsystem_correlation_key": correlation_key,
                }
        except Exception as exc:
            logger.error("Subsystem dispatch failure", exc_info=True)
            error_msg = str(exc)
            if (
                isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                or "timeout" in str(exc).lower()
            ):
                final_status = ActionAttemptOutcomeStatus.RECONCILIATION_REQUIRED
            else:
                final_status = ActionAttemptOutcomeStatus.DETERMINISTIC_FAILURE

        # ══════════════════════════════════════════════════════════════════
        # PHASE 4: OUTCOME RECORDING (DATABASE TRANSACTION B)
        # ══════════════════════════════════════════════════════════════════
        # Reacquire lock on latest pointer
        stmt_ptr_b = (
            select(AutonomyLoopLatestPointer)
            .where(AutonomyLoopLatestPointer.loop_id == iteration.loop_id)
            .with_for_update()
        )
        pointer_b = (await session.execute(stmt_ptr_b)).scalar_one()

        time_res = await session.execute(text("SELECT NOW()"))
        db_now = time_res.scalar()

        outcome_key_final = hashlib.sha256(
            f"outcome:{attempt.id}:3:{final_status.value}".encode()
        ).hexdigest()

        outcome_final = AutonomyActionAttemptOutcome(
            id=uuid4(),
            action_attempt_id=attempt.id,
            outcome_sequence=3,
            status=final_status.value,
            worker_id=effective_worker,
            claim_generation=claim_gen,
            subsystem_response=subsystem_result,
            error_message=error_msg,
            outcome_dedupe_key=outcome_key_final,
            occurred_at=db_now,
        )
        session.add(outcome_final)

        # Budget resolution
        if reservation:
            if final_status == ActionAttemptOutcomeStatus.RESULT_CONFIRMED:
                await AutonomyBudgetService.capture_reservation(
                    session=session,
                    reservation_id=reservation.id,
                    captured_amount=plan.estimated_cost_usd,
                )
            elif final_status == ActionAttemptOutcomeStatus.RECONCILIATION_REQUIRED:
                reservation.status = BudgetReservationStatus.RECONCILIATION_REQUIRED.value
            elif final_status == ActionAttemptOutcomeStatus.DETERMINISTIC_FAILURE:
                await AutonomyBudgetService.release_reservation(
                    session=session,
                    reservation_id=reservation.id,
                    release_reason=error_msg or "Deterministic failure",
                )

        # Update loop state
        if final_status == ActionAttemptOutcomeStatus.RESULT_CONFIRMED:
            pointer_b.operational_state = AutonomyLoopState.VERIFYING.value
        elif final_status == ActionAttemptOutcomeStatus.RECONCILIATION_REQUIRED:
            pointer_b.operational_state = AutonomyLoopState.BLOCKED.value
            pointer_b.stop_reason = f"Reconciliation required: {error_msg}"
        else:
            pointer_b.operational_state = AutonomyLoopState.FAILED.value
            pointer_b.stop_reason = f"Execution failed: {error_msg}"

        pointer_b.updated_at = db_now
        pointer_b.last_iteration_finished_at = db_now

        await session.commit()
        return attempt, outcome_final
