"""023 - Add dispatch generation and queue stall tracking to production render jobs.

Revision ID: 023
Revises: 022
Create Date: 2026-09-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "production_render_jobs",
        sa.Column(
            "dispatch_generation",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "production_render_jobs",
        sa.Column(
            "dispatch_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_production_render_jobs_dispatch_stall",
        "production_render_jobs",
        ["state", "dispatch_started_at"],
        postgresql_where=sa.text("state = 'QUEUED' AND dispatch_started_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_production_render_jobs_dispatch_stall",
        table_name="production_render_jobs",
    )
    op.drop_column("production_render_jobs", "dispatch_started_at")
    op.drop_column("production_render_jobs", "dispatch_generation")
