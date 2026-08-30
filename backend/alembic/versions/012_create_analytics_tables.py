"""012 — Create Analytics Engine tables and constraints.

Revision ID: 012
Revises: 011
Create Date: 2026-08-30

Creates eleven tables for the OMEGA-012 Analytics Engine:
1. analytics_assets — Authoritative entity binding PublishIntent and PublishAttempt to tracking
2. analytics_metric_capabilities — Static registry of metric/dimension/scope capabilities
3. analytics_quota_buckets — Dynamic tracking of provider quota consumption and internal budgets
4. analytics_poll_jobs — Lease coordination and fencing for background polling tasks
5. analytics_provider_snapshots — Single append-only raw HTTP response store (video and channel)
6. analytics_windows — Temporal evaluation windows (relative elapsed & Pacific calendar days)
7. analytics_metric_observations — Normalized immutable provider facts
8. analytics_metric_latest_pointer — Mutable query projection for current metric observation state
9. analytics_computed_metrics — Immutable derived and heuristic metrics with multi-source lineage
10. analytics_channel_snapshots — Normalized channel metric snapshots referencing raw evidence
11. analytics_channel_latest_pointer — Mutable query projection for current channel snapshot state
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. analytics_assets ──
    op.create_table(
        "analytics_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "platform_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_accounts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "publish_intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publish_intents.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "publish_attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publish_attempts.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "media_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "channel_dna_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(32), nullable=False, server_default="YOUTUBE"),
        sa.Column("provider_video_id", sa.String(128), nullable=False, index=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("asset_status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("lifecycle_phase", sa.String(32), nullable=False, server_default="ACTIVE_HOURLY"),
        sa.Column("next_poll_due_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("poll_error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_category", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "provider", "provider_video_id", name="uq_analytics_assets_provider_video"
        ),
        sa.CheckConstraint(
            "asset_status IN ('ACTIVE', 'MATURE', 'ARCHIVED', 'PROVIDER_DELETED', 'UNAVAILABLE', 'FAILED')",
            name="chk_analytics_assets_status",
        ),
        sa.CheckConstraint(
            "lifecycle_phase IN ('ACTIVE_HOURLY', 'MATURE_DAILY', 'STABLE_WEEKLY', 'ARCHIVED_MONTHLY', 'PAUSED')",
            name="chk_analytics_assets_lifecycle",
        ),
    )
    op.create_index(
        "idx_analytics_assets_poll_due",
        "analytics_assets",
        ["asset_status", "next_poll_due_at"],
    )

    # ── 2. analytics_metric_capabilities ──
    op.create_table(
        "analytics_metric_capabilities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("canonical_metric_name", sa.String(64), nullable=False),
        sa.Column("provider_metric_name", sa.String(64), nullable=False),
        sa.Column("api_source", sa.String(64), nullable=False),
        sa.Column("supported_report_types", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("compatible_dimensions", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("required_scopes", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("availability_class", sa.String(32), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False, server_default="COUNT"),
        sa.Column("aggregation_semantics", sa.String(32), nullable=False, server_default="SUM"),
        sa.Column("capability_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "provider",
            "canonical_metric_name",
            "api_source",
            "capability_version",
            name="uq_analytics_metric_capabilities",
        ),
        sa.CheckConstraint(
            "availability_class IN ('UNIVERSAL', 'DIMENSION_RESTRICTED', 'PRIVACY_GATED', 'PROVIDER_GATED', 'CHANNEL_ONLY', 'DEPRECATED')",
            name="chk_metric_capabilities_class",
        ),
    )
    op.create_index(
        "idx_metric_capabilities_lookup",
        "analytics_metric_capabilities",
        ["provider", "canonical_metric_name"],
    )

    # ── 3. analytics_quota_buckets ──
    op.create_table(
        "analytics_quota_buckets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("quota_bucket", sa.String(64), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("configured_daily_limit", sa.BigInteger(), nullable=True),
        sa.Column("internal_budget_limit", sa.BigInteger(), nullable=True),
        sa.Column("provider_authoritative_usage", sa.BigInteger(), nullable=True),
        sa.Column("estimated_internal_units", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "provider_timezone",
            sa.String(64),
            nullable=False,
            server_default="America/Los_Angeles",
        ),
        sa.Column("last_reset_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_reset_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "evidence_source", sa.String(64), nullable=False, server_default="DEFAULT_ESTIMATE"
        ),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "provider", "quota_bucket", "method", name="uq_analytics_quota_buckets"
        ),
    )

    # ── 4. analytics_poll_jobs ──
    op.create_table(
        "analytics_poll_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("poll_execution_key", sa.String(64), nullable=False, index=True),
        sa.Column("target_asset_ids", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claim_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_by_worker_id", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CLAIMED', 'COMPLETED', 'FAILED', 'EXPIRED')",
            name="chk_analytics_poll_jobs_status",
        ),
    )
    op.create_index(
        "idx_poll_jobs_lease",
        "analytics_poll_jobs",
        ["status", "lease_expires_at"],
    )

    # ── 5. analytics_provider_snapshots ──
    op.create_table(
        "analytics_provider_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_assets.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "platform_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_accounts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("api_endpoint", sa.String(64), nullable=False),
        sa.Column("request_params", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_payload", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_checksum", sa.String(64), nullable=False),
        sa.Column("logical_query_key", sa.String(64), nullable=False, index=True),
        sa.Column("poll_execution_key", sa.String(64), nullable=False, index=True),
        sa.Column("snapshot_dedupe_key", sa.String(64), nullable=False, unique=True),
        sa.Column("retrieval_timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("http_status", sa.Integer(), nullable=False, server_default="200"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_provider_snapshots_asset_time",
        "analytics_provider_snapshots",
        ["asset_id", "retrieval_timestamp"],
    )

    # ── 6. analytics_windows ──
    op.create_table(
        "analytics_windows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_assets.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("window_type", sa.String(32), nullable=False),
        sa.Column("provider_date", sa.Date(), nullable=True),
        sa.Column(
            "provider_timezone",
            sa.String(64),
            nullable=False,
            server_default="America/Los_Angeles",
        ),
        sa.Column("window_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_state", sa.String(32), nullable=False, server_default="PROVISIONAL"),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "asset_id",
            "window_type",
            "window_start_utc",
            name="uq_analytics_windows_asset_type_start",
        ),
        sa.CheckConstraint(
            "window_state IN ('PENDING', 'PROVISIONAL', 'FINALIZED', 'REVISED')",
            name="chk_analytics_windows_state",
        ),
    )
    op.create_index(
        "idx_analytics_windows_state",
        "analytics_windows",
        ["asset_id", "window_state"],
    )

    # ── 7. analytics_metric_observations ──
    op.create_table(
        "analytics_metric_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_assets.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "window_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_windows.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_provider_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False, server_default="PROVIDER_FACT"),
        sa.Column("metric_quality", sa.String(32), nullable=False),
        sa.Column("numeric_value", sa.Float(), nullable=True),
        sa.Column("integer_value", sa.BigInteger(), nullable=True),
        sa.Column("string_value", sa.String(255), nullable=True),
        sa.Column("dimensions", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("dimensions_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("normalization_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("observation_dedupe_key", sa.String(64), nullable=False, unique=True),
        sa.Column("revision_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "preceding_observation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_metric_observations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("observation_timestamp", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "window_id",
            "metric_name",
            "dimensions_hash",
            "revision_sequence",
            name="uq_metric_obs_window_metric_dim_rev",
        ),
        sa.CheckConstraint(
            "classification = 'PROVIDER_FACT'",
            name="chk_metric_obs_classification",
        ),
        sa.CheckConstraint(
            "metric_quality IN ('AVAILABLE', 'ZERO_CONFIRMED', 'NOT_READY', 'NOT_AVAILABLE', 'SUPPRESSED', 'PERMISSION_DENIED', 'PROVIDER_ERROR', 'STALE', 'REVISED', 'UNKNOWN_MISSING')",
            name="chk_metric_obs_quality",
        ),
    )
    op.create_index(
        "idx_metric_obs_asset_metric",
        "analytics_metric_observations",
        ["asset_id", "metric_name"],
    )

    # ── 8. analytics_metric_latest_pointer ──
    op.create_table(
        "analytics_metric_latest_pointer",
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_assets.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "window_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_windows.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("metric_name", sa.String(64), primary_key=True),
        sa.Column("dimensions_hash", sa.String(64), primary_key=True, server_default=""),
        sa.Column(
            "current_observation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_metric_observations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("current_revision_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── 9. analytics_computed_metrics ──
    op.create_table(
        "analytics_computed_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_assets.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "window_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_windows.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("formula_identifier", sa.String(64), nullable=False),
        sa.Column("formula_version", sa.String(32), nullable=False),
        sa.Column("source_observation_ids", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "source_computed_metric_ids", postgresql.JSON(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("source_checksum", sa.String(64), nullable=False),
        sa.Column("computation_dedupe_key", sa.String(64), nullable=False, unique=True),
        sa.Column("numeric_value", sa.Float(), nullable=True),
        sa.Column("metric_quality", sa.String(32), nullable=False, server_default="AVAILABLE"),
        sa.Column(
            "computation_revision_sequence", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "window_id",
            "metric_name",
            "formula_version",
            "computation_revision_sequence",
            name="uq_computed_metrics_window_name_ver_rev",
        ),
        sa.CheckConstraint(
            "classification IN ('DETERMINISTIC_DERIVED', 'HEURISTIC_FEATURE')",
            name="chk_computed_metrics_classification",
        ),
        sa.CheckConstraint(
            "metric_quality IN ('AVAILABLE', 'ZERO_CONFIRMED', 'NOT_READY', 'NOT_AVAILABLE', 'SUPPRESSED', 'PERMISSION_DENIED', 'PROVIDER_ERROR', 'STALE', 'REVISED', 'UNKNOWN_MISSING')",
            name="chk_computed_metrics_quality",
        ),
    )
    op.create_index(
        "idx_computed_metrics_lookup",
        "analytics_computed_metrics",
        ["asset_id", "metric_name", "classification"],
    )

    # ── 10. analytics_channel_snapshots ──
    op.create_table(
        "analytics_channel_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "platform_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_accounts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_provider_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column(
            "provider_timezone",
            sa.String(64),
            nullable=False,
            server_default="America/Los_Angeles",
        ),
        sa.Column("window_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_views", sa.BigInteger(), nullable=False),
        sa.Column("subscriber_count", sa.BigInteger(), nullable=False),
        sa.Column("video_count", sa.Integer(), nullable=False),
        sa.Column("aggregate_watch_time_seconds", sa.Float(), nullable=True),
        sa.Column("revision_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "preceding_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_channel_snapshots.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "channel_id",
            "snapshot_date",
            "revision_sequence",
            name="uq_channel_snapshots_channel_date_rev",
        ),
    )
    op.create_index(
        "idx_channel_snapshots_date",
        "analytics_channel_snapshots",
        ["channel_id", "snapshot_date"],
    )

    # ── 11. analytics_channel_latest_pointer ──
    op.create_table(
        "analytics_channel_latest_pointer",
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("snapshot_date", sa.Date(), primary_key=True),
        sa.Column(
            "current_channel_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_channel_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("current_revision_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("analytics_channel_latest_pointer")
    op.drop_table("analytics_channel_snapshots")
    op.drop_table("analytics_computed_metrics")
    op.drop_table("analytics_metric_latest_pointer")
    op.drop_table("analytics_metric_observations")
    op.drop_table("analytics_windows")
    op.drop_table("analytics_provider_snapshots")
    op.drop_table("analytics_poll_jobs")
    op.drop_table("analytics_quota_buckets")
    op.drop_table("analytics_metric_capabilities")
    op.drop_table("analytics_assets")
