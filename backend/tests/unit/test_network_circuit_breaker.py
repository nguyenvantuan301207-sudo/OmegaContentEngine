"""Unit tests for OMEGA-009 authoritative persistent circuit breaker."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.network.circuit_breaker import RouteCircuitBreaker
from omega.domain.network import CircuitState, ServiceCategory
from omega.infrastructure.models import NetworkProfile, NetworkRoute


@pytest.mark.asyncio
async def test_circuit_breaker_closed_to_open_transition(db_session: AsyncSession):
    """Test that consecutive failures trip circuit from CLOSED to OPEN."""
    now = datetime.now(UTC)
    prof = NetworkProfile(id=uuid4(), name=f"Prof-{uuid4()}", created_at=now, updated_at=now)
    db_session.add(prof)
    await db_session.flush()

    route = NetworkRoute(
        id=uuid4(),
        profile_id=prof.id,
        name="Test Route",
        route_type="DIRECT",
        allowed_service_categories=["GENERAL_HTTP"],
        tls_verify=True,
        config_checksum="chk",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)
    await db_session.commit()

    # Record failures up to threshold (3)
    for _ in range(2):
        state = await RouteCircuitBreaker.record_probe_outcome(
            session=db_session,
            route_id=route.id,
            service_category=ServiceCategory.GENERAL_HTTP,
            is_success=False,
            failure_threshold=3,
        )
        assert state == CircuitState.CLOSED

    # 3rd failure trips to OPEN
    state = await RouteCircuitBreaker.record_probe_outcome(
        session=db_session,
        route_id=route.id,
        service_category=ServiceCategory.GENERAL_HTTP,
        is_success=False,
        failure_threshold=3,
    )
    assert state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_lease_and_reset(db_session: AsyncSession):
    """Test half-open lease acquisition, crash recovery, and reset to CLOSED."""
    now = datetime.now(UTC)
    prof = NetworkProfile(id=uuid4(), name=f"Prof-{uuid4()}", created_at=now, updated_at=now)
    db_session.add(prof)
    await db_session.flush()

    route = NetworkRoute(
        id=uuid4(),
        profile_id=prof.id,
        name="Test Route 2",
        route_type="DIRECT",
        allowed_service_categories=["GENERAL_HTTP"],
        tls_verify=True,
        config_checksum="chk",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)
    await db_session.commit()

    # Trip to OPEN with 1s cooldown
    await RouteCircuitBreaker.record_probe_outcome(
        session=db_session,
        route_id=route.id,
        service_category=ServiceCategory.GENERAL_HTTP,
        is_success=False,
        failure_threshold=1,
        cooldown_seconds=1,
    )

    # Immediately after tripping, execution is blocked
    _, allowed = await RouteCircuitBreaker.check_circuit_status(
        session=db_session,
        route_id=route.id,
        service_category=ServiceCategory.GENERAL_HTTP,
    )
    assert allowed is False

    # Simulate cooldown expiration by setting cooldown_until to past
    circuit = await RouteCircuitBreaker.get_or_create_circuit_state(
        session=db_session,
        route_id=route.id,
        service_category=ServiceCategory.GENERAL_HTTP,
    )
    circuit.cooldown_until = now - timedelta(seconds=5)
    await db_session.commit()

    # Worker 1 acquires HALF_OPEN lease
    token1 = await RouteCircuitBreaker.try_acquire_half_open_lease(
        session=db_session,
        route_id=route.id,
        service_category=ServiceCategory.GENERAL_HTTP,
        lease_seconds=2.0,
    )
    assert token1 is not None

    # Concurrent worker cannot acquire active lease
    token2 = await RouteCircuitBreaker.try_acquire_half_open_lease(
        session=db_session,
        route_id=route.id,
        service_category=ServiceCategory.GENERAL_HTTP,
        lease_seconds=2.0,
    )
    assert token2 is None

    # Simulate Worker 1 crash by expiring the lease
    circuit.half_open_lease_expires_at = now - timedelta(seconds=1)
    await db_session.commit()

    # Worker 3 can now reclaim the expired lease
    token3 = await RouteCircuitBreaker.try_acquire_half_open_lease(
        session=db_session,
        route_id=route.id,
        service_category=ServiceCategory.GENERAL_HTTP,
        lease_seconds=2.0,
    )
    assert token3 is not None

    # Record 2 successful trial probes -> resets to CLOSED
    await RouteCircuitBreaker.record_probe_outcome(
        session=db_session,
        route_id=route.id,
        service_category=ServiceCategory.GENERAL_HTTP,
        is_success=True,
        half_open_success_threshold=2,
    )
    state = await RouteCircuitBreaker.record_probe_outcome(
        session=db_session,
        route_id=route.id,
        service_category=ServiceCategory.GENERAL_HTTP,
        is_success=True,
        half_open_success_threshold=2,
    )
    assert state == CircuitState.CLOSED
