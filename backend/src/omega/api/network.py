"""FastAPI router for OMEGA-009 Network Manager endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from omega.api.dependencies import get_db
from omega.application.network.circuit_breaker import RouteCircuitBreaker
from omega.application.network.preflight import NetworkPreflightService
from omega.application.network.probe_engine import ProbeEngine
from omega.domain.network import (
    NetworkCheckResponse,
    NetworkPolicyCreate,
    NetworkPolicyResponse,
    NetworkPolicyStatus,
    NetworkPreflightRequest,
    NetworkProbeResultData,
    NetworkProfileCreate,
    NetworkProfileResponse,
    NetworkRouteCreate,
    NetworkRouteResponse,
    RouteType,
    ServiceCategory,
    compute_config_checksum,
    normalize_destination,
)
from omega.infrastructure.database import AsyncSessionLocal
from omega.infrastructure.models import (
    NetworkCheck,
    NetworkPolicy,
    NetworkProfile,
    NetworkRoute,
)
from omega.logging import get_logger

logger = get_logger(service="omega-network-api")

router = APIRouter(prefix="/api/v1/network", tags=["network"])


# ── Profiles ──


@router.get("/profiles", response_model=list[NetworkProfileResponse])
async def list_network_profiles(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[NetworkProfileResponse]:
    """List all configured network profiles."""
    stmt = select(NetworkProfile).order_by(NetworkProfile.created_at.asc())
    res = await db.execute(stmt)
    profiles = res.scalars().all()
    return [NetworkProfileResponse.model_validate(p) for p in profiles]


@router.post(
    "/profiles", response_model=NetworkProfileResponse, status_code=status.HTTP_201_CREATED
)
async def create_network_profile(
    payload: NetworkProfileCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NetworkProfileResponse:
    """Create a new network profile."""
    # If is_default, clear other defaults
    if payload.is_default:
        existing_defaults = await db.execute(
            select(NetworkProfile).where(NetworkProfile.is_default.is_(True))
        )
        for p in existing_defaults.scalars().all():
            p.is_default = False

    now = datetime.now(UTC)
    profile = NetworkProfile(
        id=uuid4(),
        name=payload.name,
        description=payload.description,
        is_default=payload.is_default,
        created_at=now,
        updated_at=now,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return NetworkProfileResponse.model_validate(profile)


# ── Routes ──


@router.get("/routes", response_model=list[NetworkRouteResponse])
async def list_network_routes(
    db: Annotated[AsyncSession, Depends(get_db)],
    profile_id: UUID | None = None,
) -> list[NetworkRouteResponse]:
    """List configured network routes with redacted credentials."""
    stmt = select(NetworkRoute).order_by(NetworkRoute.priority_weight.desc())
    if profile_id:
        stmt = stmt.where(NetworkRoute.profile_id == profile_id)
    res = await db.execute(stmt)
    routes = res.scalars().all()
    return [
        NetworkRouteResponse(
            id=r.id,
            profile_id=r.profile_id,
            name=r.name,
            route_type=RouteType(r.route_type),
            endpoint_url=r.endpoint_url,
            credential_ref=r.credential_ref,
            allowed_service_categories=r.allowed_service_categories or [],
            tls_verify=r.tls_verify,
            connect_timeout_seconds=r.connect_timeout_seconds,
            read_timeout_seconds=r.read_timeout_seconds,
            max_retries=r.max_retries,
            priority_weight=r.priority_weight,
            is_enabled=r.is_enabled,
            config_version=r.config_version,
            config_checksum=r.config_checksum,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in routes
    ]


@router.post("/routes", response_model=NetworkRouteResponse, status_code=status.HTTP_201_CREATED)
async def create_network_route(
    payload: NetworkRouteCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NetworkRouteResponse:
    """Create an outbound network route."""
    # Verify profile exists
    prof_res = await db.execute(
        select(NetworkProfile).where(NetworkProfile.id == payload.profile_id)
    )
    if not prof_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"NetworkProfile '{payload.profile_id}' not found.",
        )

    now = datetime.now(UTC)
    config_dict = {
        "route_type": payload.route_type.value,
        "endpoint_url": payload.endpoint_url,
        "credential_ref": payload.credential_ref,
        "allowed_service_categories": [c.value for c in payload.allowed_service_categories],
        "tls_verify": payload.tls_verify,
        "connect_timeout_seconds": payload.connect_timeout_seconds,
        "read_timeout_seconds": payload.read_timeout_seconds,
        "max_retries": payload.max_retries,
    }
    checksum = compute_config_checksum(config_dict)

    route = NetworkRoute(
        id=uuid4(),
        profile_id=payload.profile_id,
        name=payload.name,
        route_type=payload.route_type.value,
        endpoint_url=payload.endpoint_url,
        credential_ref=payload.credential_ref,
        allowed_service_categories=[c.value for c in payload.allowed_service_categories],
        tls_verify=payload.tls_verify,
        connect_timeout_seconds=payload.connect_timeout_seconds,
        read_timeout_seconds=payload.read_timeout_seconds,
        max_retries=payload.max_retries,
        priority_weight=payload.priority_weight,
        is_enabled=payload.is_enabled,
        config_version=1,
        config_checksum=checksum,
        created_at=now,
        updated_at=now,
    )
    db.add(route)
    await db.commit()
    await db.refresh(route)

    return NetworkRouteResponse(
        id=route.id,
        profile_id=route.profile_id,
        name=route.name,
        route_type=RouteType(route.route_type),
        endpoint_url=route.endpoint_url,
        credential_ref=route.credential_ref,
        allowed_service_categories=route.allowed_service_categories or [],
        tls_verify=route.tls_verify,
        connect_timeout_seconds=route.connect_timeout_seconds,
        read_timeout_seconds=route.read_timeout_seconds,
        max_retries=route.max_retries,
        priority_weight=route.priority_weight,
        is_enabled=route.is_enabled,
        config_version=route.config_version,
        config_checksum=route.config_checksum,
        created_at=route.created_at,
        updated_at=route.updated_at,
    )


@router.post("/routes/{route_id}/enable", response_model=NetworkRouteResponse)
async def enable_network_route(
    route_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NetworkRouteResponse:
    """Enable a network route."""
    res = await db.execute(
        select(NetworkRoute).where(NetworkRoute.id == route_id).with_for_update()
    )
    route = res.scalar_one_or_none()
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="NetworkRoute not found.")

    route.is_enabled = True
    route.config_version += 1
    route.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(route)
    return NetworkRouteResponse.model_validate(route)


@router.post("/routes/{route_id}/disable", response_model=NetworkRouteResponse)
async def disable_network_route(
    route_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NetworkRouteResponse:
    """Disable a network route."""
    res = await db.execute(
        select(NetworkRoute).where(NetworkRoute.id == route_id).with_for_update()
    )
    route = res.scalar_one_or_none()
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="NetworkRoute not found.")

    route.is_enabled = False
    route.config_version += 1
    route.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(route)
    return NetworkRouteResponse.model_validate(route)


# ── Preflight & Checks ──


@router.post("/preflight", response_model=NetworkCheckResponse)
async def execute_preflight(
    payload: NetworkPreflightRequest,
) -> NetworkCheckResponse:
    """Execute authoritative network preflight evaluation."""
    preflight_svc = NetworkPreflightService(session_factory=AsyncSessionLocal)
    check_resp, _ = await preflight_svc.preflight(payload)
    return check_resp


@router.get("/checks/{check_id}", response_model=NetworkCheckResponse)
async def get_network_check(
    check_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NetworkCheckResponse:
    """Retrieve full details and decision of a NetworkCheck."""
    stmt = (
        select(NetworkCheck)
        .where(NetworkCheck.id == check_id)
        .options(
            selectinload(NetworkCheck.decision),
            selectinload(NetworkCheck.probe_runs),
        )
    )
    res = await db.execute(stmt)
    check = res.scalar_one_or_none()
    if not check:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="NetworkCheck not found.")

    preflight_svc = NetworkPreflightService(session_factory=AsyncSessionLocal)
    return preflight_svc._build_check_response(check)


# ── Health & Manual Probes ──


@router.get("/routes/{route_id}/health")
async def get_route_health(
    route_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    service_category: ServiceCategory = ServiceCategory.GENERAL_HTTP,
) -> dict[str, Any]:
    """Get real-time circuit state and health observation for a route."""
    circuit = await RouteCircuitBreaker.get_or_create_circuit_state(db, route_id, service_category)
    return {
        "route_id": str(route_id),
        "service_category": service_category.value,
        "circuit_state": circuit.state,
        "consecutive_failures": circuit.consecutive_failures,
        "half_open_successes": circuit.half_open_successes,
        "cooldown_until": circuit.cooldown_until,
        "last_failure_at": circuit.last_failure_at,
        "last_success_at": circuit.last_success_at,
    }


@router.post("/routes/{route_id}/probe", response_model=list[NetworkProbeResultData])
async def probe_route(
    route_id: UUID,
    target_destination: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[NetworkProbeResultData]:
    """Execute a manual probe against an authorized destination."""
    res = await db.execute(select(NetworkRoute).where(NetworkRoute.id == route_id))
    route = res.scalar_one_or_none()
    if not route:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="NetworkRoute not found.")

    try:
        canon_dest = normalize_destination(target_destination)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid target destination: {exc}",
        ) from exc

    results = await ProbeEngine.run_probes(
        canonical_dest=canon_dest,
        route_type=RouteType(route.route_type),
        proxy_endpoint_url=route.endpoint_url,
        connect_timeout=route.connect_timeout_seconds,
        read_timeout=route.read_timeout_seconds,
    )
    return results


# ── Policies ──


@router.get("/policies", response_model=list[NetworkPolicyResponse])
async def list_network_policies(
    db: Annotated[AsyncSession, Depends(get_db)],
    service_category: ServiceCategory | None = None,
) -> list[NetworkPolicyResponse]:
    """List network policies."""
    stmt = select(NetworkPolicy).order_by(NetworkPolicy.effective_at.desc())
    if service_category:
        stmt = stmt.where(NetworkPolicy.service_category == service_category.value)
    res = await db.execute(stmt)
    policies = res.scalars().all()
    return [NetworkPolicyResponse.model_validate(p) for p in policies]


@router.post("/policies", response_model=NetworkPolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_network_policy(
    payload: NetworkPolicyCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> NetworkPolicyResponse:
    """Create a new versioned NetworkPolicy."""
    checksum = compute_config_checksum(payload.policy_config)
    now = datetime.now(UTC)

    # Check if active policy should retire other active policies
    if payload.status == NetworkPolicyStatus.ACTIVE:
        old_actives = await db.execute(
            select(NetworkPolicy).where(
                NetworkPolicy.service_category == payload.service_category.value,
                NetworkPolicy.status == NetworkPolicyStatus.ACTIVE.value,
            )
        )
        for p in old_actives.scalars().all():
            p.status = NetworkPolicyStatus.RETIRED.value

    policy = NetworkPolicy(
        id=uuid4(),
        service_category=payload.service_category.value,
        version=payload.version,
        status=payload.status.value,
        effective_at=payload.effective_at,
        policy_config=payload.policy_config,
        checksum=checksum,
        created_at=now,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return NetworkPolicyResponse.model_validate(policy)
