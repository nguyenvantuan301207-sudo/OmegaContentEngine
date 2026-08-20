"""FastAPI dependencies.

Shared dependency injection for database sessions.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from omega.infrastructure.database import AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session for request-scoped use."""
    async with AsyncSessionLocal() as session:
        yield session
