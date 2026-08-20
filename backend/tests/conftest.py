"""Shared test fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


def is_docker() -> bool:
    """Check if we're running inside a Docker container."""
    return os.path.exists("/.dockerenv") or os.environ.get("ENVIRONMENT") == "development"


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set fallback test environment variables when not running in Docker container."""
    if not is_docker():
        if "DATABASE_URL" not in os.environ:
            monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
        if "DATABASE_URL_SYNC" not in os.environ:
            monkeypatch.setenv(
                "DATABASE_URL_SYNC", "postgresql+psycopg2://test:test@localhost:5432/test"
            )
        if "REDIS_URL" not in os.environ:
            monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        if "ENVIRONMENT" not in os.environ:
            monkeypatch.setenv("ENVIRONMENT", "test")
        if "CORS_ORIGINS" not in os.environ:
            monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session for integration tests."""
    from omega.infrastructure.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session
