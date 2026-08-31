"""PostgreSQL transaction-level advisory locking for OMEGA-013 concurrency."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from omega.domain.learning import compute_advisory_lock_key


async def acquire_advisory_lock(session: AsyncSession, namespace: str, *parts: str) -> int:
    """Acquire a deterministic transaction-level advisory lock in PostgreSQL.

    The lock is automatically released when the transaction ends (commit or rollback).
    """
    key = compute_advisory_lock_key(namespace, *parts)
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})
    return key
