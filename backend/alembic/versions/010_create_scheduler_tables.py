"""010 — Create Smart Scheduler tables and constraints.

Revision ID: 010
Revises: 009
Create Date: 2026-08-30

Creates five tables for the OMEGA-010 Smart Scheduler:
1. schedule_policies — Versioned scheduling policy configuration
2. schedule_decisions — Authoritative/audit scheduling decisions
3. schedule_reservations — Authoritative slot allocation state machine
4. schedule_state_transitions — Append-only reservation audit trail
5. scheduler_dispatch_outbox — Transactional outbox for crash-safe dispatch
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. schedule_policies ──
    op.create_table(
        "schedule_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workload_category", sa.String(50), nullable=False, index=True),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="DRAFT",
            index=True,
        ),
        sa.Column("policy_config", postgresql.JSON, nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "workload_category", "version", name="uq_schedule_policy_category_version"
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'RETIRED')",
            name="ck_schedule_policies_status",
        ),
    )

    # Partial unique index: at most one ACTIVE policy per workload_category
    op.create_index(
        "uix_schedule_policies_active_category",
        "schedule_policies",
        ["workload_category"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    # ── 2. schedule_decisions ──
    op.create_table(
        "schedule_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("target_type", sa.String(50), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("workload_category", sa.String(50), nullable=False),
        sa.Column("action", sa.String(50), nullable=False, index=True),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("schedule_policies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(50), nullable=False),
        sa.Column("policy_checksum", sa.String(64), nullable=False),
        sa.Column(
            "channel_dna_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("guardian_epoch", sa.Integer, nullable=False),
        sa.Column(
            "idempotency_key",
            sa.String(64),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("diagnostic_context", postgresql.JSON, nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── 3. schedule_reservations ──
    op.create_table(
        "schedule_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("schedule_decisions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("target_type", sa.String(50), nullable=False, index=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("workload_category", sa.String(50), nullable=False, index=True),
        sa.Column(
            "scheduled_start_at",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
        sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "state",
            sa.String(50),
            nullable=False,
            server_default="ACTIVE",
            index=True,
        ),
        sa.Column("priority_score", sa.Float, nullable=False, server_default="0.0"),
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("schedule_policies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(50), nullable=False),
        sa.Column("policy_checksum", sa.String(64), nullable=False),
        sa.Column(
            "channel_dna_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("guardian_epoch", sa.Integer, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("dispatching_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.CheckConstraint(
            "state IN ('ACTIVE', 'DISPATCHING', 'CONSUMED', 'RELEASED', 'EXPIRED', 'CANCELLED')",
            name="ck_schedule_reservations_state",
        ),
    )

    # Composite indexes for sweep and overlap queries
    op.create_index(
        "ix_schedule_reservations_dispatch_sweep",
        "schedule_reservations",
        ["state", "scheduled_start_at"],
    )
    op.create_index(
        "ix_schedule_reservations_channel_overlap",
        "schedule_reservations",
        ["channel_id", "state", "scheduled_start_at", "scheduled_end_at"],
    )

    # ── 4. schedule_state_transitions ──
    op.create_table(
        "schedule_state_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "reservation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("schedule_reservations.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("from_state", sa.String(50), nullable=False),
        sa.Column("to_state", sa.String(50), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("actor", sa.String(50), nullable=False, server_default="SCHEDULER"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── 5. scheduler_dispatch_outbox ──
    op.create_table(
        "scheduler_dispatch_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "reservation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("schedule_reservations.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("celery_task_name", sa.String(255), nullable=False),
        sa.Column("celery_args", postgresql.JSON, nullable=False),
        sa.Column(
            "idempotency_key",
            sa.String(64),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="PENDING",
            index=True,
        ),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column(
            "scheduled_send_at",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENT', 'RETRY', 'DEAD_LETTER')",
            name="ck_scheduler_dispatch_outbox_status",
        ),
    )

    # Composite index for outbox relay query
    op.create_index(
        "ix_dispatch_outbox_pending",
        "scheduler_dispatch_outbox",
        ["status", "scheduled_send_at"],
    )


def downgrade() -> None:
    op.drop_table("scheduler_dispatch_outbox")
    op.drop_table("schedule_state_transitions")
    op.drop_table("schedule_reservations")
    op.drop_table("schedule_decisions")
    op.drop_index("uix_schedule_policies_active_category", table_name="schedule_policies")
    op.drop_table("schedule_policies")
