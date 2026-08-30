"""Authoritative persistent circuit breaker with crash-safe half-open lease.

Manages route/service state transitions in PostgreSQL with optimistic locking
and immutable audit log transitions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.network import CircuitState, ServiceCategory
from omega.infrastructure.models import NetworkCircuitState, NetworkCircuitTransition
from omega.logging import get_logger

logger = get_logger(service="omega-circuit-breaker")

DEFAULT_HALF_OPEN_LEASE_SECONDS = 10.0


class RouteCircuitBreaker:
    """Manages authoritative circuit breaker state transitions in PostgreSQL."""

    @staticmethod
    async def get_or_create_circuit_state(
        session: AsyncSession,
        route_id: UUID,
        service_category: ServiceCategory,
    ) -> NetworkCircuitState:
        """Get or initialize the circuit state record for a route/service pair."""
        stmt = (
            select(NetworkCircuitState)
            .where(
                NetworkCircuitState.route_id == route_id,
                NetworkCircuitState.service_category == service_category.value,
            )
            .with_for_update()
        )
        res = await session.execute(stmt)
        circuit = res.scalar_one_or_none()

        if not circuit:
            now = datetime.now(UTC)
            circuit = NetworkCircuitState(
                route_id=route_id,
                service_category=service_category.value,
                state=CircuitState.CLOSED.value,
                consecutive_failures=0,
                half_open_successes=0,
                version=1,
                updated_at=now,
            )
            session.add(circuit)
            await session.flush()

        return circuit

    @staticmethod
    async def check_circuit_status(
        session: AsyncSession,
        route_id: UUID,
        service_category: ServiceCategory,
    ) -> tuple[CircuitState, bool]:
        """Check current circuit state and whether a probe execution is permitted.

        Returns (CircuitState, is_execution_allowed).
        """
        circuit = await RouteCircuitBreaker.get_or_create_circuit_state(
            session, route_id, service_category
        )
        now = datetime.now(UTC)
        current_state = CircuitState(circuit.state)

        if current_state == CircuitState.CLOSED:
            return CircuitState.CLOSED, True

        if current_state == CircuitState.OPEN:
            if circuit.cooldown_until and now >= circuit.cooldown_until:
                # Eligible for HALF_OPEN probe lease
                return CircuitState.HALF_OPEN, True
            return CircuitState.OPEN, False

        if current_state == CircuitState.HALF_OPEN:
            # Check if lease expired or available
            if (
                circuit.half_open_lease_expires_at is None
                or now >= circuit.half_open_lease_expires_at
            ):
                return CircuitState.HALF_OPEN, True
            return CircuitState.HALF_OPEN, False

        return CircuitState.CLOSED, True

    @staticmethod
    async def try_acquire_half_open_lease(
        session: AsyncSession,
        route_id: UUID,
        service_category: ServiceCategory,
        lease_seconds: float = DEFAULT_HALF_OPEN_LEASE_SECONDS,
    ) -> UUID | None:
        """Atomically acquire or reclaim the trial probe lease in HALF_OPEN state."""
        circuit = await RouteCircuitBreaker.get_or_create_circuit_state(
            session, route_id, service_category
        )
        now = datetime.now(UTC)
        current_state = CircuitState(circuit.state)

        is_eligible = (
            current_state == CircuitState.OPEN
            and circuit.cooldown_until
            and now >= circuit.cooldown_until
        ) or (
            current_state == CircuitState.HALF_OPEN
            and (
                circuit.half_open_lease_expires_at is None
                or now >= circuit.half_open_lease_expires_at
            )
        )

        if not is_eligible:
            return None

        token = uuid4()
        prev_state = circuit.state
        circuit.state = CircuitState.HALF_OPEN.value
        circuit.half_open_lease_token = token
        circuit.half_open_lease_expires_at = now + timedelta(seconds=lease_seconds)
        circuit.version += 1
        circuit.updated_at = now

        if prev_state != CircuitState.HALF_OPEN.value:
            session.add(
                NetworkCircuitTransition(
                    id=uuid4(),
                    route_id=route_id,
                    service_category=service_category.value,
                    from_state=prev_state,
                    to_state=CircuitState.HALF_OPEN.value,
                    trigger_reason="Cooldown expired; entering HALF_OPEN trial state.",
                    created_at=now,
                )
            )

        await session.flush()
        return token

    @staticmethod
    async def record_probe_outcome(
        session: AsyncSession,
        route_id: UUID,
        service_category: ServiceCategory,
        is_success: bool,
        failure_threshold: int = 5,
        cooldown_seconds: int = 30,
        half_open_success_threshold: int = 2,
        trigger_reason: str = "",
    ) -> CircuitState:
        """Record probe outcome and execute atomic circuit state transition."""
        circuit = await RouteCircuitBreaker.get_or_create_circuit_state(
            session, route_id, service_category
        )
        now = datetime.now(UTC)
        current_state = CircuitState(circuit.state)
        prev_state = circuit.state

        if is_success:
            circuit.last_success_at = now
            if current_state == CircuitState.HALF_OPEN:
                circuit.half_open_successes += 1
                if circuit.half_open_successes >= half_open_success_threshold:
                    # Reset to CLOSED
                    circuit.state = CircuitState.CLOSED.value
                    circuit.consecutive_failures = 0
                    circuit.half_open_successes = 0
                    circuit.half_open_lease_token = None
                    circuit.half_open_lease_expires_at = None
                    circuit.cooldown_until = None
                    circuit.version += 1
                    circuit.updated_at = now

                    session.add(
                        NetworkCircuitTransition(
                            id=uuid4(),
                            route_id=route_id,
                            service_category=service_category.value,
                            from_state=prev_state,
                            to_state=CircuitState.CLOSED.value,
                            trigger_reason="Half-open trial success threshold satisfied; circuit reset to CLOSED.",
                            created_at=now,
                        )
                    )
            elif current_state == CircuitState.CLOSED:
                circuit.consecutive_failures = 0
                circuit.updated_at = now

        else:
            circuit.last_failure_at = now
            circuit.consecutive_failures += 1

            if current_state == CircuitState.HALF_OPEN:
                # Failure in HALF_OPEN immediately trips back to OPEN
                circuit.state = CircuitState.OPEN.value
                circuit.opened_at = now
                circuit.cooldown_until = now + timedelta(seconds=cooldown_seconds)
                circuit.half_open_successes = 0
                circuit.half_open_lease_token = None
                circuit.half_open_lease_expires_at = None
                circuit.version += 1
                circuit.updated_at = now

                session.add(
                    NetworkCircuitTransition(
                        id=uuid4(),
                        route_id=route_id,
                        service_category=service_category.value,
                        from_state=prev_state,
                        to_state=CircuitState.OPEN.value,
                        trigger_reason=f"Trial probe failed in HALF_OPEN: {trigger_reason}",
                        created_at=now,
                    )
                )

            elif current_state == CircuitState.CLOSED:
                if circuit.consecutive_failures >= failure_threshold:
                    circuit.state = CircuitState.OPEN.value
                    circuit.opened_at = now
                    circuit.cooldown_until = now + timedelta(seconds=cooldown_seconds)
                    circuit.version += 1
                    circuit.updated_at = now

                    session.add(
                        NetworkCircuitTransition(
                            id=uuid4(),
                            route_id=route_id,
                            service_category=service_category.value,
                            from_state=prev_state,
                            to_state=CircuitState.OPEN.value,
                            trigger_reason=f"Failure threshold ({failure_threshold}) exceeded: {trigger_reason}",
                            created_at=now,
                        )
                    )

        await session.flush()
        return CircuitState(circuit.state)
