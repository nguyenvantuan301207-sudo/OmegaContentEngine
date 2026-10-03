"""027 — Create NarrativePlan and Lineage Tables.

Revision ID: 027
Revises: 026
Create Date: 2026-10-03

Creates authoritative database persistence for P21-A Narrative Architecture:
1. narrative_plans — First-class pre-script story structure authority with partial unique constraint
   guaranteeing exactly one current plan per ContentGenerationRequest.
2. narrative_sections — Normalized ordered narrative sections with promise_id uniqueness.
3. narrative_grounding_citations — Normalized relational grounding referencing ResearchBrief, Claim, Evidence, and Source.
4. script_versions.narrative_plan_id — Immutable relational foreign key linking script versions to exact narrative plans.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create narrative_plans
    op.create_table(
        "narrative_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "content_generation_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_generation_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "topic_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("topic_candidates.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "research_brief_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_briefs.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "channel_dna_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "supersedes_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("narrative_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="DRAFT"),
        sa.Column("format_profile", sa.String(50), nullable=False, server_default="MEDIUM"),
        sa.Column("target_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("estimated_duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
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
            "content_generation_request_id",
            "version",
            name="uq_narrative_plan_version",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'VALIDATED', 'APPROVED', 'REJECTED', 'SUPERSEDED')",
            name="chk_narrative_plan_status",
        ),
        sa.CheckConstraint(
            "format_profile IN ('SHORT', 'MEDIUM', 'LONG')",
            name="chk_narrative_plan_format_profile",
        ),
        sa.CheckConstraint(
            "target_duration_seconds > 0",
            name="chk_narrative_plan_target_duration",
        ),
        sa.CheckConstraint(
            "estimated_duration_seconds >= 0",
            name="chk_narrative_plan_estimated_duration",
        ),
    )

    # CRITICAL: Database-enforced partial unique constraint guaranteeing at most ONE current plan per request
    op.create_index(
        "uq_narrative_plan_single_current",
        "narrative_plans",
        ["content_generation_request_id"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )
    op.create_index(
        "ix_narrative_plans_status",
        "narrative_plans",
        ["status"],
    )

    # 2. Create narrative_sections
    op.create_table(
        "narrative_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "narrative_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("narrative_plans.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("section_order", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("objective", sa.String(500), nullable=False),
        sa.Column("key_information", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("target_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("target_information_density", sa.String(20), nullable=False, server_default="MEDIUM"),
        sa.Column("open_loop_intent", sa.String(300), nullable=True),
        sa.Column("promise_id", sa.String(100), nullable=True),
        sa.Column("payoff_reference", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.UniqueConstraint(
            "narrative_plan_id",
            "section_order",
            name="uq_narrative_section_order",
        ),
        sa.CheckConstraint(
            "section_order > 0",
            name="chk_narrative_section_order",
        ),
        sa.CheckConstraint(
            "target_duration_seconds > 0",
            name="chk_narrative_section_duration",
        ),
        sa.CheckConstraint(
            "role IN ('HOOK', 'PROMISE', 'CONTEXT', 'DEVELOPMENT', 'ESCALATION', 'PAYOFF', 'TAKEAWAY', 'CLOSING', 'CTA')",
            name="chk_narrative_section_role",
        ),
        sa.CheckConstraint(
            "target_information_density IN ('LOW', 'MEDIUM', 'HIGH')",
            name="chk_narrative_section_density",
        ),
    )

    # Partial unique index ensuring promise_id is unique within a plan
    op.create_index(
        "uq_narrative_section_promise_id",
        "narrative_sections",
        ["narrative_plan_id", "promise_id"],
        unique=True,
        postgresql_where=sa.text("promise_id IS NOT NULL"),
    )
    op.create_index(
        "ix_narrative_sections_payoff_ref",
        "narrative_sections",
        ["narrative_plan_id", "payoff_reference"],
        postgresql_where=sa.text("payoff_reference IS NOT NULL"),
    )

    # 3. Create narrative_grounding_citations
    op.create_table(
        "narrative_grounding_citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "narrative_section_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("narrative_sections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "research_brief_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_briefs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_claims.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claim_evidence.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_sources.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("grounding_type", sa.String(50), nullable=False, server_default="FACTUAL"),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "grounding_type IN ('FACTUAL', 'BACKGROUND', 'DATA_POINT', 'QUOTE')",
            name="chk_narrative_grounding_type",
        ),
    )

    # 4. Add nullable narrative_plan_id to script_versions
    op.add_column(
        "script_versions",
        sa.Column(
            "narrative_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("narrative_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_script_versions_narrative_plan_id",
        "script_versions",
        ["narrative_plan_id"],
        postgresql_where=sa.text("narrative_plan_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Drop script_versions lineage
    op.drop_index("ix_script_versions_narrative_plan_id", table_name="script_versions")
    op.drop_column("script_versions", "narrative_plan_id")

    # Drop narrative_grounding_citations
    op.drop_table("narrative_grounding_citations")

    # Drop narrative_sections
    op.drop_index("ix_narrative_sections_payoff_ref", table_name="narrative_sections")
    op.drop_index("uq_narrative_section_promise_id", table_name="narrative_sections")
    op.drop_table("narrative_sections")

    # Drop narrative_plans
    op.drop_index("ix_narrative_plans_status", table_name="narrative_plans")
    op.drop_index("uq_narrative_plan_single_current", table_name="narrative_plans")
    op.drop_table("narrative_plans")
