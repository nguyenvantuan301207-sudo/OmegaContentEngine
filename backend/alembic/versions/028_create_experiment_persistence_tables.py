"""028 - Create Durable Experiment Persistence Tables.

Revision ID: 028
Revises: 027
Create Date: 2026-10-04

Establishes canonical database persistence for P25-C Controlled Experimentation & Causal Attribution:
1. experiment_roots: Stable experiment identity, target scope, and lifecycle state.
2. experiment_revisions: Immutable causal definition revisions pinning hypothesis, metrics, and parameters.
3. experiment_revision_dimensions: Normalized changed creative dimensions for precise overlap detection.
4. experiment_variants: Normalized variant authority with P24 acceptance evidence snapshots.
5. experiment_exposures: Explicit exposure tracking supporting individual, aggregate, and provider-native modes.
6. experiment_attribution_results: Append-only immutable causal attribution results with deterministic replay identity.
7. experiment_analysis_inputs: Normalized lineage referencing exact P25-B performance snapshots.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. experiment_roots
    op.create_table(
        "experiment_roots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("target_scope_type", sa.String(32), nullable=False),
        sa.Column("target_scope_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("current_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            "status IN ('DRAFT', 'READY', 'RUNNING', 'PAUSED', 'COMPLETED', 'CANCELLED', 'INVALIDATED')",
            name="ck_experiment_roots_status",
        ),
        sa.CheckConstraint(
            "target_scope_type IN ('MEDIA_ARTIFACT', 'PUBLICATION', 'CONTENT_PRODUCTION', 'CHANNEL_TIME_BUCKET')",
            name="ck_experiment_roots_scope_type",
        ),
    )
    op.create_index(
        "idx_experiment_roots_target_scope",
        "experiment_roots",
        ["target_scope_type", "target_scope_id"],
    )
    op.create_index("idx_experiment_roots_status", "experiment_roots", ["status"])

    # 2. experiment_revisions
    op.create_table(
        "experiment_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "experiment_root_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_roots.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "supersedes_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("experiment_type", sa.String(32), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("primary_metric", sa.String(64), nullable=False),
        sa.Column("secondary_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("assignment_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("experiment_unit", sa.String(32), nullable=False),
        sa.Column("minimum_sample_size", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("analysis_window_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("experiment_root_id", "revision_number", name="uq_experiment_revisions_root_rev"),
        sa.CheckConstraint(
            "experiment_type IN ('TITLE', 'THUMBNAIL', 'TITLE_AND_THUMBNAIL', 'DESCRIPTION', 'PACKAGING_BUNDLE')",
            name="ck_experiment_revisions_type",
        ),
        sa.CheckConstraint(
            "experiment_unit IN ('IMPRESSION', 'VIEWER', 'PUBLICATION', 'TIME_BUCKET')",
            name="ck_experiment_revisions_unit",
        ),
        sa.CheckConstraint("revision_number >= 1", name="ck_experiment_revisions_rev_pos"),
        sa.CheckConstraint("minimum_sample_size >= 1", name="ck_experiment_revisions_min_sample_pos"),
        sa.CheckConstraint("analysis_window_hours >= 1", name="ck_experiment_revisions_window_pos"),
    )

    # Add FK from roots to current revision
    op.create_foreign_key(
        "fk_experiment_roots_current_revision",
        "experiment_roots",
        "experiment_revisions",
        ["current_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. experiment_revision_dimensions
    op.create_table(
        "experiment_revision_dimensions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "experiment_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_revisions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("dimension", sa.String(32), nullable=False),
        sa.UniqueConstraint("experiment_revision_id", "dimension", name="uq_exp_rev_dim"),
        sa.CheckConstraint(
            "dimension IN ('TITLE', 'THUMBNAIL', 'DESCRIPTION', 'PACKAGING_BUNDLE', 'AUDIO_MIX', 'VIDEO_RENDER')",
            name="ck_exp_rev_dim_valid",
        ),
    )
    op.create_index("idx_exp_rev_dim_dim", "experiment_revision_dimensions", ["dimension"])

    # 4. experiment_variants
    op.create_table(
        "experiment_variants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "experiment_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_revisions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("change_dimension", sa.String(32), nullable=False),
        sa.Column(
            "media_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("packaging_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("thumbnail_concept_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("thumbnail_ref", sa.Text(), nullable=True),
        sa.Column("is_accepted_p24", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("creative_qa_status", sa.String(16), nullable=False, server_default="FAIL"),
        sa.Column("variant_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("experiment_revision_id", "id", name="uq_exp_variants_rev_id"),
        sa.CheckConstraint("role IN ('CONTROL', 'TREATMENT')", name="ck_exp_variants_role"),
        sa.CheckConstraint("creative_qa_status IN ('PASS', 'REVISE', 'FAIL')", name="ck_exp_variants_qa_status"),
    )
    # Exactly one control per revision enforced by database partial index
    op.create_index(
        "uq_exp_variants_single_control",
        "experiment_variants",
        ["experiment_revision_id"],
        unique=True,
        postgresql_where=sa.text("role = 'CONTROL'"),
    )

    # 5. experiment_exposures
    op.create_table(
        "experiment_exposures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "experiment_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_revisions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exposure_mode", sa.String(32), nullable=False),
        sa.Column("subject_key", sa.String(128), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "exposed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("source_lineage", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "exposure_mode IN ('INDIVIDUAL', 'AGGREGATE', 'PROVIDER_NATIVE')",
            name="ck_exp_exposures_mode",
        ),
        sa.CheckConstraint("sample_count >= 1", name="ck_exp_exposures_sample_pos"),
        sa.ForeignKeyConstraint(
            ["experiment_revision_id", "variant_id"],
            ["experiment_variants.experiment_revision_id", "experiment_variants.id"],
            ondelete="CASCADE",
            name="fk_exp_exposures_variant",
        ),
    )
    op.create_index(
        "idx_exp_exposures_rev_var",
        "experiment_exposures",
        ["experiment_revision_id", "variant_id"],
    )
    op.create_index("idx_exp_exposures_exposed_at", "experiment_exposures", ["exposed_at"])

    # 6. experiment_attribution_results
    op.create_table(
        "experiment_attribution_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "experiment_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_revisions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("control_variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("treatment_variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("analysis_window", sa.String(32), nullable=False),
        sa.Column("input_lineage_fingerprint", sa.String(64), nullable=False),
        sa.Column("control_value", sa.Float(), nullable=True),
        sa.Column("treatment_value", sa.Float(), nullable=True),
        sa.Column("absolute_difference", sa.Float(), nullable=True),
        sa.Column("relative_lift", sa.Float(), nullable=True),
        sa.Column("sample_basis", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("statistical_inference", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("data_maturity", sa.String(32), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "data_maturity IN ('FRESH', 'MATURE', 'DELAYED', 'STALE', 'UNAVAILABLE', 'INSUFFICIENT_DATA')",
            name="ck_attr_results_maturity",
        ),
        sa.CheckConstraint(
            "classification IN ('INCONCLUSIVE', 'CONTROL_BETTER', 'TREATMENT_BETTER', 'NO_MEANINGFUL_DIFFERENCE', 'INSUFFICIENT_DATA', 'INVALID_EXPERIMENT')",
            name="ck_attr_results_classification",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_revision_id", "control_variant_id"],
            ["experiment_variants.experiment_revision_id", "experiment_variants.id"],
            ondelete="RESTRICT",
            name="fk_attr_res_control",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_revision_id", "treatment_variant_id"],
            ["experiment_variants.experiment_revision_id", "experiment_variants.id"],
            ondelete="RESTRICT",
            name="fk_attr_res_treatment",
        ),
        sa.UniqueConstraint(
            "experiment_revision_id",
            "control_variant_id",
            "treatment_variant_id",
            "metric",
            "analysis_window",
            "input_lineage_fingerprint",
            name="uq_attr_results_deterministic_identity",
        ),
    )
    op.create_index("idx_attr_results_eval_at", "experiment_attribution_results", ["evaluated_at"])

    # 7. experiment_analysis_inputs
    op.create_table(
        "experiment_analysis_inputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attribution_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_attribution_results.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_provider_snapshots.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("snapshot_type", sa.String(64), nullable=False),
        sa.Column("snapshot_checksum", sa.String(64), nullable=False),
        sa.Column("retrieval_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "attribution_result_id", "snapshot_checksum", name="uq_analysis_input_checksum"
        ),
    )


def downgrade() -> None:
    op.drop_table("experiment_analysis_inputs")
    op.drop_table("experiment_attribution_results")
    op.drop_table("experiment_exposures")
    op.drop_index("uq_exp_variants_single_control", table_name="experiment_variants")
    op.drop_table("experiment_variants")
    op.drop_table("experiment_revision_dimensions")
    op.drop_constraint("fk_experiment_roots_current_revision", "experiment_roots", type_="foreignkey")
    op.drop_table("experiment_revisions")
    op.drop_table("experiment_roots")
