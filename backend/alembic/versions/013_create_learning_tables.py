"""013 — Create Learning Engine tables and constraints.

Revision ID: 013
Revises: 012
Create Date: 2026-08-31

Creates exactly sixteen tables for the OMEGA-013 Learning Engine:
1. learning_ingestion_cursors — Durable high-water mark tracking for OMEGA-012 discovery
2. learning_input_snapshots — Immutable store of ingested OMEGA-012 observations
3. learning_input_latest_pointers — Mutable synchronization anchor and latest pointer
4. learning_feature_snapshots — Versioned deterministic and model-extracted content features
5. learning_model_extractions — Audit provenance log for LLM feature extractions
6. learning_cohorts — Channel-local comparison cohort definitions
7. learning_cohort_members — Mapping between feature snapshots and cohorts
8. learning_baselines — Immutable central tendency and dispersion performance baselines
9. learning_hypotheses — Immutable versioned hypothesis definitions
10. learning_hypothesis_latest_pointers — Mutable lifecycle projection for hypotheses
11. learning_hypothesis_evaluations — Immutable statistical evaluation results
12. learning_evaluation_evidence_manifests — Complete, reproducible sample population manifest
13. learning_knowledge_items — Immutable knowledge claims and statistical evidence links
14. learning_knowledge_latest_pointers — Mutable lifecycle projection for knowledge claims
15. learning_events — Authoritative append-only audit event journal
16. learning_jobs — Fenced background execution jobs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. learning_ingestion_cursors
    op.create_table(
        "learning_ingestion_cursors",
        sa.Column("consumer_id", sa.String(64), primary_key=True),
        sa.Column("cursor_updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cursor_window_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("high_water_mark_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "last_success_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # 2. learning_input_snapshots
    op.create_table(
        "learning_input_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "publish_intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publish_intents.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("provider_video_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "media_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "channel_dna_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("window_type", sa.String(32), nullable=False),
        sa.Column("window_state", sa.String(32), nullable=False),
        sa.Column("window_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_metrics", postgresql.JSONB(), nullable=False),
        sa.Column("metric_qualities", postgresql.JSONB(), nullable=False),
        sa.Column("classifications", postgresql.JSONB(), nullable=False),
        sa.Column("is_fully_finalized", sa.Boolean(), nullable=False),
        sa.Column(
            "quality_flags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("payload_checksum", sa.String(64), nullable=False),
        sa.Column("input_dedupe_key", sa.String(64), nullable=False, unique=True),
        sa.Column("revision_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "preceding_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_input_snapshots.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "observation_id",
            "window_type",
            "revision_sequence",
            name="uq_learning_input_obs_win_rev",
        ),
        sa.CheckConstraint(
            "window_state IN ('PENDING', 'PROVISIONAL', 'FINALIZED', 'REVISED')",
            name="ck_learning_input_window_state",
        ),
    )

    # 3. learning_input_latest_pointers
    op.create_table(
        "learning_input_latest_pointers",
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("window_type", sa.String(32), nullable=False),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "current_input_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_input_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("current_revision_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_payload_checksum", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint(
            "observation_id", "window_type", name="pk_learning_input_latest_pointers"
        ),
    )

    # 4. learning_feature_snapshots
    op.create_table(
        "learning_feature_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "input_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_input_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("feature_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("extractor_version", sa.String(32), nullable=False),
        sa.Column("deterministic_features", postgresql.JSONB(), nullable=False),
        sa.Column(
            "model_features",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("feature_snapshot_key", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # 5. learning_model_extractions
    op.create_table(
        "learning_model_extractions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "feature_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_feature_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("model_provider", sa.String(32), nullable=False),
        sa.Column("model_name", sa.String(64), nullable=False),
        sa.Column("prompt_template_version", sa.String(32), nullable=False),
        sa.Column("prompt_checksum", sa.String(64), nullable=False),
        sa.Column("response_checksum", sa.String(64), nullable=False),
        sa.Column("structured_output", postgresql.JSONB(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # 6. learning_cohorts
    op.create_table(
        "learning_cohorts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("cohort_slug", sa.String(64), nullable=False),
        sa.Column("content_format", sa.String(32), nullable=False),
        sa.Column("duration_bucket", sa.String(32), nullable=False),
        sa.Column(
            "dna_compatibility_mode",
            sa.String(32),
            nullable=False,
            server_default="STRICT_REVISION",
        ),
        sa.Column("evaluation_window", sa.String(32), nullable=False),
        sa.Column("inclusion_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("channel_id", "cohort_slug", name="uq_learning_cohorts_chan_slug"),
        sa.CheckConstraint(
            "content_format IN ('SHORT', 'LONG_FORM')", name="ck_learning_cohorts_content_format"
        ),
    )

    # 7. learning_cohort_members
    op.create_table(
        "learning_cohort_members",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "cohort_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_cohorts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "feature_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_feature_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("cohort_member_key", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "admitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # 8. learning_baselines
    op.create_table(
        "learning_baselines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "cohort_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_cohorts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("outcome_metric", sa.String(64), nullable=False),
        sa.Column("evaluation_window", sa.String(32), nullable=False),
        sa.Column("baseline_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("metric_median", sa.Float(), nullable=False),
        sa.Column("metric_mean", sa.Float(), nullable=False),
        sa.Column("metric_stddev", sa.Float(), nullable=False),
        sa.Column("metric_iqr", sa.Float(), nullable=False),
        sa.Column("metric_mad", sa.Float(), nullable=False),
        sa.Column("member_ids_checksum", sa.String(64), nullable=False),
        sa.Column("evaluation_as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("selection_policy_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("baseline_key", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_baselines_lookup_latest",
        "learning_baselines",
        ["cohort_id", "outcome_metric", "evaluation_window", sa.text("baseline_version DESC")],
    )

    # 9. learning_hypotheses
    op.create_table(
        "learning_hypotheses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "hypothesis_family_id", postgresql.UUID(as_uuid=True), nullable=False, index=True
        ),
        sa.Column("hypothesis_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "cohort_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_cohorts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("hypothesis_slug", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("factor_name", sa.String(64), nullable=False),
        sa.Column("treatment_definition", postgresql.JSONB(), nullable=False),
        sa.Column("control_definition", postgresql.JSONB(), nullable=False),
        sa.Column("target_outcome_metric", sa.String(64), nullable=False),
        sa.Column("target_evaluation_window", sa.String(32), nullable=False),
        sa.Column("definition_checksum", sa.String(64), nullable=False),
        sa.Column(
            "supersedes_hypothesis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_hypotheses.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "hypothesis_family_id", "hypothesis_version", name="uq_hypotheses_family_version"
        ),
        sa.UniqueConstraint(
            "hypothesis_family_id", "definition_checksum", name="uq_hypotheses_family_def_checksum"
        ),
    )

    # 10. learning_hypothesis_latest_pointers
    op.create_table(
        "learning_hypothesis_latest_pointers",
        sa.Column("hypothesis_family_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "current_hypothesis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_hypotheses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("latest_evaluation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "current_status IN ('DRAFT', 'ACTIVE', 'INCONCLUSIVE', 'SUPPORTED', 'WEAKENED', 'CONTRADICTED', 'STALE', 'SUPERSEDED')",
            name="ck_hypotheses_current_status",
        ),
    )

    # 11. learning_hypothesis_evaluations
    op.create_table(
        "learning_hypothesis_evaluations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "hypothesis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_hypotheses.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "hypothesis_family_id", postgresql.UUID(as_uuid=True), nullable=False, index=True
        ),
        sa.Column(
            "baseline_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_baselines.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("evaluation_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sample_size_treatment", sa.Integer(), nullable=False),
        sa.Column("sample_size_control", sa.Integer(), nullable=False),
        sa.Column("treatment_median", sa.Float(), nullable=False),
        sa.Column("control_median", sa.Float(), nullable=False),
        sa.Column("effect_size_absolute", sa.Float(), nullable=False),
        sa.Column("effect_size_relative_percent", sa.Float(), nullable=True),
        sa.Column("cliffs_delta", sa.Float(), nullable=False),
        sa.Column("p_value_raw", sa.Float(), nullable=False),
        sa.Column("p_value_adjusted", sa.Float(), nullable=False),
        sa.Column("confidence_class", sa.String(32), nullable=False),
        sa.Column("resulting_status", sa.String(32), nullable=False),
        sa.Column("evaluation_key", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "evaluated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "resulting_status IN ('INCONCLUSIVE', 'SUPPORTED', 'WEAKENED', 'CONTRADICTED')",
            name="ck_evaluation_resulting_status",
        ),
        sa.CheckConstraint(
            "confidence_class IN ('VERY_LOW', 'LOW', 'MODERATE', 'HIGH', 'VERY_HIGH')",
            name="ck_evaluation_confidence_class",
        ),
    )

    # Add foreign key from learning_hypothesis_latest_pointers.latest_evaluation_id to learning_hypothesis_evaluations.id
    op.create_foreign_key(
        "fk_hyp_pointers_latest_eval",
        "learning_hypothesis_latest_pointers",
        "learning_hypothesis_evaluations",
        ["latest_evaluation_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 12. learning_evaluation_evidence_manifests
    op.create_table(
        "learning_evaluation_evidence_manifests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "evaluation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_hypothesis_evaluations.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("treatment_input_snapshot_ids", postgresql.JSONB(), nullable=False),
        sa.Column("control_input_snapshot_ids", postgresql.JSONB(), nullable=False),
        sa.Column("source_learning_observation_ids", postgresql.JSONB(), nullable=False),
        sa.Column("source_feature_snapshot_ids", postgresql.JSONB(), nullable=False),
        sa.Column(
            "baseline_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_baselines.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("treatment_values", postgresql.JSONB(), nullable=False),
        sa.Column("control_values", postgresql.JSONB(), nullable=False),
        sa.Column("member_set_checksum", sa.String(64), nullable=False),
        sa.Column("analysis_version", sa.String(32), nullable=False),
        sa.Column("effect_policy_id", sa.String(64), nullable=False),
        sa.Column("effect_policy_version", sa.Integer(), nullable=False),
        sa.Column("confidence_policy_id", sa.String(64), nullable=False),
        sa.Column("confidence_policy_version", sa.Integer(), nullable=False),
        sa.Column("multiple_comparison_family_id", sa.String(128), nullable=False),
        sa.Column("correction_method", sa.String(32), nullable=False),
        sa.Column("correction_method_version", sa.Integer(), nullable=False),
        sa.Column("evaluation_as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("manifest_checksum", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # 13. learning_knowledge_items
    op.create_table(
        "learning_knowledge_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("knowledge_family_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("knowledge_type", sa.String(64), nullable=False),
        sa.Column("structured_claim", postgresql.JSONB(), nullable=False),
        sa.Column("human_readable_summary", sa.Text(), nullable=False),
        sa.Column("evidence_type", sa.String(32), nullable=False, server_default="OBSERVATIONAL"),
        sa.Column("confidence_class", sa.String(32), nullable=False),
        sa.Column("effect_size_absolute", sa.Float(), nullable=False),
        sa.Column("effect_size_relative_percent", sa.Float(), nullable=True),
        sa.Column("cliffs_delta", sa.Float(), nullable=False),
        sa.Column("sample_size_treatment", sa.Integer(), nullable=False),
        sa.Column("sample_size_control", sa.Integer(), nullable=False),
        sa.Column(
            "source_hypothesis_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_hypotheses.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "source_evaluation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_hypothesis_evaluations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_knowledge_items.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("knowledge_key", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "evidence_type IN ('OBSERVATIONAL', 'CONTROLLED_EXPERIMENT', 'MANUAL_LABEL', 'EXTERNAL_PRIOR')",
            name="ck_knowledge_evidence_type",
        ),
    )

    # 14. learning_knowledge_latest_pointers
    op.create_table(
        "learning_knowledge_latest_pointers",
        sa.Column("knowledge_family_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "current_knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_knowledge_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("current_revision_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "current_status IN ('ACTIVE', 'WEAKENED', 'STALE', 'SUPERSEDED', 'RETRACTED')",
            name="ck_knowledge_current_status",
        ),
    )

    # 15. learning_events
    op.create_table(
        "learning_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "source_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("event_dedupe_key", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "idx_learning_events_chan_time", "learning_events", ["channel_id", "occurred_at"]
    )
    op.create_index(
        "idx_learning_events_agg", "learning_events", ["aggregate_type", "aggregate_id"]
    )

    # 16. learning_jobs
    op.create_table(
        "learning_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING", index=True),
        sa.Column("job_dedupe_key", sa.String(64), nullable=False, unique=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claim_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_by_worker_id", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_category", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CLAIMED', 'COMPLETED', 'FAILED', 'DEFERRED')",
            name="ck_learning_jobs_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("learning_jobs")
    op.drop_index("idx_learning_events_agg", table_name="learning_events")
    op.drop_index("idx_learning_events_chan_time", table_name="learning_events")
    op.drop_table("learning_events")
    op.drop_table("learning_knowledge_latest_pointers")
    op.drop_table("learning_knowledge_items")
    op.drop_table("learning_evaluation_evidence_manifests")
    op.drop_constraint(
        "fk_hyp_pointers_latest_eval", "learning_hypothesis_latest_pointers", type_="foreignkey"
    )
    op.drop_table("learning_hypothesis_evaluations")
    op.drop_table("learning_hypothesis_latest_pointers")
    op.drop_table("learning_hypotheses")
    op.drop_index("idx_baselines_lookup_latest", table_name="learning_baselines")
    op.drop_table("learning_baselines")
    op.drop_table("learning_cohort_members")
    op.drop_table("learning_cohorts")
    op.drop_table("learning_model_extractions")
    op.drop_table("learning_feature_snapshots")
    op.drop_table("learning_input_latest_pointers")
    op.drop_table("learning_input_snapshots")
    op.drop_table("learning_ingestion_cursors")
