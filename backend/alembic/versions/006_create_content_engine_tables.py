"""006 — Create Content Engine tables.

Revision ID: 006
Revises: 005
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. content_generation_requests ──
    op.create_table(
        "content_generation_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "topic_candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("topic_candidates.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "research_brief_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_briefs.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "channel_dna_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "mission_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mission_executions.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("mode", sa.String(50), nullable=False, server_default="INTERACTIVE"),
        sa.Column("status", sa.String(50), nullable=False, index=True, server_default="DRAFT"),
        sa.Column("outcome", sa.String(50), nullable=True),
        sa.Column("content_type", sa.String(50), nullable=False, server_default="YOUTUBE_LONGFORM"),
        sa.Column("target_duration_seconds", sa.Integer(), nullable=False, server_default="480"),
        sa.Column("target_word_count", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(20), nullable=False, server_default="en"),
        sa.Column("region", sa.String(10), nullable=False, server_default="US"),
        sa.Column("creative_direction", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
    )

    # ── 2. content_intents ──
    op.create_table(
        "content_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "content_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_generation_requests.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("primary_goal", sa.Text(), nullable=False),
        sa.Column("audience_intent", sa.Text(), nullable=False),
        sa.Column("viewer_promise", sa.Text(), nullable=False),
        sa.Column("central_question", sa.Text(), nullable=False),
        sa.Column("core_takeaway", sa.Text(), nullable=False),
        sa.Column("tone", sa.String(100), nullable=False),
        sa.Column("pace", sa.String(100), nullable=False),
        sa.Column("complexity", sa.String(100), nullable=False),
        sa.Column("desired_emotion", sa.String(100), nullable=False),
        sa.Column("call_to_action_type", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── 3. content_hooks ──
    op.create_table(
        "content_hooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "content_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_generation_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("hook_variant_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("hook_type", sa.String(50), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("reason_codes", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Partial unique index: at most one hook selected per content request
    op.create_index(
        "uq_content_hook_selected",
        "content_hooks",
        ["content_request_id"],
        unique=True,
        postgresql_where=sa.text("selected = true"),
    )

    # ── 4. content_hook_citations ──
    op.create_table(
        "content_hook_citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "hook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_hooks.id", ondelete="CASCADE"),
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
            nullable=False,
            index=True,
        ),
        sa.Column(
            "evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claim_evidence.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── 5. content_outlines ──
    op.create_table(
        "content_outlines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "content_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_generation_requests.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("opening_description", sa.Text(), nullable=False),
        sa.Column("sections", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("closing_description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── 6. script_versions ──
    op.create_table(
        "script_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "content_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_generation_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default="true", index=True),
        sa.Column(
            "supersedes_script_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("script_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column(
            "hook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_hooks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("hook_text", sa.Text(), nullable=False),
        sa.Column("closing_text", sa.Text(), nullable=False),
        sa.Column("cta_text", sa.Text(), nullable=False),
        sa.Column("estimated_word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("qa_status", sa.String(50), nullable=False, server_default="PENDING", index=True),
        sa.Column("style_snapshot", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("content_request_id", "version", name="uq_script_version_number"),
    )
    # Partial unique index: at most one current script version per content request
    op.create_index(
        "uq_script_version_current",
        "script_versions",
        ["content_request_id"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    # ── 7. script_sections ──
    op.create_table(
        "script_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "script_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("script_versions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("section_order", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(255), nullable=False),
        sa.Column("narration_text", sa.Text(), nullable=False),
        sa.Column("estimated_duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transition_text", sa.Text(), nullable=True),
        sa.Column("retention_beat", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── 8. script_statements ──
    op.create_table(
        "script_statements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "script_section_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("script_sections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("statement_order", sa.Integer(), nullable=False),
        sa.Column("statement_text", sa.Text(), nullable=False),
        sa.Column("statement_type", sa.String(50), nullable=False, index=True),
        sa.Column("qualification_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── 9. content_citations ──
    op.create_table(
        "content_citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "script_statement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("script_statements.id", ondelete="CASCADE"),
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
            nullable=False,
            index=True,
        ),
        sa.Column(
            "evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claim_evidence.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("research_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── 10. content_qa_results ──
    op.create_table(
        "content_qa_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "script_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("script_versions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("status", sa.String(50), nullable=False, index=True, server_default="PENDING"),
        sa.Column("findings", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("content_qa_results")
    op.drop_table("content_citations")
    op.drop_table("script_statements")
    op.drop_table("script_sections")
    op.drop_index("uq_script_version_current", table_name="script_versions")
    op.drop_table("script_versions")
    op.drop_table("content_outlines")
    op.drop_table("content_hook_citations")
    op.drop_index("uq_content_hook_selected", table_name="content_hooks")
    op.drop_table("content_hooks")
    op.drop_table("content_intents")
    op.drop_table("content_generation_requests")
