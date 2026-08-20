"""005 — Create Research Engine tables.

Revision ID: 005
Revises: 004
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. research_requests ──
    op.create_table(
        "research_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "topic_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("topic_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mission_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mission_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("mode", sa.String(50), nullable=False, server_default="INTERACTIVE"),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING"),
        sa.Column("outcome", sa.String(50), nullable=True),
        sa.Column("research_question", sa.String(500), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("language", sa.String(20), nullable=False, server_default="en"),
        sa.Column("region", sa.String(10), nullable=False, server_default="US"),
        sa.Column("max_sources", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("minimum_source_quality", sa.Float(), nullable=False, server_default="50.0"),
        sa.Column("minimum_claim_confidence", sa.Float(), nullable=False, server_default="60.0"),
        sa.Column("metadata", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_research_requests_channel_id", "research_requests", ["channel_id"])
    op.create_index("ix_research_requests_topic_candidate_id", "research_requests", ["topic_candidate_id"])
    op.create_index("ix_research_requests_mission_execution_id", "research_requests", ["mission_execution_id"])
    op.create_index("ix_research_requests_status", "research_requests", ["status"])

    # ── 2. research_sources ──
    op.create_table(
        "research_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "research_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(50), nullable=False, server_default="MANUAL"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("publisher", sa.String(200), nullable=False),
        sa.Column("author", sa.String(200), nullable=True),
        sa.Column("url", sa.String(1000), nullable=True),
        sa.Column("content_excerpt", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("primary_source_status", sa.String(50), nullable=False, server_default="UNKNOWN"),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="50.0"),
        sa.Column("relevance_score", sa.Float(), nullable=False, server_default="50.0"),
        sa.Column("freshness_score", sa.Float(), nullable=False, server_default="50.0"),
        sa.Column("quality_reasons", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("independence_cluster_id", sa.String(64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("language", sa.String(20), nullable=False, server_default="en"),
        sa.Column("region", sa.String(10), nullable=False, server_default="US"),
        sa.Column("metadata", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
    )
    op.create_index("ix_research_sources_research_request_id", "research_sources", ["research_request_id"])
    op.create_index("ix_research_sources_channel_id", "research_sources", ["channel_id"])
    op.create_index("ix_research_sources_content_hash", "research_sources", ["content_hash"])
    op.create_index("ix_research_sources_independence_cluster_id", "research_sources", ["independence_cluster_id"])

    # ── 3. research_claims ──
    op.create_table(
        "research_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "research_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("normalized_claim", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(50), nullable=False, server_default="FACT"),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("confidence_band", sa.String(50), nullable=False, server_default="LOW"),
        sa.Column("supporting_sources_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("contradicting_sources_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("independent_sources_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reasons", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("metadata", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_research_claims_research_request_id", "research_claims", ["research_request_id"])
    op.create_index("ix_research_claims_channel_id", "research_claims", ["channel_id"])
    op.create_index("ix_research_claims_confidence_score", "research_claims", ["confidence_score"])
    op.create_index("ix_research_claims_is_verified", "research_claims", ["is_verified"])

    # ── 4. claim_evidence ──
    op.create_table(
        "claim_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("support_direction", sa.String(50), nullable=False, server_default="SUPPORTS"),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("source_location", sa.String(200), nullable=True),
        sa.Column("strength_score", sa.Float(), nullable=False, server_default="80.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_claim_evidence_claim_id", "claim_evidence", ["claim_id"])
    op.create_index("ix_claim_evidence_source_id", "claim_evidence", ["source_id"])

    # ── 5. research_conflicts ──
    op.create_table(
        "research_conflicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "research_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_claims.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("conflict_type", sa.String(50), nullable=False, server_default="DIRECT_CONTRADICTION"),
        sa.Column("severity", sa.String(50), nullable=False, server_default="HIGH"),
        sa.Column("status", sa.String(50), nullable=False, server_default="OPEN"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("involved_evidence_ids", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("involved_source_ids", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_research_conflicts_research_request_id", "research_conflicts", ["research_request_id"])
    op.create_index("ix_research_conflicts_claim_id", "research_conflicts", ["claim_id"])
    op.create_index("ix_research_conflicts_status", "research_conflicts", ["status"])

    # ── 6. research_briefs ──
    op.create_table(
        "research_briefs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "research_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "topic_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("topic_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "supersedes_brief_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_briefs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("outcome", sa.String(50), nullable=False, server_default="INSUFFICIENT"),
        sa.Column("overall_confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("verified_claims", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("uncertain_claims", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("contradictions", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("key_facts", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("statistics", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("dates", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("quotes", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("open_questions", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("sources_summary", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("metadata", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("research_request_id", "version", name="uq_research_brief_version"),
    )
    op.create_index("ix_research_briefs_research_request_id", "research_briefs", ["research_request_id"])
    op.create_index("ix_research_briefs_channel_id", "research_briefs", ["channel_id"])
    op.create_index("ix_research_briefs_topic_candidate_id", "research_briefs", ["topic_candidate_id"])
    op.create_index("ix_research_briefs_is_current", "research_briefs", ["is_current"])


def downgrade() -> None:
    op.drop_table("research_briefs")
    op.drop_table("research_conflicts")
    op.drop_table("claim_evidence")
    op.drop_table("research_claims")
    op.drop_table("research_sources")
    op.drop_table("research_requests")
