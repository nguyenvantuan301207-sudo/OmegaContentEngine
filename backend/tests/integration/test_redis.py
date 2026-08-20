"""Integration tests for Redis connectivity."""

from __future__ import annotations

import pytest

from tests.conftest import is_docker


@pytest.mark.skipif(not is_docker(), reason="Requires Docker services")
class TestRedis:
    """Test Redis connectivity."""

    @pytest.mark.asyncio
    async def test_ping(self) -> None:
        """Redis should respond to ping."""
        from omega.infrastructure.redis import redis_client

        result = await redis_client.ping()
        assert result is True

    @pytest.mark.asyncio
    async def test_set_get(self) -> None:
        """Redis should store and retrieve values."""
        from omega.infrastructure.redis import redis_client

        await redis_client.set("omega:test:key", "test_value")
        value = await redis_client.get("omega:test:key")
        assert value == "test_value"
        await redis_client.delete("omega:test:key")
