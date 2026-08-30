"""Atomic pre-side-effect revalidation fence.

Verifies Guardian epoch stability and NetworkEgressPermit validity immediately
before any external network operation is executed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.network import NetworkEgressPermit
from omega.infrastructure.models import Mission, NetworkRoute
from omega.logging import get_logger

logger = get_logger(service="omega-side-effect-fence")


class FenceViolationError(RuntimeError):
    """Raised when immediate pre-call revalidation detects an epoch or permit mismatch."""


class SideEffectFence:
    """Provides atomic revalidation immediately prior to external side-effect dispatch."""

    @staticmethod
    async def revalidate_fence(
        session: AsyncSession,
        mission_id: UUID | None,
        recorded_guardian_epoch: int,
        permit: NetworkEgressPermit,
    ) -> bool:
        """Verify mission epoch and permit validity before dispatching outbound side effect."""
        now = datetime.now(UTC)

        # 1. Revalidate Permit Expiration
        if now >= permit.expires_at:
            logger.warning(
                "Side-effect aborted: egress permit expired", permit_id=str(permit.permit_id)
            )
            raise FenceViolationError(
                f"Egress permit '{permit.permit_id}' expired at {permit.expires_at}."
            )

        # 2. Revalidate Route Configuration
        route_stmt = select(NetworkRoute).where(NetworkRoute.id == permit.route_id)
        route_res = await session.execute(route_stmt)
        route = route_res.scalar_one_or_none()
        if not route or not route.is_enabled:
            raise FenceViolationError(f"Route '{permit.route_id}' is disabled or not found.")

        if route.config_version != permit.route_config_version:
            raise FenceViolationError(
                f"Route configuration mutated mid-dispatch (current v={route.config_version} != permit v={permit.route_config_version})."
            )

        # 3. Revalidate Guardian Epoch on Mission if mission_id provided
        if mission_id:
            m_stmt = select(Mission.guardian_epoch).where(Mission.id == mission_id)
            m_res = await session.execute(m_stmt)
            current_epoch = m_res.scalar_one_or_none()
            if current_epoch is None:
                raise FenceViolationError(
                    f"Mission '{mission_id}' not found during fence revalidation."
                )

            if current_epoch != recorded_guardian_epoch:
                logger.warning(
                    "Side-effect aborted: Guardian epoch changed",
                    mission_id=str(mission_id),
                    recorded_epoch=recorded_guardian_epoch,
                    current_epoch=current_epoch,
                )
                raise FenceViolationError(
                    f"Guardian epoch changed from {recorded_guardian_epoch} to {current_epoch} (mission was paused, resumed, or failed)."
                )

        return True
