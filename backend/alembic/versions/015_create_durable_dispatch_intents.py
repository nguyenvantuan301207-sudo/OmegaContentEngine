"""015 — Create generic durable Celery dispatch intents.

Revision ID: 015
Revises: 014
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "durable_dispatch_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column(
            "args",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("purpose", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("missions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "mission_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mission_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "mission_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "production_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "render_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_render_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_durable_dispatch_intents_idempotency_key"
        ),
        sa.CheckConstraint(
            "state IN ('PENDING', 'CLAIMED', 'RETRY', 'SENT', 'DEAD_LETTER')",
            name="ck_durable_dispatch_intents_state",
        ),
    )
    op.create_index(
        "idx_durable_dispatch_intents_relay",
        "durable_dispatch_intents",
        ["state", "next_attempt_at", "created_at"],
    )
    op.create_index(
        "idx_durable_dispatch_intents_claim_recovery",
        "durable_dispatch_intents",
        ["state", "claimed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_durable_dispatch_intents_claim_recovery",
        table_name="durable_dispatch_intents",
    )
    op.drop_index(
        "idx_durable_dispatch_intents_relay",
        table_name="durable_dispatch_intents",
    )
    op.drop_table("durable_dispatch_intents")
