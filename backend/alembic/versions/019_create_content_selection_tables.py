"""019 - Create canonical content selection authorities.

Revision ID: 019
Revises: 018
Create Date: 2026-09-18
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_selection_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channels.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("channel_dna_revision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("mission_execution_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("mission_executions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="READY"),
        sa.Column("policy_name", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("policy_checksum", sa.String(64), nullable=False),
        sa.Column("candidate_set_checksum", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("recommended_candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("topic_candidates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("selected_candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("topic_candidates.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("selection_mode", sa.String(32), nullable=True),
        sa.Column("selected_by", sa.String(100), nullable=True),
        sa.Column("selection_reason", sa.Text(), nullable=True),
        sa.Column("considered_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("channel_id", "idempotency_key", name="uq_content_selection_runs_channel_idempotency"),
        sa.CheckConstraint("status IN ('READY', 'SELECTED')", name="ck_content_selection_runs_status"),
        sa.CheckConstraint("selection_mode IS NULL OR selection_mode IN ('POLICY', 'OVERRIDE')", name="ck_content_selection_runs_mode"),
        sa.CheckConstraint("considered_count > 0", name="ck_content_selection_runs_count_positive"),
        sa.CheckConstraint(
            "(status = 'READY' AND selected_candidate_id IS NULL AND selection_mode IS NULL AND selected_by IS NULL AND selection_reason IS NULL AND selected_at IS NULL) OR "
            "(status = 'SELECTED' AND selected_candidate_id IS NOT NULL AND selection_mode IS NOT NULL AND selected_by IS NOT NULL AND selection_reason IS NOT NULL AND selected_at IS NOT NULL)",
            name="ck_content_selection_runs_finalization_fields",
        ),
    )
    op.create_index("idx_content_selection_runs_channel_id", "content_selection_runs", ["channel_id"])
    op.create_index("idx_content_selection_runs_dna_revision", "content_selection_runs", ["channel_dna_revision_id"])
    op.create_index("idx_content_selection_runs_mission_execution", "content_selection_runs", ["mission_execution_id"])
    op.create_index("idx_content_selection_runs_status", "content_selection_runs", ["status"])

    op.create_table(
        "content_selection_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("selection_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_selection_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("topic_candidates.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("score_breakdown", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("reasons", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("duplicate_status", sa.String(50), nullable=False),
        sa.Column("similar_memory_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("topic_memory.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("candidate_title_snapshot", sa.String(300), nullable=False),
        sa.Column("topic_fingerprint_snapshot", sa.String(64), nullable=False),
        sa.Column("candidate_snapshot", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_snapshot", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("selection_run_id", "candidate_id", name="uq_selection_decisions_run_candidate"),
        sa.UniqueConstraint("selection_run_id", "rank", name="uq_selection_decisions_run_rank"),
        sa.CheckConstraint("rank > 0", name="ck_selection_decisions_rank_positive"),
        sa.CheckConstraint("final_score >= 0 AND final_score <= 100", name="ck_selection_decisions_score_range"),
    )
    op.create_index("idx_content_selection_decisions_run", "content_selection_decisions", ["selection_run_id"])
    op.create_index("idx_content_selection_decisions_candidate", "content_selection_decisions", ["candidate_id"])


def downgrade() -> None:
    op.drop_index("idx_content_selection_decisions_candidate", table_name="content_selection_decisions")
    op.drop_index("idx_content_selection_decisions_run", table_name="content_selection_decisions")
    op.drop_table("content_selection_decisions")
    op.drop_index("idx_content_selection_runs_status", table_name="content_selection_runs")
    op.drop_index("idx_content_selection_runs_mission_execution", table_name="content_selection_runs")
    op.drop_index("idx_content_selection_runs_dna_revision", table_name="content_selection_runs")
    op.drop_index("idx_content_selection_runs_channel_id", table_name="content_selection_runs")
    op.drop_table("content_selection_runs")
