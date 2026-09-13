"""Shared test fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from omega.config import Settings

_RUNTIME_DATABASE_URL: str | None = None
_TEST_DATABASE_URL: str | None = None


def _database_identity(raw_url: str) -> tuple[str, str, int, str]:
    """Return a credential-free identity suitable for strict URL comparison."""
    url = make_url(raw_url)
    backend = url.get_backend_name().lower()
    host = (url.host or "").lower()
    port = url.port or (5432 if backend == "postgresql" else 0)
    database = (url.database or "").strip().lower()
    return backend, host, port, database


def _validate_test_database_url(runtime_url: str, test_url: str | None) -> str:
    """Validate and return a positively identified, non-runtime test DB URL."""
    if not test_url:
        raise pytest.UsageError(
            "TEST_DATABASE_URL is required when collecting integration tests; "
            "no runtime-database fallback is allowed."
        )

    try:
        runtime_identity = _database_identity(runtime_url)
        test_identity = _database_identity(test_url)
    except Exception as exc:
        raise pytest.UsageError("Runtime or test database URL is invalid.") from exc

    if test_identity == runtime_identity:
        raise pytest.UsageError("TEST_DATABASE_URL resolves to the runtime database.")

    test_database = test_identity[3]
    if test_database == "omega":
        raise pytest.UsageError("The runtime database name 'omega' is forbidden for tests.")
    if "test" not in test_database:
        raise pytest.UsageError(
            "TEST_DATABASE_URL database name must be positively identifiable as test-only."
        )
    return test_url


def _sync_url(async_url: str) -> str:
    """Derive the sync SQLAlchemy URL without logging credentials."""
    url: URL = make_url(async_url)
    if url.get_backend_name() != "postgresql":
        raise pytest.UsageError("Publisher integration tests require PostgreSQL.")
    return url.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False)


def _integration_tests_requested(config: pytest.Config) -> bool:
    """Return whether this pytest invocation can collect integration tests."""
    raw_targets = [str(arg) for arg in getattr(config, "args", ())]
    if not raw_targets:
        return True
    normalized = [str(Path(target)).replace("\\", "/").lower() for target in raw_targets]
    return not all("tests/unit" in target for target in normalized)


def pytest_configure(config: pytest.Config) -> None:
    """Fail closed before integration modules can import runtime DB globals."""
    global _RUNTIME_DATABASE_URL, _TEST_DATABASE_URL

    integration_requested = _integration_tests_requested(config)
    supplied_test_url = os.environ.get("TEST_DATABASE_URL")
    if not integration_requested and not supplied_test_url:
        return

    runtime_url = Settings().database_url
    test_url = _validate_test_database_url(runtime_url, supplied_test_url)

    _RUNTIME_DATABASE_URL = runtime_url
    _TEST_DATABASE_URL = test_url
    os.environ["DATABASE_URL"] = test_url
    os.environ["DATABASE_URL_SYNC"] = _sync_url(test_url)
    os.environ["ENVIRONMENT"] = "test"


async def _truncate_test_database(engine) -> None:
    """Deterministically remove committed fixtures from the dedicated test database."""
    async with engine.begin() as connection:
        table_names = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_table_names(schema="public")
        )
        fixture_tables = [name for name in table_names if name != "alembic_version"]
        if not fixture_tables:
            return
        preparer = connection.dialect.identifier_preparer
        qualified = ", ".join(
            f"public.{preparer.quote_identifier(table_name)}" for table_name in fixture_tables
        )
        await connection.execute(text(f"TRUNCATE TABLE {qualified} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a dedicated test DB session with deterministic committed-data cleanup."""
    if _TEST_DATABASE_URL is None or _RUNTIME_DATABASE_URL is None:
        raise pytest.UsageError("Isolated test database configuration was not initialized.")
    _validate_test_database_url(_RUNTIME_DATABASE_URL, _TEST_DATABASE_URL)

    from omega.infrastructure import database as application_database

    if _database_identity(str(application_database.async_engine.url)) != _database_identity(
        _TEST_DATABASE_URL
    ):
        raise pytest.UsageError("Application AsyncSessionLocal is not bound to the test database.")

    engine = create_async_engine(_TEST_DATABASE_URL, poolclass=NullPool)
    try:
        await _truncate_test_database(engine)
        session = AsyncSession(bind=engine, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            try:
                await _truncate_test_database(engine)
            except Exception as exc:
                raise RuntimeError("Dedicated test database cleanup failed.") from exc
    finally:
        await engine.dispose()


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
