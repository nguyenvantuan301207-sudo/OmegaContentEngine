"""024 - Add lazy campaign runtime orchestration authorities.

Revision ID: 024
Revises: 023
Create Date: 2026-09-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_content_campaigns_status", "content_campaigns", type_="check")
    op.add_column("content_campaigns", sa.Column("orchestration_mode", sa.String(32), nullable=False, server_default="LEGACY_UPFRONT"))
    op.add_column("content_campaigns", sa.Column("plan_checksum_version", sa.SmallInteger(), nullable=False, server_default="1"))
    op.add_column("content_campaigns", sa.Column("max_concurrent_missions", sa.Integer(), nullable=True))
    for name in ("started_at", "paused_at", "completed_at", "cancelled_at", "last_admitted_at", "archived_at"):
        op.add_column("content_campaigns", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_content_campaigns_status", "content_campaigns", "status IN ('READY','RUNNING','PAUSED','CANCELLING','SUCCEEDED','PARTIAL','FAILED','CANCELLED')")
    op.create_check_constraint("ck_content_campaigns_orchestration_mode", "content_campaigns", "orchestration_mode IN ('LEGACY_UPFRONT','LAZY_ADMISSION_V1')")
    op.create_check_constraint("ck_content_campaigns_max_concurrency", "content_campaigns", "max_concurrent_missions IS NULL OR max_concurrent_missions >= 1")
    op.create_index("ix_content_campaigns_channel_mode_status", "content_campaigns", ["channel_id", "orchestration_mode", "status"])
    op.create_index("ix_content_campaigns_admission_order", "content_campaigns", ["status", "last_admitted_at", "priority", "created_at"])

    op.add_column("content_campaign_items", sa.Column("item_key", sa.String(128), nullable=True))
    op.execute("UPDATE content_campaign_items SET item_key = 'selection-run:' || selection_run_id::text")
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM content_campaign_items WHERE item_key IS NULL) OR EXISTS (SELECT 1 FROM content_campaign_items GROUP BY campaign_id,item_key HAVING count(*) > 1) THEN RAISE EXCEPTION 'campaign item_key backfill invariant failed'; END IF; END $$")
    op.alter_column("content_campaign_items", "item_key", nullable=False)
    op.add_column("content_campaign_items", sa.Column("admission_state", sa.String(32), nullable=False, server_default="PENDING"))
    op.add_column("content_campaign_items", sa.Column("materialization_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("content_campaign_items", sa.Column("materialization_error_code", sa.String(64), nullable=True))
    op.add_column("content_campaign_items", sa.Column("sanitized_materialization_error", sa.String(500), nullable=True))
    op.add_column("content_campaign_items", sa.Column("next_materialization_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("content_campaign_items", sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("content_campaign_items", sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE content_campaign_items i SET admission_state='MATERIALIZED', materialized_at=COALESCE((SELECT created_at FROM content_campaign_item_executions e WHERE e.campaign_item_id=i.id), now()) WHERE EXISTS (SELECT 1 FROM content_campaign_item_executions e WHERE e.campaign_item_id=i.id)")
    op.create_check_constraint("ck_content_campaign_items_admission_state", "content_campaign_items", "admission_state IN ('PENDING','ADMITTED','MATERIALIZED','FAILED','CANCELLED')")
    op.create_check_constraint("ck_content_campaign_items_attempts", "content_campaign_items", "materialization_attempts >= 0")
    op.create_unique_constraint("uq_content_campaign_items_campaign_item_key", "content_campaign_items", ["campaign_id", "item_key"])
    op.create_index("ix_content_campaign_items_pending", "content_campaign_items", ["campaign_id", "position"], postgresql_where=sa.text("admission_state = 'PENDING'"))
    op.create_index("ix_content_campaign_items_admitted_recovery", "content_campaign_items", ["campaign_id", "next_materialization_attempt_at", "position"], postgresql_where=sa.text("admission_state = 'ADMITTED'"))

    op.drop_constraint("ck_content_campaign_executions_status", "content_campaign_executions", type_="check")
    op.add_column("content_campaign_executions", sa.Column("materialization_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_content_campaign_executions_status", "content_campaign_executions", "status IN ('ACTIVE','MATERIALIZED','CANCELLED')")


def downgrade() -> None:
    op.execute("UPDATE content_campaign_executions SET status='MATERIALIZED' WHERE status <> 'MATERIALIZED'")
    op.drop_constraint("ck_content_campaign_executions_status", "content_campaign_executions", type_="check")
    op.drop_column("content_campaign_executions", "materialization_completed_at")
    op.create_check_constraint("ck_content_campaign_executions_status", "content_campaign_executions", "status = 'MATERIALIZED'")
    op.drop_index("ix_content_campaign_items_admitted_recovery", table_name="content_campaign_items")
    op.drop_index("ix_content_campaign_items_pending", table_name="content_campaign_items")
    op.drop_constraint("uq_content_campaign_items_campaign_item_key", "content_campaign_items", type_="unique")
    op.drop_constraint("ck_content_campaign_items_attempts", "content_campaign_items", type_="check")
    op.drop_constraint("ck_content_campaign_items_admission_state", "content_campaign_items", type_="check")
    for name in ("materialized_at", "admitted_at", "next_materialization_attempt_at", "sanitized_materialization_error", "materialization_error_code", "materialization_attempts", "admission_state", "item_key"):
        op.drop_column("content_campaign_items", name)
    op.execute("UPDATE content_campaigns SET status='READY' WHERE status <> 'READY'")
    op.drop_index("ix_content_campaigns_admission_order", table_name="content_campaigns")
    op.drop_index("ix_content_campaigns_channel_mode_status", table_name="content_campaigns")
    op.drop_constraint("ck_content_campaigns_max_concurrency", "content_campaigns", type_="check")
    op.drop_constraint("ck_content_campaigns_orchestration_mode", "content_campaigns", type_="check")
    op.drop_constraint("ck_content_campaigns_status", "content_campaigns", type_="check")
    for name in ("archived_at", "last_admitted_at", "cancelled_at", "completed_at", "paused_at", "started_at", "max_concurrent_missions", "plan_checksum_version", "orchestration_mode"):
        op.drop_column("content_campaigns", name)
    op.create_check_constraint("ck_content_campaigns_status", "content_campaigns", "status = 'READY'")
