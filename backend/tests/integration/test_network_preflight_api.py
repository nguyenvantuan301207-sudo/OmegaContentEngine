"""Integration tests for OMEGA-009 Network Preflight, Fencing, and API endpoints."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.network.preflight import NetworkPreflightService
from omega.domain.network import (
    NetworkAction,
    NetworkPreflightRequest,
    RouteType,
    ServiceCategory,
)
from omega.infrastructure.models import (
    NetworkProfile,
    NetworkRoute,
)


@pytest.mark.asyncio
async def test_db_check_constraint_enforces_mandatory_tls_verify(db_session: AsyncSession):
    """Test that the database check constraint strictly rejects tls_verify=False."""
    now = datetime.now(UTC)
    prof = NetworkProfile(id=uuid4(), name=f"Prof-{uuid4()}", created_at=now, updated_at=now)
    db_session.add(prof)
    await db_session.flush()

    invalid_route = NetworkRoute(
        id=uuid4(),
        profile_id=prof.id,
        name="Insecure Route",
        route_type="DIRECT",
        tls_verify=False,  # Violates ck_network_route_tls_verify_mandatory
        config_checksum="chk",
        created_at=now,
        updated_at=now,
    )
    db_session.add(invalid_route)

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_network_preflight_allow_flow_and_permit(db_session: AsyncSession):
    """Test standard preflight check yielding ALLOW and issuing an authorized egress permit."""
    now = datetime.now(UTC)
    prof = NetworkProfile(
        id=uuid4(),
        name=f"Production-{uuid4()}",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(prof)
    await db_session.flush()

    route = NetworkRoute(
        id=uuid4(),
        profile_id=prof.id,
        name="Direct Internet",
        route_type=RouteType.DIRECT.value,
        allowed_service_categories=[
            ServiceCategory.GENERAL_HTTP.value,
            ServiceCategory.YOUTUBE_API.value,
        ],
        tls_verify=True,
        config_version=1,
        config_checksum="chk1",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)
    await db_session.commit()

    # Instantiate preflight service with factory returning the test sessionmaker
    from omega.infrastructure.database import AsyncSessionLocal

    preflight_svc = NetworkPreflightService(session_factory=AsyncSessionLocal)

    req = NetworkPreflightRequest(
        destination_url="https://httpbin.org/get",
        service_category=ServiceCategory.GENERAL_HTTP,
        caller_key="test_client_1",
    )

    check_resp, permit = await preflight_svc.preflight(req)

    assert check_resp.status == "COMPLETED"
    assert check_resp.decision is not None
    assert check_resp.decision.action in (NetworkAction.ALLOW, NetworkAction.ALLOW_DEGRADED)
    assert permit is not None
    assert permit.canonical_destination.startswith("https://httpbin.org:443")
    assert permit.is_valid_for("https://httpbin.org/get") is True


@pytest.mark.asyncio
async def test_preflight_blocks_private_destination_ssrf(db_session: AsyncSession):
    """Test that preflight immediately blocks private/loopback destinations with BLOCKED_NETWORK."""
    now = datetime.now(UTC)
    prof = NetworkProfile(
        id=uuid4(),
        name=f"Prof-SSRF-{uuid4()}",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(prof)
    await db_session.flush()

    route = NetworkRoute(
        id=uuid4(),
        profile_id=prof.id,
        name="Direct Egress",
        route_type=RouteType.DIRECT.value,
        allowed_service_categories=[ServiceCategory.GENERAL_HTTP.value],
        tls_verify=True,
        config_version=1,
        config_checksum="chk_ssrf",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)
    await db_session.commit()

    from omega.infrastructure.database import AsyncSessionLocal

    preflight_svc = NetworkPreflightService(session_factory=AsyncSessionLocal)

    req = NetworkPreflightRequest(
        destination_url="http://169.254.169.254/latest/meta-data/",
        service_category=ServiceCategory.GENERAL_HTTP,
        caller_key="test_ssrf",
    )

    check_resp, permit = await preflight_svc.preflight(req)

    assert check_resp.status == "COMPLETED"
    assert check_resp.decision is not None
    assert check_resp.decision.action == NetworkAction.BLOCKED_NETWORK
    assert permit is None


@pytest.mark.asyncio
async def test_preflight_route_fencing_prevents_stale_allow(db_session: AsyncSession):
    """Test that mutating route configuration while check is evaluated prevents stale ALLOW."""
    now = datetime.now(UTC)
    prof = NetworkProfile(
        id=uuid4(),
        name=f"Prof-Fence-{uuid4()}",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(prof)
    await db_session.flush()

    route = NetworkRoute(
        id=uuid4(),
        profile_id=prof.id,
        name="Fenced Route",
        route_type=RouteType.DIRECT.value,
        allowed_service_categories=[ServiceCategory.GENERAL_HTTP.value],
        tls_verify=True,
        config_version=1,
        config_checksum="chk_fence",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)
    await db_session.commit()

    from omega.infrastructure.database import AsyncSessionLocal

    # Disable route directly in database to simulate route mutation / disablement
    route.is_enabled = False
    route.config_version = 2
    await db_session.commit()

    preflight_svc = NetworkPreflightService(session_factory=AsyncSessionLocal)

    req = NetworkPreflightRequest(
        destination_url="https://httpbin.org/status/200",
        service_category=ServiceCategory.GENERAL_HTTP,
        caller_key="test_fence_disabled",
    )

    check_resp, permit = await preflight_svc.preflight(req)
    assert check_resp.decision.action == NetworkAction.WAITING_NETWORK
    assert permit is None
