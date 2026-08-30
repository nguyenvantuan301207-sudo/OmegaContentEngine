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


@pytest.fixture
def publisher_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject deterministic test-only Publisher secrets into environment."""
    monkeypatch.setenv(
        "OMEGA_SECRET_ENCRYPTION_KEY",
        "MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE=",
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI",
        "http://localhost:8000/api/v1/publisher/accounts/youtube/callback",
    )
    import omega.infrastructure.vault as vault_module

    monkeypatch.setattr(vault_module, "_default_vault", None)
