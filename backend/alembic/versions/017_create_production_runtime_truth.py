"""017 - Create artifact-scoped production runtime truth.

Revision ID: 017
Revises: 016
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "production_runtime_truth",
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_artifacts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("manifest_run_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_production_runtime_truth_schema_version_positive",
        ),
        sa.CheckConstraint(
            "length(manifest_run_fingerprint) = 64",
            name="ck_production_runtime_truth_manifest_fingerprint_length",
        ),
    )


def downgrade() -> None:
    op.drop_table("production_runtime_truth")
