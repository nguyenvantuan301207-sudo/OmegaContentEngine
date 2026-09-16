"""018 - Create append-only attribution delivery evidence.

Revision ID: 018
Revises: 017
Create Date: 2026-09-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attribution_delivery_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column(
            "obligation_ids",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("delivery_channel", sa.String(32), nullable=False),
        sa.Column("target_context_id", sa.String(255), nullable=False),
        sa.Column("target_platform", sa.String(50), nullable=True),
        sa.Column("target_account_id", sa.String(128), nullable=True),
        sa.Column("delivered_text", sa.Text(), nullable=False),
        sa.Column("delivered_text_sha256", sa.String(64), nullable=False),
        sa.Column("verification_state", sa.String(32), nullable=False),
        sa.Column("evidence_reference", sa.Text(), nullable=False),
        sa.Column("evidence_checksum", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "supersedes_evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attribution_delivery_evidence.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "delivery_channel IN ('PHYSICAL_RENDER', 'PUBLISH_METADATA', 'EXPORT_SIDECAR')",
            name="ck_attribution_delivery_evidence_channel",
        ),
        sa.CheckConstraint(
            "verification_state IN ('UNVERIFIED', 'VERIFIED', 'REJECTED')",
            name="ck_attribution_delivery_evidence_verification_state",
        ),
        sa.CheckConstraint(
            "length(artifact_sha256) = 64",
            name="ck_attribution_delivery_evidence_artifact_hash_length",
        ),
        sa.CheckConstraint(
            "length(delivered_text_sha256) = 64",
            name="ck_attribution_delivery_evidence_text_hash_length",
        ),
        sa.CheckConstraint(
            "length(evidence_checksum) = 64",
            name="ck_attribution_delivery_evidence_checksum_length",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="ck_attribution_delivery_evidence_schema_version",
        ),
        sa.CheckConstraint(
            "delivery_channel != 'PUBLISH_METADATA' OR "
            "(target_platform IS NOT NULL AND target_account_id IS NOT NULL)",
            name="ck_attribution_delivery_evidence_publish_target",
        ),
        sa.CheckConstraint(
            "delivery_channel = 'PUBLISH_METADATA' OR "
            "(target_platform IS NULL AND target_account_id IS NULL)",
            name="ck_attribution_delivery_evidence_non_publish_target",
        ),
    )
    op.create_index(
        "idx_attribution_delivery_evidence_artifact_id",
        "attribution_delivery_evidence",
        ["artifact_id"],
    )
    op.create_index(
        "idx_attribution_delivery_evidence_artifact_recorded",
        "attribution_delivery_evidence",
        ["artifact_id", "recorded_at"],
    )
    op.create_index(
        "idx_attribution_delivery_evidence_publish_target",
        "attribution_delivery_evidence",
        ["target_platform", "target_account_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_attribution_delivery_evidence_publish_target",
        table_name="attribution_delivery_evidence",
    )
    op.drop_index(
        "idx_attribution_delivery_evidence_artifact_recorded",
        table_name="attribution_delivery_evidence",
    )
    op.drop_index(
        "idx_attribution_delivery_evidence_artifact_id",
        table_name="attribution_delivery_evidence",
    )
    op.drop_table("attribution_delivery_evidence")
