"""026 — Create Pipeline Analytics Rollups Table.

Revision ID: 026
Revises: 025
Create Date: 2026-10-01

Creates pipeline_analytics_rollups table for OMEGA P20-B Deterministic Pipeline Analytics Foundation:
1. pipeline_analytics_rollups — Daily UTC aggregate rollup table for execution reliability,
   performance, scheduler health, and QA outcomes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_analytics_rollups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("metric_family", sa.String(64), nullable=False),
        sa.Column("dimension_type", sa.String(32), nullable=False),
        sa.Column("dimension_value", sa.String(128), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.UniqueConstraint(
            "metric_family",
            "dimension_type",
            "dimension_value",
            "bucket_start",
            name="uq_pipeline_analytics_rollup_bucket",
        ),
        sa.CheckConstraint(
            "bucket_end = bucket_start + interval '1 day'",
            name="chk_rollup_daily_utc_bucket",
        ),
        sa.CheckConstraint("sample_count >= 0", name="chk_rollup_sample_count"),
        sa.CheckConstraint("schema_version > 0", name="chk_rollup_schema_version"),
        sa.CheckConstraint(
            "metric_family IN ('render_reliability', 'render_performance', 'scheduler_reliability', 'qa_quality')",
            name="chk_rollup_metric_family",
        ),
        sa.CheckConstraint(
            "dimension_type IN ('GLOBAL', 'CHANNEL', 'VIDEO_CODEC', 'SCHEDULE_TARGET_TYPE')",
            name="chk_rollup_dimension_type",
        ),
        sa.CheckConstraint(
            "(metric_family IN ('render_reliability', 'render_performance') AND dimension_type IN ('GLOBAL', 'CHANNEL', 'VIDEO_CODEC')) OR "
            "(metric_family = 'scheduler_reliability' AND dimension_type IN ('GLOBAL', 'SCHEDULE_TARGET_TYPE')) OR "
            "(metric_family = 'qa_quality' AND dimension_type IN ('GLOBAL', 'CHANNEL'))",
            name="chk_rollup_family_dimension_matrix",
        ),
    )

    op.create_index(
        "ix_pipeline_analytics_rollup_query",
        "pipeline_analytics_rollups",
        ["metric_family", "dimension_type", "dimension_value", "bucket_start", "bucket_end"],
    )
    op.create_index(
        "ix_pipeline_analytics_rollup_updated",
        "pipeline_analytics_rollups",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_analytics_rollup_updated", table_name="pipeline_analytics_rollups")
    op.drop_index("ix_pipeline_analytics_rollup_query", table_name="pipeline_analytics_rollups")
    op.drop_table("pipeline_analytics_rollups")
