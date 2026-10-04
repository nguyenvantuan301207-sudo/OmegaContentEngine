"""029 - Create Durable Learning Loop Persistence Tables.

Revision ID: 029
Revises: 028
Create Date: 2026-10-04

Establishes canonical database persistence for P25-D Evidence-Based Learning Loop:
1. learning_policy_roots: Stable policy root identity.
2. learning_policy_revisions: Immutable versioned policy configuration pinning thresholds and weights.
3. learning_loop_evidence: Immutable durable evidence records pinning exact P25-C and P25-B source truth.
4. learning_hypothesis_roots: Stable hypothesis identity with durable deduplication fingerprint.
5. learning_hypothesis_revisions: Immutable versioned hypothesis definitions pinning claim and scope.
6. learning_evaluations: Append-only immutable hypothesis evaluations with replay identity.
7. learning_evaluation_evidence_memberships: Normalized M2M link pinning evidence role (SUPPORTING, CONTRADICTING, CONTEXT, TRADEOFF).
8. learning_insights: Immutable learning insight synthesis linked to evaluations.
9. learning_recommendations: Actionable recommendations with safety snapshots and deterministic ranking.
10. learning_candidate_adaptations: Unapplied proposals with strict approval lifecycle states.
11. learning_adaptation_approval_history: Immutable audit history of candidate adaptation state transitions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. learning_policy_roots
    op.create_table(
        "learning_policy_roots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("policy_name", sa.String(64), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
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
    )

    # 2. learning_policy_revisions
    op.create_table(
        "learning_policy_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "policy_root_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_policy_roots.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("evidence_tier_weights", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("recency_half_life_days", sa.Float(), nullable=False, server_default="90.0"),
        sa.Column("replication_threshold", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("confidence_thresholds", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("generalization_policy", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("ranking_policy", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("goodhart_rules", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("tradeoff_rules", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "brand_safety_policy_version", sa.String(64), nullable=False, server_default="v1.0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("policy_root_id", "revision_number", name="uq_policy_root_rev_num"),
    )

    op.create_foreign_key(
        "fk_policy_roots_current_revision",
        "learning_policy_roots",
        "learning_policy_revisions",
        ["current_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. learning_loop_evidence
    op.create_table(
        "learning_loop_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("evidence_type", sa.String(64), nullable=False),
        sa.Column("source_phase", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column(
            "attribution_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiment_attribution_results.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_provider_snapshots.id", ondelete="RESTRICT"),
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
        sa.Column("scope_context", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("creative_dimensions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("effect_direction", sa.String(32), nullable=False),
        sa.Column("effect_magnitude", sa.Float(), nullable=True),
        sa.Column("sample_basis", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("data_maturity", sa.String(32), nullable=False),
        sa.Column("causal_status", sa.String(32), nullable=False),
        sa.Column("evidence_tier", sa.String(64), nullable=False),
        sa.Column("quality_flags", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "causal_status IN ('CAUSAL', 'ASSOCIATIONAL', 'DESCRIPTIVE', 'HEURISTIC', 'INSUFFICIENT')",
            name="ck_learning_evidence_causality",
        ),
        sa.CheckConstraint(
            "evidence_tier IN ('TIER_1_CAUSAL_EXPERIMENT', 'TIER_2_REPLICATED_CAUSAL', 'TIER_2_CAUSAL_REPLICATED', 'TIER_3_CONTROLLED_OBSERVATION', 'TIER_3_MULTI_PRODUCTION_OBSERVATIONAL', 'TIER_4_DESCRIPTIVE_ASSOCIATION', 'TIER_4_SINGLE_PRODUCTION_DESCRIPTIVE', 'TIER_5_HEURISTIC')",
            name="ck_learning_evidence_tier",
        ),
        sa.UniqueConstraint(
            "channel_id", "source_fingerprint", name="uq_learning_evidence_source_fp"
        ),
    )
    op.create_index(
        "idx_learning_evidence_lookup",
        "learning_loop_evidence",
        ["channel_id", "metric", "evidence_tier"],
    )

    # 4. learning_hypothesis_roots
    op.create_table(
        "learning_hypothesis_roots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("dedup_fingerprint", sa.String(64), nullable=False),
        sa.Column("target_dimension", sa.String(64), nullable=False),
        sa.Column("target_metric", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PROPOSED"),
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
            "status IN ('PROPOSED', 'EVALUATING', 'SUPPORTED', 'WEAKENED', 'CONTRADICTED', 'INCONCLUSIVE', 'RETIRED')",
            name="ck_hypothesis_root_status",
        ),
        sa.UniqueConstraint("channel_id", "dedup_fingerprint", name="uq_hypothesis_root_dedup"),
    )

    # 5. learning_hypothesis_revisions
    op.create_table(
        "learning_hypothesis_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hypothesis_root_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_hypothesis_roots.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("target_dimension", sa.String(64), nullable=False),
        sa.Column("target_metric", sa.String(64), nullable=False),
        sa.Column("predicted_direction", sa.String(32), nullable=False),
        sa.Column("scope_context", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("generalization_scope", sa.String(64), nullable=False, server_default="LOCAL"),
        sa.Column("evidence_requirements", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "hypothesis_root_id", "revision_number", name="uq_hypothesis_root_rev_num"
        ),
    )

    op.create_foreign_key(
        "fk_hypothesis_roots_current_revision",
        "learning_hypothesis_roots",
        "learning_hypothesis_revisions",
        ["current_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 6. learning_evaluations
    op.create_table(
        "learning_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hypothesis_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_hypothesis_revisions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "policy_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_policy_revisions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("evidence_set_fingerprint", sa.String(64), nullable=False),
        sa.Column("causal_status", sa.String(32), nullable=False),
        sa.Column("resulting_status", sa.String(32), nullable=False),
        sa.Column("confidence", sa.String(32), nullable=False),
        sa.Column("net_effect_magnitude", sa.Float(), nullable=True),
        sa.Column("tradeoffs_detected", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("limitations", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "confidence IN ('VERY_LOW', 'LOW', 'MODERATE', 'HIGH')",
            name="ck_learning_eval_confidence",
        ),
        sa.CheckConstraint(
            "resulting_status IN ('PROPOSED', 'EVALUATING', 'SUPPORTED', 'WEAKENED', 'CONTRADICTED', 'INCONCLUSIVE', 'RETIRED')",
            name="ck_learning_eval_status",
        ),
        sa.UniqueConstraint(
            "hypothesis_revision_id",
            "policy_revision_id",
            "evidence_set_fingerprint",
            name="uq_learning_eval_replay",
        ),
    )

    # 7. learning_evaluation_evidence_memberships
    op.create_table(
        "learning_evaluation_evidence_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "evaluation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_evaluations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_loop_evidence.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "role IN ('SUPPORTING', 'CONTRADICTING', 'CONTEXT', 'TRADEOFF')",
            name="ck_eval_evidence_role",
        ),
        sa.UniqueConstraint(
            "evaluation_id", "evidence_id", "role", name="uq_eval_evidence_membership"
        ),
    )

    # 8. learning_insights
    op.create_table(
        "learning_insights",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "evaluation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_evaluations.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "hypothesis_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_hypothesis_revisions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "policy_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_policy_revisions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("scope_context", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("causal_status", sa.String(32), nullable=False),
        sa.Column("confidence", sa.String(32), nullable=False),
        sa.Column("metric_impact", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("tradeoffs", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("limitations", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "confidence IN ('VERY_LOW', 'LOW', 'MODERATE', 'HIGH')",
            name="ck_learning_insight_confidence",
        ),
    )

    # 9. learning_recommendations
    op.create_table(
        "learning_recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "insight_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_insights.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "evaluation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_evaluations.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "policy_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_policy_revisions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_authority", sa.String(64), nullable=False),
        sa.Column("target_dimension", sa.String(64), nullable=False),
        sa.Column("scope_context", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("confidence", sa.String(32), nullable=False),
        sa.Column("ranking_score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("risk_level", sa.String(32), nullable=False, server_default="LOW"),
        sa.Column("expected_metric_effect", sa.String(64), nullable=False),
        sa.Column("safety_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "validation_requirements", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
        sa.Column("explanation", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("replay_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "action IN ('CONSIDER_TITLE_STYLE', 'CONSIDER_THUMBNAIL_STYLE', 'CONSIDER_PACING_CHANGE', 'CONSIDER_VISUAL_DENSITY', 'CONSIDER_AUDIO_DENSITY', 'CONSIDER_FORMAT_STRATEGY', 'RUN_FOLLOWUP_EXPERIMENT', 'KEEP_CURRENT_POLICY')",
            name="ck_recommendation_action",
        ),
        sa.CheckConstraint(
            "confidence IN ('VERY_LOW', 'LOW', 'MODERATE', 'HIGH')",
            name="ck_recommendation_confidence",
        ),
        sa.CheckConstraint("ranking_score >= 0.0", name="ck_recommendation_ranking_pos"),
        sa.UniqueConstraint(
            "insight_id",
            "action",
            "target_dimension",
            "replay_fingerprint",
            name="uq_recommendation_replay",
        ),
    )

    # 10. learning_candidate_adaptations
    op.create_table(
        "learning_candidate_adaptations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "recommendation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_recommendations.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("target_authority", sa.String(64), nullable=False),
        sa.Column("target_field", sa.String(64), nullable=False),
        sa.Column("current_value_context", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("proposed_value", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("scope_context", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("confidence", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PROPOSED"),
        sa.Column("validation_requirement", sa.String(255), nullable=False),
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
            "status IN ('PROPOSED', 'APPROVED', 'REJECTED', 'EXPIRED', 'SUPERSEDED')",
            name="ck_candidate_adaptation_status",
        ),
    )

    # 11. learning_adaptation_approval_history
    op.create_table(
        "learning_adaptation_approval_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_adaptation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_candidate_adaptations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("from_status", sa.String(32), nullable=False),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("transition_reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("evidence_context", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "transitioned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_adaptation_approval_hist",
        "learning_adaptation_approval_history",
        ["candidate_adaptation_id", "transitioned_at"],
    )

    # Content identities and lossless immutable domain snapshots.
    for table in (
        "learning_loop_evidence",
        "learning_hypothesis_revisions",
        "learning_insights",
        "learning_recommendations",
        "learning_candidate_adaptations",
    ):
        op.add_column(table, sa.Column("domain_snapshot", postgresql.JSONB(), nullable=False))
    op.add_column(
        "learning_policy_revisions", sa.Column("configuration", postgresql.JSONB(), nullable=False)
    )
    op.add_column(
        "learning_policy_revisions",
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
    )
    op.create_unique_constraint(
        "uq_policy_config",
        "learning_policy_revisions",
        ["policy_root_id", "configuration_fingerprint"],
    )
    op.create_check_constraint(
        "ck_policy_revision_positive",
        "learning_policy_revisions",
        "revision_number > 0 AND recency_half_life_days > 0 AND replication_threshold > 0",
    )
    op.add_column(
        "learning_hypothesis_revisions",
        sa.Column("definition_fingerprint", sa.String(64), nullable=False),
    )
    op.create_unique_constraint(
        "uq_hypothesis_definition",
        "learning_hypothesis_revisions",
        ["hypothesis_root_id", "definition_fingerprint"],
    )
    op.create_check_constraint(
        "ck_hypothesis_revision_positive", "learning_hypothesis_revisions", "revision_number > 0"
    )
    op.create_unique_constraint(
        "uq_learning_insight_evaluation", "learning_insights", ["evaluation_id"]
    )
    op.create_unique_constraint(
        "uq_learning_candidate_recommendation",
        "learning_candidate_adaptations",
        ["recommendation_id"],
    )
    op.create_check_constraint(
        "ck_learning_causal_source",
        "learning_loop_evidence",
        "causal_status != 'CAUSAL' OR (source_phase = 'P25-C' AND attribution_result_id IS NOT NULL AND evidence_tier IN ('TIER_1_CAUSAL_EXPERIMENT', 'TIER_2_REPLICATED_CAUSAL'))",
    )
    op.create_check_constraint(
        "ck_learning_descriptive_source",
        "learning_loop_evidence",
        "source_phase != 'P25-B' OR (snapshot_id IS NOT NULL AND causal_status != 'CAUSAL')",
    )
    op.execute("""CREATE FUNCTION p25d_reject_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'P25-D history is append-only: %', TG_TABLE_NAME; END $$""")
    for table in (
        "learning_policy_revisions",
        "learning_loop_evidence",
        "learning_hypothesis_revisions",
        "learning_evaluations",
        "learning_evaluation_evidence_memberships",
        "learning_insights",
        "learning_recommendations",
        "learning_adaptation_approval_history",
    ):
        op.execute(
            f"CREATE TRIGGER p25d_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION p25d_reject_history_mutation()"
        )

    op.create_unique_constraint(
        "uq_learning_eval_lineage",
        "learning_evaluations",
        ["id", "hypothesis_revision_id", "policy_revision_id"],
    )
    op.create_unique_constraint(
        "uq_learning_insight_lineage",
        "learning_insights",
        ["id", "evaluation_id", "policy_revision_id"],
    )
    op.create_foreign_key(
        "fk_learning_insight_lineage",
        "learning_insights",
        "learning_evaluations",
        ["evaluation_id", "hypothesis_revision_id", "policy_revision_id"],
        ["id", "hypothesis_revision_id", "policy_revision_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_learning_recommendation_lineage",
        "learning_recommendations",
        "learning_insights",
        ["insight_id", "evaluation_id", "policy_revision_id"],
        ["id", "evaluation_id", "policy_revision_id"],
        ondelete="RESTRICT",
    )
    op.execute("""CREATE FUNCTION p25d_candidate_boundary() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'P25-D candidate proposal is immutable'; END IF;
      IF (to_jsonb(NEW) - 'status' - 'updated_at') IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'updated_at') THEN
        RAISE EXCEPTION 'P25-D candidate proposal is immutable';
      END IF;
      IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT ((OLD.status = 'PROPOSED' AND NEW.status IN ('APPROVED','REJECTED','EXPIRED','SUPERSEDED')) OR (OLD.status = 'APPROVED' AND NEW.status IN ('EXPIRED','SUPERSEDED'))) THEN
          RAISE EXCEPTION 'Invalid candidate status transition';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM learning_adaptation_approval_history WHERE candidate_adaptation_id = OLD.id AND from_status = OLD.status AND to_status = NEW.status AND length(actor) > 0 AND length(transition_reason) > 0) THEN
          RAISE EXCEPTION 'Candidate transition requires append-only approval history';
        END IF;
      END IF;
      RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER p25d_candidate_immutable BEFORE UPDATE OR DELETE ON learning_candidate_adaptations FOR EACH ROW EXECUTE FUNCTION p25d_candidate_boundary()"
    )


def downgrade() -> None:
    op.drop_index("idx_adaptation_approval_hist", table_name="learning_adaptation_approval_history")
    op.drop_table("learning_adaptation_approval_history")
    op.drop_table("learning_candidate_adaptations")
    op.drop_table("learning_recommendations")
    op.drop_table("learning_insights")
    op.drop_table("learning_evaluation_evidence_memberships")
    op.drop_table("learning_evaluations")
    op.drop_constraint(
        "fk_hypothesis_roots_current_revision", "learning_hypothesis_roots", type_="foreignkey"
    )
    op.drop_table("learning_hypothesis_revisions")
    op.drop_table("learning_hypothesis_roots")
    op.drop_index("idx_learning_evidence_lookup", table_name="learning_loop_evidence")
    op.drop_table("learning_loop_evidence")
    op.drop_constraint(
        "fk_policy_roots_current_revision", "learning_policy_roots", type_="foreignkey"
    )
    op.drop_table("learning_policy_revisions")
    op.drop_table("learning_policy_roots")

    op.execute("DROP FUNCTION p25d_reject_history_mutation()")
    op.execute("DROP FUNCTION p25d_candidate_boundary()")
