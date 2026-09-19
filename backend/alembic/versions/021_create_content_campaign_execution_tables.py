"""021 - Create canonical content campaign execution authorities.

Revision ID: 021
Revises: 020
Create Date: 2026-09-19
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_campaign_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "campaign_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_campaigns.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
        sa.Column("status", sa.String(32), nullable=False, server_default="MATERIALIZED"),
        sa.Column("fanout_policy_name", sa.String(100), nullable=False),
        sa.Column("fanout_policy_version", sa.Integer(), nullable=False),
        sa.Column("fanout_policy_checksum", sa.String(64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("materialized_by", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "campaign_id", name="uq_content_campaign_executions_campaign_id"
        ),
        sa.CheckConstraint(
            "status = 'MATERIALIZED'", name="ck_content_campaign_executions_status"
        ),
        sa.CheckConstraint(
            "item_count >= 1 AND item_count <= 50",
            name="ck_content_campaign_executions_item_count_range",
        ),
    )
    op.create_index(
        "idx_content_campaign_executions_channel_id",
        "content_campaign_executions",
        ["channel_id"],
    )
    op.create_index(
        "idx_content_campaign_executions_dna_revision",
        "content_campaign_executions",
        ["channel_dna_revision_id"],
    )
    op.create_index(
        "idx_content_campaign_executions_status",
        "content_campaign_executions",
        ["status"],
    )

    op.create_table(
        "content_campaign_item_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "campaign_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_campaign_executions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "campaign_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_campaign_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "mission_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mission_executions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "campaign_item_id", name="uq_content_campaign_item_executions_item_id"
        ),
        sa.UniqueConstraint(
            "mission_id", name="uq_content_campaign_item_executions_mission_id"
        ),
        sa.UniqueConstraint(
            "mission_execution_id",
            name="uq_content_campaign_item_executions_mission_execution_id",
        ),
        sa.UniqueConstraint(
            "campaign_execution_id",
            "position",
            name="uq_content_campaign_item_executions_exec_pos",
        ),
        sa.CheckConstraint(
            "position >= 1 AND position <= 50",
            name="ck_content_campaign_item_executions_position_positive",
        ),
    )
    op.create_index(
        "idx_content_campaign_item_executions_exec_id",
        "content_campaign_item_executions",
        ["campaign_execution_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_content_campaign_item_executions_exec_id",
        table_name="content_campaign_item_executions",
    )
    op.drop_table("content_campaign_item_executions")
    op.drop_index(
        "idx_content_campaign_executions_status",
        table_name="content_campaign_executions",
    )
    op.drop_index(
        "idx_content_campaign_executions_dna_revision",
        table_name="content_campaign_executions",
    )
    op.drop_index(
        "idx_content_campaign_executions_channel_id",
        table_name="content_campaign_executions",
    )
    op.drop_table("content_campaign_executions")
