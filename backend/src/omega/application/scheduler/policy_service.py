"""Schedule Policy lifecycle service.

Manages DRAFT -> ACTIVE -> RETIRED lifecycle with partial unique index
enforcement and immutability guarantees.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.scheduler import (
    SchedulePolicyConfig,
    SchedulePolicyStatus,
    compute_policy_checksum,
)
from omega.infrastructure.models import SchedulePolicy
from omega.logging import get_logger

logger = get_logger(service="omega-scheduler-policy")


class PolicyImmutabilityError(Exception):
    """Raised when attempting to modify an immutable (ACTIVE/RETIRED) policy."""


class PolicyActivationError(Exception):
    """Raised when policy activation fails."""


class SchedulePolicyService:
    """Manages schedule policy lifecycle with concurrency safety."""

    @staticmethod
    async def create_policy(
        session: AsyncSession,
        *,
        workload_category: str,
        version: str,
        policy_config: dict,
        activate: bool = False,
    ) -> SchedulePolicy:
        """Create a new schedule policy in DRAFT status."""
        # Validate config
        config = SchedulePolicyConfig(
            workload_category=workload_category,
            **policy_config,
        )
        checksum = compute_policy_checksum(config)

        policy = SchedulePolicy(
            id=uuid4(),
            workload_category=workload_category,
            version=version,
            status=SchedulePolicyStatus.DRAFT.value,
            policy_config=config.model_dump(mode="json"),
            checksum=checksum,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(policy)
        await session.flush()

        logger.info(
            "Schedule policy created",
            policy_id=str(policy.id),
            workload_category=workload_category,
            version=version,
            checksum=checksum,
        )

        if activate:
            await SchedulePolicyService.activate_policy(session, policy.id)
            await session.refresh(policy)

        return policy

    @staticmethod
    async def activate_policy(session: AsyncSession, policy_id: UUID) -> SchedulePolicy:
        """Activate a DRAFT policy, retiring any currently active policy for the same category.

        Uses FOR UPDATE to serialize concurrent activation attempts.
        The partial unique index on (workload_category) WHERE status = 'ACTIVE'
        provides the authoritative DB-level invariant.
        """
        # Lock the target policy
        result = await session.execute(
            select(SchedulePolicy).where(SchedulePolicy.id == policy_id).with_for_update()
        )
        target = result.scalar_one_or_none()
        if not target:
            raise PolicyActivationError(f"Policy {policy_id} not found")

        if target.status != SchedulePolicyStatus.DRAFT.value:
            raise PolicyActivationError(
                f"Cannot activate policy in {target.status} state. Only DRAFT policies can be activated."
            )

        # Retire any currently active policy for the same category
        current_active_result = await session.execute(
            select(SchedulePolicy)
            .where(
                SchedulePolicy.workload_category == target.workload_category,
                SchedulePolicy.status == SchedulePolicyStatus.ACTIVE.value,
            )
            .with_for_update()
        )
        current_active = current_active_result.scalar_one_or_none()
        if current_active:
            now = datetime.now(UTC)
            current_active.status = SchedulePolicyStatus.RETIRED.value
            current_active.retired_at = now
            current_active.updated_at = now
            await session.flush()
            logger.info(
                "Retired previous active policy",
                retired_policy_id=str(current_active.id),
                workload_category=current_active.workload_category,
                version=current_active.version,
            )

        # Activate the target policy
        now = datetime.now(UTC)
        target.status = SchedulePolicyStatus.ACTIVE.value
        target.effective_at = now
        target.updated_at = now
        await session.flush()

        logger.info(
            "Schedule policy activated",
            policy_id=str(target.id),
            workload_category=target.workload_category,
            version=target.version,
        )

        return target

    @staticmethod
    async def get_active_policy(
        session: AsyncSession, workload_category: str
    ) -> SchedulePolicy | None:
        """Get the current ACTIVE policy for a workload category."""
        result = await session.execute(
            select(SchedulePolicy).where(
                SchedulePolicy.workload_category == workload_category,
                SchedulePolicy.status == SchedulePolicyStatus.ACTIVE.value,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_policies(
        session: AsyncSession,
        workload_category: str | None = None,
    ) -> list[SchedulePolicy]:
        """List policies, optionally filtered by workload category."""
        stmt = select(SchedulePolicy).order_by(SchedulePolicy.created_at.desc())
        if workload_category:
            stmt = stmt.where(SchedulePolicy.workload_category == workload_category)
        result = await session.execute(stmt)
        return list(result.scalars().all())
