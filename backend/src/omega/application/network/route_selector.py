"""Deterministic route selection and reliability failover.

Selects active, service-compatible routes based on priority weights and circuit breaker health.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.network.circuit_breaker import RouteCircuitBreaker
from omega.domain.network import ServiceCategory
from omega.infrastructure.models import NetworkProfile, NetworkRoute
from omega.logging import get_logger

logger = get_logger(service="omega-route-selector")


class RouteSelector:
    """Selects the highest-priority healthy route for an egress operation."""

    @staticmethod
    async def select_route_for_service(
        session: AsyncSession,
        service_category: ServiceCategory,
        profile_id: UUID | None = None,
    ) -> NetworkRoute | None:
        """Find the optimal active route for a service category with reliability failover."""
        # 1. Resolve Profile
        if profile_id:
            prof_stmt = select(NetworkProfile).where(NetworkProfile.id == profile_id)
        else:
            prof_stmt = (
                select(NetworkProfile)
                .where(NetworkProfile.is_default.is_(True))
                .order_by(NetworkProfile.created_at.desc())
            )

        prof_res = await session.execute(prof_stmt)
        profile = prof_res.scalars().first()

        if not profile:
            # Fallback to any profile
            any_prof = await session.execute(
                select(NetworkProfile).order_by(NetworkProfile.created_at.desc())
            )
            profile = any_prof.scalars().first()
            if not profile:
                logger.warning("No NetworkProfile configured in system.")
                return None

        # 2. Query enabled routes for this profile
        route_stmt = (
            select(NetworkRoute)
            .where(
                NetworkRoute.profile_id == profile.id,
                NetworkRoute.is_enabled.is_(True),
            )
            .order_by(NetworkRoute.priority_weight.desc())
        )
        routes_res = await session.execute(route_stmt)
        candidate_routes = list(routes_res.scalars().all())

        # 3. Filter by service category compatibility
        compatible_routes: list[NetworkRoute] = []
        for r in candidate_routes:
            categories = r.allowed_service_categories or []
            if (
                service_category.value in categories
                or ServiceCategory.GENERAL_HTTP.value in categories
            ):
                compatible_routes.append(r)

        if not compatible_routes:
            logger.warning(
                "No compatible routes for service category",
                category=service_category.value,
                profile_id=str(profile.id),
            )
            return None

        # 4. Check circuit breaker state for each candidate in priority order
        for route in compatible_routes:
            state, allowed = await RouteCircuitBreaker.check_circuit_status(
                session=session,
                route_id=route.id,
                service_category=service_category,
            )
            if allowed:
                return route
            logger.info(
                "Failing over from route with open circuit",
                route_id=str(route.id),
                circuit_state=state.value,
                service_category=service_category.value,
            )

        return None
