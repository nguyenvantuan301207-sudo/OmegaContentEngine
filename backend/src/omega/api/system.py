"""System endpoints — deep readiness check and application info.

GET /api/v1/system/status — deep health check (PostgreSQL, Redis, Celery worker)
GET /api/v1/system/info   — application metadata (name, version, environment)
"""

from __future__ import annotations

import time

from fastapi import APIRouter

from omega.config import get_settings
from omega.logging import get_logger

router = APIRouter(prefix="/api/v1/system", tags=["system"])
logger = get_logger(service="omega-api")
settings = get_settings()


async def _check_postgres() -> dict:
    """Check PostgreSQL connectivity."""
    from sqlalchemy import text

    from omega.infrastructure.database import async_engine

    start = time.monotonic()
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "healthy", "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        logger.warning("PostgreSQL health check failed", error=str(exc))
        return {"status": "unhealthy", "latency_ms": latency_ms}


async def _check_redis() -> dict:
    """Check Redis connectivity."""
    from omega.infrastructure.redis import redis_client

    start = time.monotonic()
    try:
        await redis_client.ping()
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"status": "healthy", "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        logger.warning("Redis health check failed", error=str(exc))
        return {"status": "unhealthy", "latency_ms": latency_ms}


async def _check_worker() -> dict:
    """Check Celery worker responsiveness.

    Uses celery inspect ping with a bounded timeout.
    Verifies actual worker responsiveness, not merely container process existence.
    """
    import asyncio

    from omega.infrastructure.celery_app import celery_app

    start = time.monotonic()
    try:
        # Run the synchronous Celery inspect in a thread to avoid blocking
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: celery_app.control.inspect(timeout=settings.worker_health_timeout).ping(),
            ),
            timeout=settings.worker_health_timeout + 1,
        )
        latency_ms = round((time.monotonic() - start) * 1000, 1)

        if result:
            return {"status": "healthy", "latency_ms": latency_ms}
        return {"status": "unhealthy", "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        logger.warning("Worker health check failed", error=str(exc))
        return {"status": "unhealthy", "latency_ms": latency_ms}


@router.get("/status")
async def system_status() -> dict:
    """Deep readiness check — verifies PostgreSQL, Redis, and Celery worker."""
    checks = {
        "postgres": await _check_postgres(),
        "redis": await _check_redis(),
        "worker": await _check_worker(),
    }

    all_healthy = all(c["status"] == "healthy" for c in checks.values())
    return {
        "status": "healthy" if all_healthy else "degraded",
        "checks": checks,
    }


@router.get("/info")
async def system_info() -> dict:
    """Application metadata — lightweight, no dependency checks."""
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
    }
