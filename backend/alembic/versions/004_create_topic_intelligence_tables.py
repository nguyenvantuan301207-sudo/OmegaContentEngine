"""004 — Create Topic Intelligence tables.

Revision ID: 004
Revises: 003
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. topic_memory ──
    op.create_table(
        "topic_memory",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("canonical_topic", sa.String(300), nullable=False),
        sa.Column("normalized_topic", sa.String(300), nullable=False),
        sa.Column("topic_fingerprint", sa.String(64), nullable=False),
        sa.Column("entities", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("keywords", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("semantic_tags", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("times_discovered", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("times_selected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("times_produced", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("times_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_produced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_evaluation_score", sa.Float(), nullable=True),
        sa.Column("metadata", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.UniqueConstraint("channel_id", "topic_fingerprint", name="uq_channel_topic_fingerprint"),
    )
    op.create_index("ix_topic_memory_channel_id", "topic_memory", ["channel_id"])
    op.create_index("ix_topic_memory_topic_fingerprint", "topic_memory", ["topic_fingerprint"])
    op.create_index("ix_topic_memory_last_selected_at", "topic_memory", ["last_selected_at"])

    # ── 2. topic_candidates ──
    op.create_table(
        "topic_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("normalized_title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(50), nullable=False, server_default="MANUAL"),
        sa.Column("source_name", sa.String(100), nullable=False, server_default="user_input"),
        sa.Column("source_ref", sa.String(500), nullable=True),
        sa.Column("language", sa.String(20), nullable=False, server_default="en"),
        sa.Column("region", sa.String(10), nullable=False, server_default="US"),
        sa.Column("entities", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("keywords", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("tags", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("topic_fingerprint", sa.String(64), nullable=False),
        sa.Column("audience_fit_score", sa.Float(), nullable=True),
        sa.Column("strategic_fit_score", sa.Float(), nullable=True),
        sa.Column("trend_score", sa.Float(), nullable=True),
        sa.Column("novelty_score", sa.Float(), nullable=True),
        sa.Column("content_gap_score", sa.Float(), nullable=True),
        sa.Column("historical_performance_score", sa.Float(), nullable=True),
        sa.Column("cost_efficiency_score", sa.Float(), nullable=True),
        sa.Column("revenue_potential_score", sa.Float(), nullable=True),
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column("duplicate_status", sa.String(50), nullable=False, server_default="FRESH_TOPIC"),
        sa.Column(
            "similar_memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("topic_memory.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="DISCOVERED"),
        sa.Column("reasons", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("score_breakdown", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.String(100), nullable=True),
        sa.Column("metadata", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
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
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_topic_candidates_channel_id", "topic_candidates", ["channel_id"])
    op.create_index("ix_topic_candidates_normalized_title", "topic_candidates", ["normalized_title"])
    op.create_index("ix_topic_candidates_source_type", "topic_candidates", ["source_type"])
    op.create_index("ix_topic_candidates_topic_fingerprint", "topic_candidates", ["topic_fingerprint"])
    op.create_index("ix_topic_candidates_final_score", "topic_candidates", ["final_score"])
    op.create_index("ix_topic_candidates_status", "topic_candidates", ["status"])
    op.create_index("ix_topic_candidates_idempotency_key", "topic_candidates", ["idempotency_key"])
    op.create_index("ix_topic_candidates_created_at", "topic_candidates", ["created_at"])

    # ── 3. topic_angles ──
    op.create_table(
        "topic_angles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("topic_candidates.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("topic_memory.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("angle", sa.String(300), nullable=False),
        sa.Column("normalized_angle", sa.String(300), nullable=False),
        sa.Column("hook", sa.String(500), nullable=True),
        sa.Column("audience_intent", sa.String(100), nullable=True),
        sa.Column("format_hint", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_topic_angles_candidate_id", "topic_angles", ["candidate_id"])
    op.create_index("ix_topic_angles_memory_id", "topic_angles", ["memory_id"])


def downgrade() -> None:
    op.drop_index("ix_topic_angles_memory_id", table_name="topic_angles")
    op.drop_index("ix_topic_angles_candidate_id", table_name="topic_angles")
    op.drop_table("topic_angles")

    op.drop_index("ix_topic_candidates_created_at", table_name="topic_candidates")
    op.drop_index("ix_topic_candidates_idempotency_key", table_name="topic_candidates")
    op.drop_index("ix_topic_candidates_status", table_name="topic_candidates")
    op.drop_index("ix_topic_candidates_final_score", table_name="topic_candidates")
    op.drop_index("ix_topic_candidates_topic_fingerprint", table_name="topic_candidates")
    op.drop_index("ix_topic_candidates_source_type", table_name="topic_candidates")
    op.drop_index("ix_topic_candidates_normalized_title", table_name="topic_candidates")
    op.drop_index("ix_topic_candidates_channel_id", table_name="topic_candidates")
    op.drop_table("topic_candidates")

    op.drop_index("ix_topic_memory_last_selected_at", table_name="topic_memory")
    op.drop_index("ix_topic_memory_topic_fingerprint", table_name="topic_memory")
    op.drop_index("ix_topic_memory_channel_id", table_name="topic_memory")
    op.drop_table("topic_memory")
