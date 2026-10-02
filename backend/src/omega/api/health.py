"""Health, Readiness, Metrics, and Operator Status endpoints (P20-C).

Provides:
- GET /health & GET /health/live: Lightweight process-liveness probes (no dependency checks).
- GET /health/ready: Feature-aware readiness evaluation (returns 200 READY or 503 DEGRADED).
- GET /metrics: Bounded Prometheus telemetry exposition (fails closed in production if unauthenticated).
- GET /ops/status: Comprehensive read-only operator diagnostic surface (fails closed in production if unauthenticated).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status

from omega.application.observability.metrics import (
    generate_prometheus_metrics,
    verify_observability_access,
)
from omega.application.observability.operator_status_service import collect_operator_status
from omega.application.observability.readiness_service import evaluate_system_readiness
from omega.config import get_settings

router = APIRouter(tags=["observability"])


@router.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Lightweight process liveness probe. No dependency checks."""
    return {"status": "ok"}


@router.get("/health/live", tags=["health"])
async def health_live() -> dict[str, str]:
    """Explicit process liveness probe for container orchestrators. No dependency checks."""
    return {"status": "ok"}


@router.get("/health/ready", tags=["health"])
async def health_ready(response: Response) -> dict[str, Any]:
    """Feature-aware readiness probe. Returns HTTP 200 when READY, HTTP 503 when DEGRADED."""
    payload, status_code = await evaluate_system_readiness()
    response.status_code = status_code
    return payload


@router.get("/metrics")
async def get_metrics(request: Request) -> Response:
    """Prometheus telemetry scrape endpoint.

    Fails closed (HTTP 403) in production if metrics_auth_token is missing or invalid.
    """
    settings = get_settings()
    if not verify_observability_access(request, settings.metrics_auth_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: invalid or missing observability authentication token",
        )

    metrics_bytes = await generate_prometheus_metrics()
    return Response(
        content=metrics_bytes,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/ops/status")
async def get_ops_status(request: Request) -> dict[str, Any]:
    """Comprehensive read-only operator diagnostics snapshot.

    Always returns HTTP 200 with structured diagnostics.
    Fails closed (HTTP 403) in production if operator_auth_token is missing or invalid.
    """
    settings = get_settings()
    if not verify_observability_access(request, settings.operator_auth_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: invalid or missing operator authentication token",
        )

    return await collect_operator_status()
