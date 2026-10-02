"""Async SQLAlchemy database engine and session (asyncpg).

FastAPI application uses async_engine with connection pool.
Celery worker tasks using asyncio.run use AsyncWorkerSessionLocal (NullPool)
to avoid sharing connections across short-lived event loops.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from omega.config import get_settings

from typing import Any

settings = get_settings()

is_postgres = "postgresql" in settings.database_url

async_engine_kwargs: dict[str, Any] = {
    "echo": False,
    "pool_pre_ping": True,
}

if is_postgres:
    async_engine_kwargs.update(
        {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_timeout": settings.db_pool_timeout,
            "pool_recycle": settings.db_pool_recycle,
            "connect_args": {
                "server_settings": {
                    "statement_timeout": str(settings.db_statement_timeout_ms),
                    "lock_timeout": str(settings.db_lock_timeout_ms),
                }
            },
        }
    )

async_engine = create_async_engine(
    settings.database_url,
    **async_engine_kwargs,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

worker_engine_kwargs: dict[str, Any] = {
    "echo": False,
    "poolclass": NullPool,
}

if is_postgres:
    worker_engine_kwargs["connect_args"] = {
        "server_settings": {
            "statement_timeout": str(settings.db_statement_timeout_ms),
            "lock_timeout": str(settings.db_lock_timeout_ms),
        }
    }

worker_async_engine = create_async_engine(
    settings.database_url,
    **worker_engine_kwargs,
)

AsyncWorkerSessionLocal = async_sessionmaker(
    bind=worker_async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session."""
    async with AsyncSessionLocal() as session:
        yield session
