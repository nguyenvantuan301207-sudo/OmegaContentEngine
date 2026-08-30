"""008 — Create Guardian Subsystem tables and columns.

Revision ID: 008
Revises: 007
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Add guardian_epoch to missions ──
    op.add_column(
        "missions",
        sa.Column("guardian_epoch", sa.Integer(), nullable=False, server_default="1"),
    )

    # ── 2. Add dispatched_epoch to tasks ──
    op.add_column(
        "tasks",
        sa.Column("dispatched_epoch", sa.Integer(), nullable=True),
    )

    # ── 3. guardian_rulesets ──
    op.create_table(
        "guardian_rulesets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="DRAFT", index=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column(
            "rules_config",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── 4. guardian_checks ──
    op.create_table(
        "guardian_checks",
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
            "production_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_requests.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "media_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_artifacts.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("trigger_type", sa.String(50), nullable=False),
        sa.Column("checkpoint", sa.String(50), nullable=False, index=True),
        sa.Column(
            "ruleset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guardian_rulesets.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("ruleset_version", sa.String(50), nullable=False),
        sa.Column("ruleset_checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING", index=True),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("guardian_epoch", sa.Integer(), nullable=False),
        sa.Column(
            "diagnostic_context",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "mission_id", "checkpoint", "idempotency_key", name="uq_guardian_check_idempotency"
        ),
    )
    op.create_index(
        "ix_guardian_checks_mission_created", "guardian_checks", ["mission_id", "created_at"]
    )

    # ── 5. guardian_detector_runs ──
    op.create_table(
        "guardian_detector_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "guardian_check_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guardian_checks.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("detector_type", sa.String(100), nullable=False),
        sa.Column("detector_version", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING", index=True),
        sa.Column("failure_policy", sa.String(50), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("error_data", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "guardian_check_id",
            "detector_type",
            "detector_version",
            name="uq_guardian_detector_run",
        ),
    )

    # ── 6. guardian_findings ──
    op.create_table(
        "guardian_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "guardian_check_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guardian_checks.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "detector_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guardian_detector_runs.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("detector_type", sa.String(100), nullable=False),
        sa.Column("detector_version", sa.String(50), nullable=False),
        sa.Column("rule_id", sa.String(100), nullable=False, index=True),
        sa.Column("severity", sa.String(50), nullable=False, index=True),
        sa.Column("risk_type", sa.String(100), nullable=False, index=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("evidence", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column(
            "location_reference",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_guardian_findings_rule_severity", "guardian_findings", ["rule_id", "severity"]
    )

    # ── 7. guardian_decisions ──
    op.create_table(
        "guardian_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "guardian_check_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guardian_checks.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("action", sa.String(50), nullable=False, index=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("resulting_gate_state", sa.String(50), nullable=False, index=True),
        sa.Column("actor", sa.String(100), nullable=False, server_default="GUARDIAN_ENGINE"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("guardian_check_id", name="uq_guardian_decision_check"),
    )

    # ── 8. guardian_exceptions ──
    op.create_table(
        "guardian_exceptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rule_id", sa.String(100), nullable=True, index=True),
        sa.Column("risk_type", sa.String(100), nullable=True, index=True),
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
            nullable=True,
            index=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_reason", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("revoked_by", sa.String(100), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("rule_id IS NOT NULL OR risk_type IS NOT NULL", name="ck_exception_scope"),
    )

    # ── 9. guardian_decision_findings ──
    op.create_table(
        "guardian_decision_findings",
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guardian_decisions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guardian_findings.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "applied_exception_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guardian_exceptions.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
    )

    # ── 10. guardian_resolution_events ──
    op.create_table(
        "guardian_resolution_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guardian_findings.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guardian_decisions.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("resolution_type", sa.String(50), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "finding_id IS NOT NULL OR decision_id IS NOT NULL", name="ck_resolution_target"
        ),
    )

    # ── 11. guardian_alert_outbox ──
    op.create_table(
        "guardian_alert_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "guardian_check_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guardian_checks.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guardian_decisions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("channel", sa.String(50), nullable=False),
        sa.Column("destination", sa.String(255), nullable=False),
        sa.Column("payload", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING", index=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "scheduled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            index=True,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── 12. cost_records ──
    op.create_table(
        "cost_records",
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
            "production_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_requests.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column("cost_type", sa.String(50), nullable=False, index=True),
        sa.Column("amount_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_type", sa.String(100), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── 13. guardian_state_transitions ──
    op.create_table(
        "guardian_state_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("checkpoint", sa.String(50), nullable=False),
        sa.Column("from_gate_state", sa.String(50), nullable=False),
        sa.Column("to_gate_state", sa.String(50), nullable=False),
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("guardian_decisions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("guardian_epoch", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_guardian_transitions_mission_created",
        "guardian_state_transitions",
        ["mission_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("guardian_state_transitions")
    op.drop_table("cost_records")
    op.drop_table("guardian_alert_outbox")
    op.drop_table("guardian_resolution_events")
    op.drop_table("guardian_decision_findings")
    op.drop_table("guardian_exceptions")
    op.drop_table("guardian_decisions")
    op.drop_table("guardian_findings")
    op.drop_table("guardian_detector_runs")
    op.drop_table("guardian_checks")
    op.drop_table("guardian_rulesets")
    op.drop_column("tasks", "dispatched_epoch")
    op.drop_column("missions", "guardian_epoch")
