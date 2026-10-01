"""025 — Create Recurring Schedules and Downstream Atomic Bindings.

Revision ID: 025
Revises: 024
Create Date: 2026-10-01

Creates tables and constraints for OMEGA P20-A Durable Scheduling:
1. recurring_schedules — Authoritative recurring schedule entity and lifecycle
2. recurring_schedule_versions — Immutable definition snapshots (cron/interval, rules, payload)
3. recurring_schedule_occurrences — Logical occurrence ledger with unique idempotency
4. recurring_schedule_mission_bindings — Downstream atomic Mission idempotency binding
5. recurring_schedule_campaign_bindings — Downstream atomic Campaign admission idempotency binding
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None

# Hard Schema Safety Bounds
HARD_MINIMUM_INTERVAL_SECONDS = 60
HARD_MAX_CATCH_UP_CAP = 10


def upgrade() -> None:
    # ── 1. Create PostgreSQL ENUM types ──
    op.execute(
        "CREATE TYPE recurring_schedule_status AS ENUM "
        "('DRAFT', 'ACTIVE', 'PAUSED', 'COMPLETED', 'CANCELLED', 'ARCHIVED');"
    )
    op.execute(
        "CREATE TYPE recurring_schedule_target_type AS ENUM "
        "('CAMPAIGN_ADMISSION', 'STANDALONE_MISSION');"
    )
    op.execute(
        "CREATE TYPE catch_up_policy_enum AS ENUM "
        "('SKIP_MISSED', 'RUN_LATEST_ONLY', 'RUN_ALL_BOUNDED');"
    )
    op.execute(
        "CREATE TYPE dst_ambiguous_enum AS ENUM "
        "('FIRST', 'SECOND', 'REJECT');"
    )
    op.execute(
        "CREATE TYPE dst_nonexistent_enum AS ENUM "
        "('NEXT_VALID', 'SKIP', 'REJECT');"
    )
    op.execute(
        "CREATE TYPE recurring_occurrence_status AS ENUM "
        "('PENDING', 'WAITING', 'DISPATCHING', 'DISPATCHED', 'SKIPPED', 'FAILED', 'CANCELLED');"
    )

    # ── 2. Create recurring_schedules table ──
    op.create_table(
        "recurring_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT", "ACTIVE", "PAUSED", "COMPLETED", "CANCELLED", "ARCHIVED",
                name="recurring_schedule_status",
                create_type=False,
            ),
            nullable=False,
            server_default="DRAFT",
            index=True,
        ),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint(
            "end_time IS NULL OR end_time > start_time",
            name="chk_schedule_end_after_start",
        ),
        sa.CheckConstraint(
            "status != 'ACTIVE' OR (current_version_id IS NOT NULL AND next_run_at IS NOT NULL)",
            name="chk_schedule_active_executable",
        ),
        sa.CheckConstraint(
            "status != 'DRAFT' OR next_run_at IS NULL",
            name="chk_schedule_draft_no_run",
        ),
        sa.CheckConstraint(
            "status NOT IN ('COMPLETED', 'CANCELLED', 'ARCHIVED') OR next_run_at IS NULL",
            name="chk_schedule_terminal_no_run",
        ),
    )

    # ── 3. Create recurring_schedule_versions table ──
    op.create_table(
        "recurring_schedule_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "schedule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recurring_schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("cron_expression", sa.String(64), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column(
            "dst_ambiguous_strategy",
            postgresql.ENUM(
                "FIRST", "SECOND", "REJECT",
                name="dst_ambiguous_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="FIRST",
        ),
        sa.Column(
            "dst_nonexistent_strategy",
            postgresql.ENUM(
                "NEXT_VALID", "SKIP", "REJECT",
                name="dst_nonexistent_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="NEXT_VALID",
        ),
        sa.Column(
            "catch_up_policy",
            postgresql.ENUM(
                "SKIP_MISSED", "RUN_LATEST_ONLY", "RUN_ALL_BOUNDED",
                name="catch_up_policy_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="SKIP_MISSED",
        ),
        sa.Column("max_catch_up_occurrences", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "target_type",
            postgresql.ENUM(
                "CAMPAIGN_ADMISSION", "STANDALONE_MISSION",
                name="recurring_schedule_target_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload_template", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint("version_number >= 1", name="chk_version_number_positive"),
        sa.CheckConstraint(
            "(cron_expression IS NOT NULL AND interval_seconds IS NULL) OR "
            "(cron_expression IS NULL AND interval_seconds IS NOT NULL)",
            name="chk_recurrence_rule",
        ),
        sa.CheckConstraint(
            f"interval_seconds IS NULL OR interval_seconds >= {HARD_MINIMUM_INTERVAL_SECONDS}",
            name="chk_interval_minimum",
        ),
        sa.CheckConstraint(
            f"max_catch_up_occurrences >= 1 AND max_catch_up_occurrences <= {HARD_MAX_CATCH_UP_CAP}",
            name="chk_max_catch_up_range",
        ),
        sa.UniqueConstraint("schedule_id", "version_number", name="uq_schedule_version_number"),
        sa.UniqueConstraint("schedule_id", "id", name="uq_schedule_versions_identity"),
    )

    # ── 4. Add composite FK from schedules to versions ──
    op.create_foreign_key(
        "fk_recurring_schedules_current_version",
        "recurring_schedules",
        "recurring_schedule_versions",
        ["id", "current_version_id"],
        ["schedule_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_index(
        "ix_recurring_schedules_active_due",
        "recurring_schedules",
        ["status", "next_run_at"],
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    # ── 5. Create recurring_schedule_occurrences table ──
    op.create_table(
        "recurring_schedule_occurrences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "schedule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recurring_schedules.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("schedule_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurrence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING", "WAITING", "DISPATCHING", "DISPATCHED", "SKIPPED", "FAILED", "CANCELLED",
                name="recurring_occurrence_status",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
            index=True,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "downstream_target_type",
            postgresql.ENUM(
                "CAMPAIGN_ADMISSION", "STANDALONE_MISSION",
                name="recurring_schedule_target_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("downstream_target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wait_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.UniqueConstraint("schedule_id", "occurrence_at", name="uq_recurring_occurrence_schedule_time"),
        sa.UniqueConstraint("idempotency_key", name="uq_recurring_occurrence_idempotency_key"),
        sa.CheckConstraint("attempt_count >= 0", name="chk_attempt_count_positive"),
        sa.ForeignKeyConstraint(
            ["schedule_id", "schedule_version_id"],
            ["recurring_schedule_versions.schedule_id", "recurring_schedule_versions.id"],
            name="fk_recurring_occurrences_version",
            ondelete="RESTRICT",
        ),
    )

    op.create_index(
        "ix_recurring_occurrences_pending",
        "recurring_schedule_occurrences",
        ["status", "created_at"],
        postgresql_where=sa.text("status IN ('PENDING', 'WAITING', 'DISPATCHING')"),
    )

    # ── 6. Create recurring_schedule_mission_bindings table ──
    op.create_table(
        "recurring_schedule_mission_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "occurrence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recurring_schedule_occurrences.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.UniqueConstraint("occurrence_id", name="uq_sched_mission_binding_occurrence"),
        sa.UniqueConstraint("mission_id", name="uq_sched_mission_binding_mission"),
    )

    # ── 7. Create recurring_schedule_campaign_bindings table ──
    op.create_table(
        "recurring_schedule_campaign_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "occurrence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recurring_schedule_occurrences.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "campaign_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_campaigns.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "campaign_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_campaign_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.UniqueConstraint("occurrence_id", name="uq_sched_campaign_binding_occurrence"),
        sa.UniqueConstraint("campaign_item_id", name="uq_sched_campaign_binding_item"),
    )


def downgrade() -> None:
    # ── 1. Drop binding tables ──
    op.drop_table("recurring_schedule_campaign_bindings")
    op.drop_table("recurring_schedule_mission_bindings")

    # ── 2. Drop occurrences table ──
    op.drop_table("recurring_schedule_occurrences")

    # ── 3. Drop composite FK and tables ──
    op.drop_constraint(
        "fk_recurring_schedules_current_version",
        "recurring_schedules",
        type_="foreignkey",
    )
    op.drop_table("recurring_schedule_versions")
    op.drop_table("recurring_schedules")

    # ── 4. Drop PostgreSQL ENUM types ──
    op.execute("DROP TYPE IF EXISTS recurring_occurrence_status;")
    op.execute("DROP TYPE IF EXISTS dst_nonexistent_enum;")
    op.execute("DROP TYPE IF EXISTS dst_ambiguous_enum;")
    op.execute("DROP TYPE IF EXISTS catch_up_policy_enum;")
    op.execute("DROP TYPE IF EXISTS recurring_schedule_target_type;")
    op.execute("DROP TYPE IF EXISTS recurring_schedule_status;")
