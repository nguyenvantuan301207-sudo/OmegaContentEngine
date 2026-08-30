"""Integration tests for OMEGA-009 Egress Permits and Guardian side-effect fence."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from omega.application.network.egress_permit import EgressPermitManager, EgressSecurityError
from omega.application.network.side_effect_fence import FenceViolationError, SideEffectFence
from omega.domain.network import NetworkEgressPermit, RouteType, ServiceCategory
from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import Mission, NetworkProfile, NetworkRoute


@pytest.mark.asyncio
async def test_egress_permit_expiration_and_client_rejection(db_session: AsyncSession):
    """Test that expired permits or mismatched destinations are rejected."""
    now = datetime.now(UTC)
    prof = NetworkProfile(id=uuid4(), name=f"Prof-{uuid4()}", created_at=now, updated_at=now)
    db_session.add(prof)
    await db_session.flush()

    route = NetworkRoute(
        id=uuid4(),
        profile_id=prof.id,
        name="Direct Permit Route",
        route_type=RouteType.DIRECT.value,
        allowed_service_categories=[ServiceCategory.GENERAL_HTTP.value],
        tls_verify=True,
        config_version=1,
        config_checksum="chk_perm",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)
    await db_session.commit()

    # Create expired permit
    expired_permit = NetworkEgressPermit(
        permit_id=uuid4(),
        network_check_id=uuid4(),
        route_id=route.id,
        route_config_version=1,
        canonical_destination="https://api.example.com:443",
        service_category=ServiceCategory.GENERAL_HTTP,
        issued_at=now - timedelta(minutes=5),
        expires_at=now - timedelta(minutes=1),  # Expired
    )

    with pytest.raises(EgressSecurityError, match="has expired"):
        await EgressPermitManager.get_authorized_client(
            session_factory=AsyncSessionLocal,
            permit=expired_permit,
        )


@pytest.mark.asyncio
async def test_pre_side_effect_fence_detects_guardian_epoch_mismatch(db_session: AsyncSession):
    """Test that SideEffectFence aborts if mission guardian_epoch changed before outbound call."""
    now = datetime.now(UTC)
    prof = NetworkProfile(id=uuid4(), name=f"Prof-{uuid4()}", created_at=now, updated_at=now)
    db_session.add(prof)
    await db_session.flush()

    route = NetworkRoute(
        id=uuid4(),
        profile_id=prof.id,
        name="Direct Fence Route",
        route_type=RouteType.DIRECT.value,
        allowed_service_categories=[ServiceCategory.GENERAL_HTTP.value],
        tls_verify=True,
        config_version=1,
        config_checksum="chk_fence",
        created_at=now,
        updated_at=now,
    )
    db_session.add(route)

    mission = Mission(
        id=uuid4(),
        title="Test Mission Fence",
        objective="Test Objective",
        state="RUNNING",
        guardian_epoch=2,  # Current epoch is 2
        created_at=now,
        updated_at=now,
    )
    db_session.add(mission)
    await db_session.commit()

    valid_permit = NetworkEgressPermit(
        permit_id=uuid4(),
        network_check_id=uuid4(),
        route_id=route.id,
        route_config_version=1,
        canonical_destination="https://api.example.com:443",
        service_category=ServiceCategory.GENERAL_HTTP,
        issued_at=now,
        expires_at=now + timedelta(seconds=60),
    )

    # Fence check with recorded epoch 1 (mismatch with mission.guardian_epoch=2)
    with pytest.raises(FenceViolationError, match="Guardian epoch changed from 1 to 2"):
        await SideEffectFence.revalidate_fence(
            session=db_session,
            mission_id=mission.id,
            recorded_guardian_epoch=1,
            permit=valid_permit,
        )

    # Fence check with matching epoch 2 passes
    passed = await SideEffectFence.revalidate_fence(
        session=db_session,
        mission_id=mission.id,
        recorded_guardian_epoch=2,
        permit=valid_permit,
    )
    assert passed is True
