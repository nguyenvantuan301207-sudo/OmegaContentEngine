"""014 — Create Autonomy Loop tables and constraints.

Revision ID: 014
Revises: 013
Create Date: 2026-08-31

Creates exactly thirteen tables for the OMEGA-014 Autonomous Loop:
1. autonomy_loops — Loop identity and configuration binding
2. autonomy_policy_snapshots — Immutable versioned governance rules and budget/rate limits
3. autonomy_observation_snapshots — Immutable world state captures with subsystem checksums
4. autonomy_loop_iterations — Immutable iteration records with monotonic sequencing
5. autonomy_action_plans — Immutable proposed action plans
6. autonomy_action_attempts — Immutable action dispatch definitions
7. autonomy_action_attempt_outcomes — Append-only attempt lifecycle evidence log
8. autonomy_approval_requests — Immutable human/system approval requests with frozen context
9. autonomy_approval_decisions — Append-only approval decision log with actor tracking
10. autonomy_budget_ledgers — Authoritative per-loop aggregate spend accumulator
11. autonomy_budget_reservations — Durable per-action budget reservations with daily attribution
12. autonomy_events — Append-only audit trail with deterministic deduplication keys
13. autonomy_loop_latest_pointers — Sole authoritative mutable projection with claim fencing

Also adds nullable unique idempotency_key to existing subsystem tables:
- content_generation_requests.idempotency_key
- production_requests.idempotency_key
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 0. Add idempotency_key to existing subsystem tables ──
    op.add_column(
        "content_generation_requests",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_content_generation_requests_idempotency_key",
        "content_generation_requests",
        ["idempotency_key"],
    )
    op.create_index(
        "idx_content_generation_requests_idempotency_key",
        "content_generation_requests",
        ["idempotency_key"],
    )

    op.add_column(
        "production_requests",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_production_requests_idempotency_key",
        "production_requests",
        ["idempotency_key"],
    )
    op.create_index(
        "idx_production_requests_idempotency_key",
        "production_requests",
        ["idempotency_key"],
    )

    # ── 1. autonomy_loops ──
    op.create_table(
        "autonomy_loops",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "autonomy_level",
            sa.String(length=32),
            nullable=False,
            server_default="SUPERVISED",
        ),
        sa.Column("loop_dedupe_key", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("loop_dedupe_key", name="uq_autonomy_loops_dedupe_key"),
        sa.CheckConstraint(
            "autonomy_level IN ('MANUAL', 'SUPERVISED', 'BOUNDED_AUTONOMOUS')",
            name="ck_autonomy_loops_level",
        ),
    )
    op.create_index("idx_autonomy_loops_mission_id", "autonomy_loops", ["mission_id"])
    op.create_index("idx_autonomy_loops_channel_id", "autonomy_loops", ["channel_id"])

    # ── 2. autonomy_policy_snapshots ──
    op.create_table(
        "autonomy_policy_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_loops.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("max_iterations_per_hour", sa.Integer(), nullable=False, server_default="30"),
        sa.Column(
            "min_iteration_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
        sa.Column("daily_budget_cap_usd", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("lifetime_budget_cap_usd", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column(
            "policy_rules",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("policy_checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("loop_id", "policy_version", name="uq_autonomy_policy_version"),
    )
    op.create_index(
        "idx_autonomy_policy_snapshots_loop_id", "autonomy_policy_snapshots", ["loop_id"]
    )

    # ── 3. autonomy_observation_snapshots ──
    op.create_table(
        "autonomy_observation_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_loops.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("guardian_context_checksum", sa.String(length=64), nullable=False),
        sa.Column("guardian_epoch", sa.Integer(), nullable=False),
        sa.Column(
            "dna_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("raw_observation", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("observation_checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_autonomy_obs_snapshots_loop_time",
        "autonomy_observation_snapshots",
        ["loop_id", "captured_at"],
    )

    # ── 4. autonomy_loop_iterations ──
    op.create_table(
        "autonomy_loop_iterations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_loops.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "observation_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_observation_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "policy_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_policy_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("iteration_sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("iteration_dedupe_key", sa.String(length=64), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.String(length=255), nullable=True),
        sa.UniqueConstraint("loop_id", "iteration_sequence", name="uq_autonomy_iteration_seq"),
        sa.UniqueConstraint("iteration_dedupe_key", name="uq_autonomy_iteration_dedupe_key"),
    )
    op.create_index(
        "idx_autonomy_iterations_loop_seq",
        "autonomy_loop_iterations",
        ["loop_id", "iteration_sequence"],
    )

    # ── 5. autonomy_action_plans ──
    op.create_table(
        "autonomy_action_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "iteration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_loop_iterations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("plan_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("risk_class", sa.String(length=32), nullable=False),
        sa.Column("target_subsystem", sa.String(length=64), nullable=False),
        sa.Column("target_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "parameters",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "estimated_cost_usd",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            server_default="0.0000",
        ),
        sa.Column("precondition_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("action_checksum", sa.String(length=64), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("advisory_rationale", sa.String(length=500), nullable=True),
        sa.Column(
            "advisory_model_metadata",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("iteration_id", "plan_sequence", name="uq_autonomy_action_plan_seq"),
    )
    op.create_index(
        "idx_autonomy_action_plans_iter_seq",
        "autonomy_action_plans",
        ["iteration_id", "plan_sequence"],
    )

    # ── 6. autonomy_action_attempts ──
    op.create_table(
        "autonomy_action_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "action_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_action_plans.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("subsystem_correlation_key", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "action_plan_id", "attempt_sequence", name="uq_autonomy_action_attempt_seq"
        ),
        sa.UniqueConstraint(
            "subsystem_correlation_key", name="uq_autonomy_attempt_correlation_key"
        ),
    )
    op.create_index(
        "idx_autonomy_action_attempts_plan",
        "autonomy_action_attempts",
        ["action_plan_id", "attempt_sequence"],
    )

    # ── 7. autonomy_action_attempt_outcomes ──
    op.create_table(
        "autonomy_action_attempt_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "action_attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_action_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("outcome_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("claim_generation", sa.Integer(), nullable=False),
        sa.Column(
            "subsystem_response",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("outcome_dedupe_key", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "action_attempt_id", "outcome_sequence", name="uq_autonomy_attempt_outcome_seq"
        ),
        sa.UniqueConstraint("outcome_dedupe_key", name="uq_autonomy_outcome_dedupe_key"),
    )
    op.create_index(
        "idx_autonomy_outcomes_attempt",
        "autonomy_action_attempt_outcomes",
        ["action_attempt_id", "outcome_sequence"],
    )

    # ── 8. autonomy_approval_requests ──
    op.create_table(
        "autonomy_approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_loops.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "action_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_action_plans.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("semantic_action_key", sa.String(length=64), nullable=False),
        sa.Column("action_checksum", sa.String(length=64), nullable=False),
        sa.Column("precondition_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("guardian_context_checksum", sa.String(length=64), nullable=False),
        sa.Column("guardian_epoch", sa.Integer(), nullable=False),
        sa.Column(
            "dna_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "policy_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_policy_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("mission_state", sa.String(length=50), nullable=False),
        sa.Column(
            "mission_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mission_executions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_dedupe_key", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("request_dedupe_key", name="uq_autonomy_approval_req_dedupe"),
    )
    op.create_index("idx_autonomy_approvals_loop_id", "autonomy_approval_requests", ["loop_id"])

    # ── 9. autonomy_approval_decisions ──
    op.create_table(
        "autonomy_approval_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "approval_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_approval_requests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("decision_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("reviewer_user_id", sa.String(length=64), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=False),
        sa.Column("action_checksum", sa.String(length=64), nullable=False),
        sa.Column("precondition_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("guardian_context_checksum", sa.String(length=64), nullable=False),
        sa.Column("guardian_epoch", sa.Integer(), nullable=False),
        sa.Column(
            "dna_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "policy_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_policy_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("mission_state", sa.String(length=50), nullable=False),
        sa.Column(
            "mission_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mission_executions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decision_dedupe_key", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "approval_request_id",
            "decision_sequence",
            name="uq_autonomy_approval_decision_seq",
        ),
        sa.UniqueConstraint("decision_dedupe_key", name="uq_autonomy_approval_decision_dedupe"),
        sa.CheckConstraint(
            "decision IN ('APPROVED', 'REJECTED', 'EXPIRED', 'INVALIDATED')",
            name="ck_autonomy_approval_decision",
        ),
        sa.CheckConstraint(
            "actor_type IN ('HUMAN', 'SYSTEM')",
            name="ck_autonomy_approval_actor_type",
        ),
    )
    op.create_index(
        "idx_autonomy_approval_decisions_req",
        "autonomy_approval_decisions",
        ["approval_request_id"],
    )

    # ── 10. autonomy_budget_ledgers ──
    op.create_table(
        "autonomy_budget_ledgers",
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_loops.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("lifetime_cap_usd", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("daily_cap_usd", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column(
            "lifetime_spend_usd",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            server_default="0.0000",
        ),
        sa.Column(
            "daily_spend_usd",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            server_default="0.0000",
        ),
        sa.Column("current_day_utc", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── 11. autonomy_budget_reservations ──
    op.create_table(
        "autonomy_budget_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_loops.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "action_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_action_plans.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("semantic_action_key", sa.String(length=64), nullable=False),
        sa.Column("reservation_key", sa.String(length=64), nullable=False),
        sa.Column("estimated_amount", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("captured_amount", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="RESERVED"),
        sa.Column("budget_day_utc", sa.Date(), nullable=False),
        sa.Column(
            "reserved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.String(length=255), nullable=True),
        sa.Column("provider_cost_reference", sa.String(length=128), nullable=True),
        sa.UniqueConstraint("reservation_key", name="uq_autonomy_budget_reservation_key"),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'RECONCILIATION_REQUIRED', 'CAPTURED', 'RELEASED')",
            name="ck_autonomy_budget_reservation_status",
        ),
    )
    op.create_index(
        "idx_autonomy_budget_res_loop_status",
        "autonomy_budget_reservations",
        ["loop_id", "status"],
    )
    op.create_index(
        "idx_autonomy_budget_res_day",
        "autonomy_budget_reservations",
        ["loop_id", "budget_day_utc"],
    )

    # ── 12. autonomy_events ──
    op.create_table(
        "autonomy_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_loops.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "iteration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_loop_iterations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "actor",
            sa.String(length=64),
            nullable=False,
            server_default="AUTONOMY_ENGINE",
        ),
        sa.Column(
            "payload",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("event_dedupe_key", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("event_dedupe_key", name="uq_autonomy_event_dedupe_key"),
    )
    op.create_index(
        "idx_autonomy_events_loop_time",
        "autonomy_events",
        ["loop_id", "occurred_at"],
    )

    # ── 13. autonomy_loop_latest_pointers ──
    op.create_table(
        "autonomy_loop_latest_pointers",
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_loops.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "operational_state",
            sa.String(length=32),
            nullable=False,
            server_default="IDLE",
        ),
        sa.Column(
            "current_iteration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_loop_iterations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "current_iteration_sequence",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "current_action_attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("autonomy_action_attempts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("active_claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "active_claim_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_iteration_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "operational_state IN ('IDLE', 'OBSERVING', 'PLANNING', 'WAITING_APPROVAL', 'EXECUTING', 'VERIFYING', 'WAITING_SIGNAL', 'PAUSED', 'BLOCKED', 'COMPLETED', 'FAILED', 'CANCELLED')",
            name="ck_autonomy_loop_latest_state",
        ),
    )


def downgrade() -> None:
    # Drop in reverse order of creation
    op.drop_table("autonomy_loop_latest_pointers")
    op.drop_table("autonomy_events")
    op.drop_table("autonomy_budget_reservations")
    op.drop_table("autonomy_budget_ledgers")
    op.drop_table("autonomy_approval_decisions")
    op.drop_table("autonomy_approval_requests")
    op.drop_table("autonomy_action_attempt_outcomes")
    op.drop_table("autonomy_action_attempts")
    op.drop_table("autonomy_action_plans")
    op.drop_table("autonomy_loop_iterations")
    op.drop_table("autonomy_observation_snapshots")
    op.drop_table("autonomy_policy_snapshots")
    op.drop_table("autonomy_loops")

    op.drop_index(
        "idx_production_requests_idempotency_key",
        table_name="production_requests",
    )
    op.drop_constraint(
        "uq_production_requests_idempotency_key",
        "production_requests",
        type_="unique",
    )
    op.drop_column("production_requests", "idempotency_key")

    op.drop_index(
        "idx_content_generation_requests_idempotency_key",
        table_name="content_generation_requests",
    )
    op.drop_constraint(
        "uq_content_generation_requests_idempotency_key",
        "content_generation_requests",
        type_="unique",
    )
    op.drop_column("content_generation_requests", "idempotency_key")
