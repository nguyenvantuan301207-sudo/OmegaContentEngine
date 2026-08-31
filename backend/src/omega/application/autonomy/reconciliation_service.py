"""Authoritative reconciliation service resolving ambiguous or timed-out action attempts for OMEGA-014."""

from __future__ import annotations

import hashlib
import os
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.autonomy.budget_service import AutonomyBudgetService
from omega.domain.autonomy import ActionAttemptOutcomeStatus, BudgetReservationStatus
from omega.infrastructure.models import (
    AutonomyActionAttempt,
    AutonomyActionAttemptOutcome,
    AutonomyActionPlan,
    AutonomyBudgetReservation,
    ContentGenerationRequest,
    ProductionRequest,
    PublishAttempt,
    ScheduleDecision,
)
from omega.logging import get_logger

logger = get_logger(service="omega-autonomy-reconciliation")


class AutonomyReconciliationService:
    """Reconciles ambiguous or unverified action attempts against subsystem source of truth."""

    @classmethod
    async def reconcile_attempt(
        cls,
        session: AsyncSession,
        action_attempt_id: UUID,
        worker_id: str | None = None,
    ) -> AutonomyActionAttemptOutcome:
        """Reconcile an action attempt by verifying actual subsystem existence."""
        effective_worker = worker_id or f"reconciler-{os.getpid()}-{uuid4().hex[:6]}"

        stmt_att = (
            select(AutonomyActionAttempt)
            .where(AutonomyActionAttempt.id == action_attempt_id)
            .with_for_update()
        )
        attempt = (await session.execute(stmt_att)).scalar_one_or_none()
        if not attempt:
            raise ValueError(f"ActionAttempt '{action_attempt_id}' not found.")

        stmt_plan = select(AutonomyActionPlan).where(
            AutonomyActionPlan.id == attempt.action_plan_id
        )
        plan = (await session.execute(stmt_plan)).scalar_one()

        correlation_key = attempt.subsystem_correlation_key

        # 1. Query Target Subsystems by correlation key
        entity_found = False
        subsystem_details = {}

        # Content Engine Check
        stmt_content = select(ContentGenerationRequest).where(
            ContentGenerationRequest.idempotency_key == correlation_key
        )
        content_obj = (await session.execute(stmt_content)).scalar_one_or_none()
        if content_obj:
            entity_found = True
            subsystem_details = {"subsystem": "content", "entity_id": str(content_obj.id)}

        # Production Engine Check
        if not entity_found:
            stmt_prod = select(ProductionRequest).where(
                ProductionRequest.idempotency_key == correlation_key
            )
            prod_obj = (await session.execute(stmt_prod)).scalar_one_or_none()
            if prod_obj:
                entity_found = True
                subsystem_details = {"subsystem": "production", "entity_id": str(prod_obj.id)}

        # Publisher Check
        if not entity_found:
            stmt_pub = select(PublishAttempt).where(
                PublishAttempt.idempotency_key == correlation_key
            )
            pub_obj = (await session.execute(stmt_pub)).scalar_one_or_none()
            if pub_obj:
                entity_found = True
                subsystem_details = {"subsystem": "publisher", "entity_id": str(pub_obj.id)}

        # Scheduler Check
        if not entity_found:
            stmt_sched = select(ScheduleDecision).where(
                ScheduleDecision.idempotency_key == correlation_key
            )
            sched_obj = (await session.execute(stmt_sched)).scalar_one_or_none()
            if sched_obj:
                entity_found = True
                subsystem_details = {"subsystem": "scheduler", "entity_id": str(sched_obj.id)}

        # 2. Determine Outcome Status
        time_res = await session.execute(text("SELECT NOW()"))
        db_now = time_res.scalar()

        if entity_found:
            status = ActionAttemptOutcomeStatus.RECONCILED_CONFIRMED
        else:
            status = ActionAttemptOutcomeStatus.RECONCILED_ABSENT

        # Find existing outcomes count for sequence
        stmt_count = select(AutonomyActionAttemptOutcome).where(
            AutonomyActionAttemptOutcome.action_attempt_id == action_attempt_id
        )
        existing_outcomes = (await session.execute(stmt_count)).scalars().all()
        next_seq = len(existing_outcomes) + 1

        dedupe_key = hashlib.sha256(
            f"outcome:{action_attempt_id}:{next_seq}:{status.value}".encode()
        ).hexdigest()

        outcome = AutonomyActionAttemptOutcome(
            id=uuid4(),
            action_attempt_id=action_attempt_id,
            outcome_sequence=next_seq,
            status=status.value,
            worker_id=effective_worker,
            claim_generation=1,
            subsystem_response=subsystem_details,
            outcome_dedupe_key=dedupe_key,
            occurred_at=db_now,
        )
        session.add(outcome)

        # 3. Resolve Associated Budget Reservation
        stmt_res = select(AutonomyBudgetReservation).where(
            AutonomyBudgetReservation.action_plan_id == plan.id,
            AutonomyBudgetReservation.status.in_(
                [
                    BudgetReservationStatus.RESERVED.value,
                    BudgetReservationStatus.RECONCILIATION_REQUIRED.value,
                ]
            ),
        )
        budget_res = (await session.execute(stmt_res)).scalar_one_or_none()
        if budget_res:
            if status == ActionAttemptOutcomeStatus.RECONCILED_CONFIRMED:
                await AutonomyBudgetService.capture_reservation(
                    session=session,
                    reservation_id=budget_res.id,
                    captured_amount=plan.estimated_cost_usd,
                    provider_cost_reference=f"reconciled:{subsystem_details.get('entity_id')}",
                )
            else:
                await AutonomyBudgetService.release_reservation(
                    session=session,
                    reservation_id=budget_res.id,
                    release_reason="Reconciliation verified action absent from target subsystem.",
                )

        await session.commit()
        return outcome
