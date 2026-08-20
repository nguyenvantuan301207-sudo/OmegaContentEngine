"""Async Redis client.

Used by the FastAPI application for connection health checks
and any async Redis operations.
"""

from __future__ import annotations

from redis.asyncio import Redis

from omega.config import get_settings

settings = get_settings()

redis_client = Redis.from_url(
    settings.redis_url,
    decode_responses=True,
)


async def get_redis() -> Redis:
    """Return the async Redis client."""
    return redis_client
