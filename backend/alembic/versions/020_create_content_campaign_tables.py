"""020 - Create canonical content campaign authorities.

Revision ID: 020
Revises: 019
Create Date: 2026-09-18
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "channel_dna_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("objective", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="READY"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("plan_checksum", sa.String(64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "channel_id", "idempotency_key", name="uq_content_campaigns_channel_idempotency"
        ),
        sa.CheckConstraint("status = 'READY'", name="ck_content_campaigns_status"),
        sa.CheckConstraint(
            "item_count >= 1 AND item_count <= 50", name="ck_content_campaigns_item_count_range"
        ),
        sa.CheckConstraint("priority >= 1", name="ck_content_campaigns_priority_positive"),
    )
    op.create_index(
        "idx_content_campaigns_channel_id", "content_campaigns", ["channel_id"]
    )
    op.create_index(
        "idx_content_campaigns_dna_revision", "content_campaigns", ["channel_dna_revision_id"]
    )
    op.create_index("idx_content_campaigns_status", "content_campaigns", ["status"])
    op.create_index("idx_content_campaigns_created_at", "content_campaigns", ["created_at"])

    op.create_table(
        "content_campaign_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "campaign_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_campaigns.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "selection_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_selection_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "selection_decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_selection_decisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "topic_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("topic_candidates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("target_content_type", sa.String(50), nullable=False),
        sa.Column("planned_release_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "campaign_id", "position", name="uq_content_campaign_items_campaign_position"
        ),
        sa.UniqueConstraint(
            "selection_run_id", name="uq_content_campaign_items_selection_run"
        ),
        sa.UniqueConstraint(
            "campaign_id", "topic_candidate_id", name="uq_content_campaign_items_campaign_candidate"
        ),
        sa.CheckConstraint(
            "position >= 1 AND position <= 50", name="ck_content_campaign_items_position_positive"
        ),
        sa.CheckConstraint(
            "target_content_type IN ('YOUTUBE_LONGFORM', 'YOUTUBE_SHORT')",
            name="ck_content_campaign_items_content_type",
        ),
    )
    op.create_index(
        "idx_content_campaign_items_campaign_id", "content_campaign_items", ["campaign_id"]
    )
    op.create_index(
        "idx_content_campaign_items_decision_id",
        "content_campaign_items",
        ["selection_decision_id"],
    )
    op.create_index(
        "idx_content_campaign_items_candidate_id",
        "content_campaign_items",
        ["topic_candidate_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_content_campaign_items_candidate_id", table_name="content_campaign_items")
    op.drop_index("idx_content_campaign_items_decision_id", table_name="content_campaign_items")
    op.drop_index("idx_content_campaign_items_campaign_id", table_name="content_campaign_items")
    op.drop_table("content_campaign_items")
    op.drop_index("idx_content_campaigns_created_at", table_name="content_campaigns")
    op.drop_index("idx_content_campaigns_status", table_name="content_campaigns")
    op.drop_index("idx_content_campaigns_dna_revision", table_name="content_campaigns")
    op.drop_index("idx_content_campaigns_channel_id", table_name="content_campaigns")
    op.drop_table("content_campaigns")
