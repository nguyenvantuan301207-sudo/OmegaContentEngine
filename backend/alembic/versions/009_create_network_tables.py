"""009 — Create Network Manager tables and constraints.

Revision ID: 009
Revises: 008
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. network_profiles ──
    op.create_table(
        "network_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
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

    # ── 2. network_routes ──
    op.create_table(
        "network_routes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("network_profiles.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("route_type", sa.String(50), nullable=False),
        sa.Column("endpoint_url", sa.String(255), nullable=True),
        sa.Column("credential_ref", sa.String(100), nullable=True),
        sa.Column(
            "allowed_service_categories",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("tls_verify", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("connect_timeout_seconds", sa.Float(), nullable=False, server_default="5.0"),
        sa.Column("read_timeout_seconds", sa.Float(), nullable=False, server_default="10.0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("priority_weight", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config_checksum", sa.String(64), nullable=False),
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
        sa.CheckConstraint("tls_verify = true", name="ck_network_route_tls_verify_mandatory"),
        sa.CheckConstraint(
            "route_type IN ('DIRECT', 'HTTPS_CONNECT_PROXY', 'SOCKS5', 'SYSTEM_DEFAULT')",
            name="ck_network_route_allowed_types",
        ),
    )

    # ── 3. network_policies ──
    op.create_table(
        "network_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("service_category", sa.String(50), nullable=False, index=True),
        sa.Column("version", sa.String(50), nullable=False, index=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="DRAFT", index=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "policy_config",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("service_category", "version", name="uq_network_policy_category_version"),
    )

    # ── 4. network_circuit_states ──
    op.create_table(
        "network_circuit_states",
        sa.Column(
            "route_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("network_routes.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("service_category", sa.String(50), primary_key=True),
        sa.Column("state", sa.String(50), nullable=False, server_default="CLOSED", index=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("half_open_successes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("half_open_lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("half_open_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── 5. network_checks ──
    op.create_table(
        "network_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("missions.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "route_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("network_routes.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("route_config_version", sa.Integer(), nullable=False),
        sa.Column("route_config_checksum", sa.String(64), nullable=False),
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("network_policies.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("policy_version", sa.String(50), nullable=False),
        sa.Column("policy_checksum", sa.String(64), nullable=False),
        sa.Column("service_category", sa.String(50), nullable=False, index=True),
        sa.Column("canonical_destination", sa.String(500), nullable=False, index=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="PENDING", index=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── 6. network_probe_runs ──
    op.create_table(
        "network_probe_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "check_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("network_checks.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("probe_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── 7. network_decisions ──
    op.create_table(
        "network_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "network_check_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("network_checks.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("action", sa.String(50), nullable=False, index=True),
        sa.Column("resulting_health_state", sa.String(50), nullable=False, index=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False, server_default="NETWORK_MANAGER"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("network_check_id", name="uq_network_decision_check"),
    )

    # ── 8. network_circuit_transitions ──
    op.create_table(
        "network_circuit_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "route_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("network_routes.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("service_category", sa.String(50), nullable=False),
        sa.Column("from_state", sa.String(50), nullable=False),
        sa.Column("to_state", sa.String(50), nullable=False),
        sa.Column("trigger_reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("network_circuit_transitions")
    op.drop_table("network_decisions")
    op.drop_table("network_probe_runs")
    op.drop_table("network_checks")
    op.drop_table("network_circuit_states")
    op.drop_table("network_policies")
    op.drop_table("network_routes")
    op.drop_table("network_profiles")
