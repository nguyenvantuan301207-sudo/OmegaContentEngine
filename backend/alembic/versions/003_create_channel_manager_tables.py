"""003 — Create Channel Manager tables and Mission integrations.

Revision ID: 003
Revises: 002
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. channels ──
    op.create_table(
        "channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state", sa.String(50), nullable=False, server_default="DRAFT"),
        sa.Column("platform", sa.String(50), nullable=False, server_default="YOUTUBE"),
        sa.Column("platform_channel_id", sa.String(255), nullable=True),
        sa.Column("primary_language", sa.String(20), nullable=False, server_default="en"),
        sa.Column("target_region", sa.String(10), nullable=False, server_default="US"),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="UTC"),
        sa.Column("dna", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
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
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_channels_slug", "channels", ["slug"], unique=True)
    op.create_index("ix_channels_state", "channels", ["state"])
    op.create_index("ix_channels_platform", "channels", ["platform"])

    # ── 2. channel_dna_revisions ──
    op.create_table(
        "channel_dna_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(50), nullable=False, server_default="USER"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("channel_id", "version", name="uq_channel_dna_version"),
    )
    op.create_index("ix_channel_dna_revisions_channel_id", "channel_dna_revisions", ["channel_id"])

    # ── 3. Alter missions: add channel_id ──
    op.add_column(
        "missions",
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_missions_channel_id", "missions", ["channel_id"])

    # ── 4. Alter mission_executions: add channel_dna_revision_id ──
    op.add_column(
        "mission_executions",
        sa.Column(
            "channel_dna_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_dna_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_mission_executions_channel_dna_revision_id",
        "mission_executions",
        ["channel_dna_revision_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_mission_executions_channel_dna_revision_id", table_name="mission_executions")
    op.drop_column("mission_executions", "channel_dna_revision_id")

    op.drop_index("ix_missions_channel_id", table_name="missions")
    op.drop_column("missions", "channel_id")

    op.drop_index("ix_channel_dna_revisions_channel_id", table_name="channel_dna_revisions")
    op.drop_table("channel_dna_revisions")

    op.drop_index("ix_channels_platform", table_name="channels")
    op.drop_index("ix_channels_state", table_name="channels")
    op.drop_index("ix_channels_slug", table_name="channels")
    op.drop_table("channels")
