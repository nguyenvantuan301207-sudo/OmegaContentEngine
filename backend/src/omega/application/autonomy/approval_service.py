"""Durable approval engine, concurrency serialization, and frozen context validation for OMEGA-014."""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.autonomy.helpers import advisory_lock_key
from omega.domain.autonomy import ApprovalActorType, ApprovalDecisionValue
from omega.infrastructure.models import (
    AutonomyApprovalDecision,
    AutonomyApprovalRequest,
)


class ApprovalContextMismatchError(Exception):
    """Raised when an approval decision or execution attempt context has drifted."""

    pass


class AutonomyApprovalService:
    """Service managing immutable approval requests, serialized decisions, and context verification."""

    @classmethod
    async def create_request(
        cls,
        session: AsyncSession,
        loop_id: UUID,
        action_plan_id: UUID,
        semantic_action_key: str,
        action_checksum: str,
        precondition_fingerprint: str,
        guardian_context_checksum: str,
        guardian_epoch: int,
        dna_revision_id: UUID,
        policy_snapshot_id: UUID,
        mission_state: str,
        mission_execution_id: UUID | None = None,
    ) -> AutonomyApprovalRequest:
        """Create an immutable approval request snapshot."""
        dedupe_key = hashlib.sha256(
            f"appr_req:{loop_id}:{action_plan_id}:{action_checksum}".encode()
        ).hexdigest()

        stmt_existing = select(AutonomyApprovalRequest).where(
            AutonomyApprovalRequest.request_dedupe_key == dedupe_key
        )
        existing = (await session.execute(stmt_existing)).scalar_one_or_none()
        if existing:
            return existing

        time_res = await session.execute(text("SELECT NOW(), NOW() + INTERVAL '24 hours'"))
        db_now, expires_at = time_res.first()

        req = AutonomyApprovalRequest(
            id=uuid4(),
            loop_id=loop_id,
            action_plan_id=action_plan_id,
            semantic_action_key=semantic_action_key,
            action_checksum=action_checksum,
            precondition_fingerprint=precondition_fingerprint,
            guardian_context_checksum=guardian_context_checksum,
            guardian_epoch=guardian_epoch,
            dna_revision_id=dna_revision_id,
            policy_snapshot_id=policy_snapshot_id,
            mission_state=mission_state,
            mission_execution_id=mission_execution_id,
            requested_at=db_now,
            expires_at=expires_at,
            request_dedupe_key=dedupe_key,
        )
        session.add(req)
        await session.flush()
        return req

    @classmethod
    async def decide_request(
        cls,
        session: AsyncSession,
        approval_request_id: UUID,
        decision: ApprovalDecisionValue,
        actor_type: ApprovalActorType,
        reviewer_user_id: str | None,
        review_reason: str,
        current_guardian_context_checksum: str | None = None,
        current_precondition_fingerprint: str | None = None,
    ) -> AutonomyApprovalDecision:
        """Atomically record an approval decision under an advisory lock."""
        # 1. Advisory Lock to serialize concurrent reviewers or SYSTEM expiry
        lock_k = advisory_lock_key("autonomy_approval", str(approval_request_id))
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": lock_k},
        )

        # 2. Lock and load Request
        stmt_req = (
            select(AutonomyApprovalRequest)
            .where(AutonomyApprovalRequest.id == approval_request_id)
            .with_for_update()
        )
        req = (await session.execute(stmt_req)).scalar_one_or_none()
        if not req:
            raise ValueError(f"ApprovalRequest '{approval_request_id}' not found.")

        # 3. Check for existing terminal decision
        stmt_dec = (
            select(AutonomyApprovalDecision)
            .where(AutonomyApprovalDecision.approval_request_id == approval_request_id)
            .order_by(AutonomyApprovalDecision.decision_sequence.desc())
        )
        decisions = (await session.execute(stmt_dec)).scalars().all()
        if decisions:
            # Return existing terminal decision idempotently
            return decisions[0]

        # 4. Context Invalidation Check: If Guardian or Fingerprint changed before decision, invalidate
        final_decision = decision
        final_reason = review_reason

        if (
            current_guardian_context_checksum
            and current_guardian_context_checksum != req.guardian_context_checksum
        ):
            final_decision = ApprovalDecisionValue.INVALIDATED
            final_reason = (
                f"Invalidated: Guardian context drifted during review. Original: {req.guardian_context_checksum[:8]}..., "
                f"Current: {current_guardian_context_checksum[:8]}..."
            )
        elif (
            current_precondition_fingerprint
            and current_precondition_fingerprint != req.precondition_fingerprint
        ):
            final_decision = ApprovalDecisionValue.INVALIDATED
            final_reason = (
                f"Invalidated: Precondition fingerprint drifted during review. Original: {req.precondition_fingerprint[:8]}..., "
                f"Current: {current_precondition_fingerprint[:8]}..."
            )

        # 5. Insert Decision Record with exact frozen context
        time_res = await session.execute(text("SELECT NOW()"))
        db_now = time_res.scalar()

        dedupe_key = hashlib.sha256(
            f"appr_dec:{approval_request_id}:1:{final_decision.value}:{actor_type.value}:{reviewer_user_id or 'SYSTEM'}".encode()
        ).hexdigest()

        decision_rec = AutonomyApprovalDecision(
            id=uuid4(),
            approval_request_id=approval_request_id,
            decision_sequence=1,
            decision=final_decision.value,
            actor_type=actor_type.value,
            reviewer_user_id=reviewer_user_id,
            review_reason=final_reason,
            action_checksum=req.action_checksum,
            precondition_fingerprint=req.precondition_fingerprint,
            guardian_context_checksum=req.guardian_context_checksum,
            guardian_epoch=req.guardian_epoch,
            dna_revision_id=req.dna_revision_id,
            policy_snapshot_id=req.policy_snapshot_id,
            mission_state=req.mission_state,
            mission_execution_id=req.mission_execution_id,
            decided_at=db_now,
            decision_dedupe_key=dedupe_key,
        )
        session.add(decision_rec)
        await session.flush()
        return decision_rec

    @classmethod
    async def validate_approval_for_dispatch(
        cls,
        session: AsyncSession,
        approval_request_id: UUID,
        expected_action_checksum: str,
        current_precondition_fingerprint: str,
        current_guardian_context_checksum: str,
        current_guardian_epoch: int,
        current_dna_revision_id: UUID,
        current_mission_state: str,
    ) -> bool:
        """Verify that an existing approval decision authorizes dispatch under current context.

        Returns True if:
        - Latest decision is APPROVED.
        - All 6 runtime context items match the frozen context.
        """
        stmt_dec = (
            select(AutonomyApprovalDecision)
            .where(AutonomyApprovalDecision.approval_request_id == approval_request_id)
            .order_by(AutonomyApprovalDecision.decision_sequence.desc())
        )
        dec = (await session.execute(stmt_dec)).scalars().first()
        if not dec:
            return False

        if dec.decision != ApprovalDecisionValue.APPROVED.value:
            return False

        # Verify strict context invariance
        if dec.action_checksum != expected_action_checksum:
            return False
        if dec.precondition_fingerprint != current_precondition_fingerprint:
            return False
        if dec.guardian_context_checksum != current_guardian_context_checksum:
            return False
        if dec.guardian_epoch != current_guardian_epoch:
            return False
        if dec.dna_revision_id != current_dna_revision_id:
            return False
        return dec.mission_state == current_mission_state
