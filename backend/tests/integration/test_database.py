"""Integration tests for database connectivity."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import is_docker


@pytest.mark.skipif(not is_docker(), reason="Requires Docker services")
class TestDatabase:
    """Test PostgreSQL connectivity."""

    @pytest.mark.asyncio
    async def test_async_connection(self) -> None:
        """Async engine should connect and execute a query."""
        from omega.infrastructure.database import async_engine

        async with async_engine.connect() as conn:
            result = await conn.execute(text("SELECT 1 AS val"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == 1

    def test_sync_connection(self) -> None:
        """Sync engine should connect and execute a query."""
        from omega.infrastructure.database_sync import sync_engine

        with sync_engine.connect() as conn:
            result = conn.execute(text("SELECT 1 AS val"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == 1

    @pytest.mark.asyncio
    async def test_job_table_exists(self) -> None:
        """Jobs table should exist after migration."""
        from omega.infrastructure.database import async_engine

        async with async_engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'jobs')"
                )
            )
            row = result.fetchone()
            assert row is not None
            assert row[0] is True
