"""011 — Create Publisher tables and constraints.

Revision ID: 011
Revises: 010
Create Date: 2026-08-30

Creates nine tables for the OMEGA-011 Publisher:
1. platform_accounts — Authoritative registry of connected external accounts
2. credential_vault — Encrypted OAuth refresh/access token storage
3. oauth_authorization_sessions — State-hash & PKCE verification for OAuth flow
4. publish_intents — Immutable publication contract & claim lease fencing
5. publish_intent_transitions — Append-only audit trail for intent states
6. publish_attempts — Authoritative log of external publishing executions
7. publish_attempt_transitions — Append-only audit trail for attempt states
8. upload_sessions — Resumable upload session URIs & byte progression
9. publisher_scheduler_handoff_outbox — Durable cross-service retry handoff to OMEGA-010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. platform_accounts ──
    op.create_table(
        "platform_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("account_display_name", sa.String(255), nullable=False),
        sa.Column("external_account_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column("scopes", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("channel_id", "platform", name="uq_platform_accounts_channel_platform"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'EXPIRED', 'REVOKED')",
            name="chk_platform_accounts_status",
        ),
    )

    # ── 2. credential_vault ──
    op.create_table(
        "credential_vault",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "platform_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_accounts.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("token_type", sa.String(32), nullable=False, server_default="Bearer"),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("key_version > 0", name="chk_credential_vault_key_version"),
    )

    # ── 3. oauth_authorization_sessions ──
    op.create_table(
        "oauth_authorization_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("encrypted_pkce_verifier", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.String(512), nullable=False),
        sa.Column("requested_scopes", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("expires_at > created_at", name="chk_oauth_sessions_expiry"),
    )
    op.create_index(
        "idx_oauth_sessions_state",
        "oauth_authorization_sessions",
        ["state_hash", "expires_at"],
    )

    # ── 4. publish_intents ──
    op.create_table(
        "publish_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="RESTRICT"),
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
        sa.Column(
            "platform_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_accounts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "media_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("media_artifact_checksum", sa.String(64), nullable=False),
        sa.Column(
            "channel_dna_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_dna_revisions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "supersedes_intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publish_intents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "requested_privacy_status", sa.String(32), nullable=False, server_default="PRIVATE"
        ),
        sa.Column("category_id", sa.String(32), nullable=False, server_default="28"),
        sa.Column("made_for_kids", sa.Boolean(), nullable=False),
        sa.Column("platform_custom_options", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("intent_checksum", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="APPROVED"),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_by_worker_id", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("task_id", "revision_number", name="uq_publish_intents_task_revision"),
        sa.CheckConstraint(
            "state IN ('DRAFT', 'APPROVED', 'CLAIMED', 'PUBLISHED', 'FAILED', 'SUPERSEDED', 'CANCELLED')",
            name="chk_publish_intents_state",
        ),
        sa.CheckConstraint("revision_number >= 1", name="chk_publish_intents_revision_number"),
        sa.CheckConstraint(
            "attempt_generation >= 0", name="chk_publish_intents_attempt_generation"
        ),
    )
    op.create_index(
        "idx_publish_intents_active_claim",
        "publish_intents",
        ["state", "lease_expires_at"],
    )
    op.create_index(
        "uix_active_intent_per_task",
        "publish_intents",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('APPROVED', 'CLAIMED')"),
    )

    # ── 5. publish_intent_transitions ──
    op.create_table(
        "publish_intent_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "publish_intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publish_intents.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("from_state", sa.String(32), nullable=False),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_intent_transitions_intent",
        "publish_intent_transitions",
        ["publish_intent_id", "created_at"],
    )

    # ── 6. publish_attempts ──
    op.create_table(
        "publish_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "publish_intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publish_intents.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="CREATED"),
        sa.Column("provider_video_id", sa.String(128), nullable=True),
        sa.Column("provider_url", sa.String(512), nullable=True),
        sa.Column("effective_privacy_status", sa.String(32), nullable=True),
        sa.Column("error_category", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
        sa.Column("reconciliation_status", sa.String(32), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('CREATED', 'UPLOADING', 'FINALIZING', 'SUCCEEDED', 'RETRYABLE_FAILED', 'PERMANENT_FAILED', 'UNKNOWN', 'BLOCKED_GUARDIAN', 'CANCELLED')",
            name="chk_publish_attempts_state",
        ),
    )
    op.create_index(
        "idx_publish_attempts_intent",
        "publish_attempts",
        ["publish_intent_id", "attempt_number"],
    )

    # ── 7. publish_attempt_transitions ──
    op.create_table(
        "publish_attempt_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "publish_attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publish_attempts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("from_state", sa.String(32), nullable=False),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_attempt_transitions_attempt",
        "publish_attempt_transitions",
        ["publish_attempt_id", "created_at"],
    )

    # ── 8. upload_sessions ──
    op.create_table(
        "upload_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "publish_attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publish_attempts.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("session_uri", sa.Text(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("bytes_uploaded", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("chunk_size_bytes", sa.Integer(), nullable=False, server_default="8388608"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("total_bytes > 0", name="chk_upload_sessions_total_bytes"),
        sa.CheckConstraint("bytes_uploaded >= 0", name="chk_upload_sessions_bytes_uploaded"),
    )
    op.create_index(
        "idx_upload_sessions_expires",
        "upload_sessions",
        ["expires_at"],
    )

    # ── 9. publisher_scheduler_handoff_outbox ──
    op.create_table(
        "publisher_scheduler_handoff_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "publish_intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publish_intents.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "publish_attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("publish_attempts.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("earliest_retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_by_worker_id", sa.String(128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CLAIMED', 'DELIVERED', 'DEAD_LETTER')",
            name="chk_pub_sched_outbox_status",
        ),
    )
    op.create_index(
        "idx_pub_sched_outbox_sweep",
        "publisher_scheduler_handoff_outbox",
        ["status", "next_attempt_at", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_table("publisher_scheduler_handoff_outbox")
    op.drop_table("upload_sessions")
    op.drop_table("publish_attempt_transitions")
    op.drop_table("publish_attempts")
    op.drop_table("publish_intent_transitions")
    op.drop_table("publish_intents")
    op.drop_table("oauth_authorization_sessions")
    op.drop_table("credential_vault")
    op.drop_table("platform_accounts")
