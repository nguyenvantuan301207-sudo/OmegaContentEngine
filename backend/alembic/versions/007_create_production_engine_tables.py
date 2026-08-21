"""007 — Create Production Engine tables.

Revision ID: 007
Revises: 006
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. production_requests ──
    op.create_table(
        "production_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "script_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("script_versions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "content_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_generation_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "channel_dna_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_dna_revisions.id", ondelete="CASCADE"),
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
        sa.Column("outcome", sa.String(50), nullable=True, index=True),
        sa.Column("target_width", sa.Integer(), nullable=False, server_default="1920"),
        sa.Column("target_height", sa.Integer(), nullable=False, server_default="1080"),
        sa.Column("fps", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("video_codec", sa.String(30), nullable=False, server_default="h264"),
        sa.Column("audio_codec", sa.String(30), nullable=False, server_default="aac"),
        sa.Column("container_format", sa.String(20), nullable=False, server_default="mp4"),
        sa.Column("voice_profile", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("metadata", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── 2. production_scenes ──
    op.create_table(
        "production_scenes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "production_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("scene_order", sa.Integer(), nullable=False),
        sa.Column(
            "script_section_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("script_sections.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "start_statement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("script_statements.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "end_statement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("script_statements.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("scene_type", sa.String(50), nullable=False, index=True),
        sa.Column("narration_text", sa.Text(), nullable=False),
        sa.Column("estimated_duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visual_intent", sa.Text(), nullable=True),
        sa.Column("transition_in", sa.String(50), nullable=True),
        sa.Column("transition_out", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("production_request_id", "scene_order", name="uq_production_scene_order"),
    )

    # ── 3. asset_requirements ──
    op.create_table(
        "asset_requirements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "scene_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_scenes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("asset_type", sa.String(50), nullable=False, index=True),
        sa.Column("purpose", sa.String(100), nullable=False),
        sa.Column("query_hint", sa.Text(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("status", sa.String(50), nullable=False, index=True, server_default="PENDING"),
        sa.Column("license_requirement", sa.String(50), nullable=False, server_default="COMMERCIAL_ALLOWED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # ── 4. production_assets ──
    op.create_table(
        "production_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "production_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "asset_requirement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("asset_requirements.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("asset_type", sa.String(50), nullable=False, index=True),
        sa.Column("provider_type", sa.String(50), nullable=False, server_default="PLACEHOLDER"),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, index=True),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("license_status", sa.String(50), nullable=False, index=True, server_default="GENERATED"),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("attribution", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # ── 5. narration_segments ──
    op.create_table(
        "narration_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "production_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "scene_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_scenes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "audio_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_assets.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # ── 6. subtitle_cues ──
    op.create_table(
        "subtitle_cues",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "production_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "scene_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_scenes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("cue_order", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("production_request_id", "cue_order", name="uq_subtitle_cue_order"),
    )

    # ── 7. render_plans ──
    op.create_table(
        "render_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "production_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("width", sa.Integer(), nullable=False, server_default="1920"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="1080"),
        sa.Column("fps", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("video_codec", sa.String(30), nullable=False, server_default="h264"),
        sa.Column("audio_codec", sa.String(30), nullable=False, server_default="aac"),
        sa.Column("container", sa.String(20), nullable=False, server_default="mp4"),
        sa.Column("total_duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scene_manifest", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("audio_manifest", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("subtitle_manifest", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("production_request_id", "version", name="uq_render_plan_version"),
    )

    # ── 8. production_render_jobs ──
    op.create_table(
        "production_render_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "production_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "render_plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("render_plans.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False, index=True),
        sa.Column("state", sa.String(50), nullable=False, index=True, server_default="PENDING"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("sanitized_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("production_request_id", "idempotency_key", name="uq_production_render_job_idempotency"),
    )

    # ── 9. media_artifacts ──
    op.create_table(
        "media_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "production_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "render_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_render_jobs.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("artifact_type", sa.String(50), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default="true", index=True),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, index=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("production_request_id", "artifact_type", "version", name="uq_media_artifact_version"),
    )

    # Partial Unique Index: at most one current video per production request
    op.create_index(
        "uq_media_artifact_current_video",
        "media_artifacts",
        ["production_request_id", "artifact_type"],
        unique=True,
        postgresql_where=sa.text("is_current = true AND artifact_type = 'VIDEO'"),
    )

    # ── 10. production_qa_results ──
    op.create_table(
        "production_qa_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "production_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("production_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_artifacts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("status", sa.String(50), nullable=False, index=True, server_default="PENDING"),
        sa.Column("findings", postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("production_qa_results")
    op.drop_index("uq_media_artifact_current_video", table_name="media_artifacts")
    op.drop_table("media_artifacts")
    op.drop_table("production_render_jobs")
    op.drop_table("render_plans")
    op.drop_table("subtitle_cues")
    op.drop_table("narration_segments")
    op.drop_table("production_assets")
    op.drop_table("asset_requirements")
    op.drop_table("production_scenes")
    op.drop_table("production_requests")
