"""Authorized Egress Permit issuance, validation, and client factory.

Guarantees that outbound network connections can only be instantiated with a valid,
unexpired NetworkEgressPermit bound to an authorized check and pinned route configuration.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.network import (
    NetworkEgressPermit,
    RouteType,
    ServiceCategory,
)
from omega.infrastructure.models import NetworkRoute
from omega.infrastructure.network.pinned_transport import PinnedAsyncHTTPTransport
from omega.logging import get_logger

logger = get_logger(service="omega-egress-permit")

DEFAULT_PERMIT_TTL_SECONDS = 60.0


class EgressSecurityError(PermissionError):
    """Raised when an outbound connection attempt violates egress permit constraints."""


class EgressPermitManager:
    """Manages authorized egress permits and client instantiation."""

    @staticmethod
    def issue_permit(
        network_check_id: UUID,
        route_id: UUID,
        route_config_version: int,
        canonical_destination: str,
        service_category: ServiceCategory,
        ttl_seconds: float = DEFAULT_PERMIT_TTL_SECONDS,
    ) -> NetworkEgressPermit:
        """Create a short-lived authorized egress permit."""
        now = datetime.now(UTC)
        return NetworkEgressPermit(
            permit_id=uuid4(),
            network_check_id=network_check_id,
            route_id=route_id,
            route_config_version=route_config_version,
            canonical_destination=canonical_destination,
            service_category=service_category,
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )

    @staticmethod
    async def get_authorized_client(
        session_factory: Callable[[], AsyncSession],
        permit: NetworkEgressPermit,
    ) -> httpx.AsyncClient:
        """Create an authorized httpx.AsyncClient bound to the permit destination and route.

        Raises EgressSecurityError if permit is expired, route is disabled, or config is stale.
        """
        now = datetime.now(UTC)
        if now >= permit.expires_at:
            raise EgressSecurityError(
                f"Egress permit '{permit.permit_id}' has expired (expired at {permit.expires_at})."
            )

        async with session_factory() as session:
            stmt = select(NetworkRoute).where(NetworkRoute.id == permit.route_id)
            res = await session.execute(stmt)
            route = res.scalar_one_or_none()

            if not route:
                raise EgressSecurityError(
                    f"Route '{permit.route_id}' referenced in permit not found."
                )

            if not route.is_enabled:
                raise EgressSecurityError(f"Route '{route.name}' is currently disabled.")

            if route.config_version != permit.route_config_version:
                raise EgressSecurityError(
                    f"Route '{route.name}' configuration changed (current v={route.config_version} != permit v={permit.route_config_version})."
                )

            # Resolve proxy credentials if applicable
            proxy_endpoint = route.endpoint_url
            if proxy_endpoint and route.credential_ref and route.credential_ref.startswith("ENV:"):
                env_var = route.credential_ref.removeprefix("ENV:")
                cred_val = os.environ.get(env_var)
                if cred_val and "@" not in proxy_endpoint:
                    # Inject credential securely into proxy URL for client
                    parsed_scheme, rest = proxy_endpoint.split("://", 1)
                    proxy_endpoint = f"{parsed_scheme}://{cred_val}@{rest}"

            transport = PinnedAsyncHTTPTransport(
                route_type=RouteType(route.route_type),
                proxy_endpoint_url=proxy_endpoint,
                connect_timeout=route.connect_timeout_seconds,
                read_timeout=route.read_timeout_seconds,
                tls_verify=route.tls_verify,
            )

            client = httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(
                    connect=route.connect_timeout_seconds,
                    read=route.read_timeout_seconds,
                ),
            )
            return client
