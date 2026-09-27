"""022 - Add worker lease and heartbeat tracking to production render jobs.

Revision ID: 022
Revises: 021
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "production_render_jobs",
        sa.Column("lease_owner_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "production_render_jobs",
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "production_render_jobs",
        sa.Column(
            "fencing_token",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "production_render_jobs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "production_render_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_production_render_jobs_lease",
        "production_render_jobs",
        ["state", "lease_expires_at"],
        postgresql_where=sa.text("state = 'RUNNING' AND lease_expires_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_production_render_jobs_lease", table_name="production_render_jobs")
    op.drop_column("production_render_jobs", "lease_expires_at")
    op.drop_column("production_render_jobs", "heartbeat_at")
    op.drop_column("production_render_jobs", "fencing_token")
    op.drop_column("production_render_jobs", "lease_token")
    op.drop_column("production_render_jobs", "lease_owner_id")
