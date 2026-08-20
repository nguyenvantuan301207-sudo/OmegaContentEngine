"""Alembic environment configuration.

Reads the database URL from OMEGA settings (environment variables)
rather than from alembic.ini, ensuring a single source of truth.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# Add src to path so we can import omega
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from omega.infrastructure.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_sync_url() -> str:
    """Get sync database URL from environment."""
    url = os.environ.get("DATABASE_URL_SYNC")
    if url:
        return url
    # Fallback: convert async URL to sync
    async_url = os.environ.get("DATABASE_URL", "")
    return async_url.replace("+asyncpg", "+psycopg2")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_sync_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(get_sync_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
