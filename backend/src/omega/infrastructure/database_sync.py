"""Synchronous SQLAlchemy database engine and session (psycopg2).

Used by Celery workers ONLY. The FastAPI application uses database.py (async).

Design decision: Celery's prefork/thread concurrency model does not provide
a running event loop. Using async SQLAlchemy inside Celery tasks would require
manually creating event loops, which is fragile and error-prone. A clean
synchronous engine with psycopg2 is the safest approach.

See docs/decisions/001-foundation-architecture.md for full rationale.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from omega.config import get_settings

settings = get_settings()

sync_engine = create_engine(
    settings.database_url_sync,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    class_=Session,
    expire_on_commit=False,
)


def get_sync_session() -> Generator[Session, None, None]:
    """Yield a synchronous database session."""
    session = SyncSessionLocal()
    try:
        yield session
    finally:
        session.close()
