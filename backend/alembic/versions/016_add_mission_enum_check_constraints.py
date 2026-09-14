"""016 — Add NOT VALID check constraints for mission state and autonomy_level.

Revision ID: 016
Revises: 015
Create Date: 2026-09-14
"""

from __future__ import annotations

from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add NOT VALID check constraint for state to preserve historical forensic rows
    op.execute(
        """
        ALTER TABLE missions
        ADD CONSTRAINT ck_missions_state
        CHECK (state IN ('DRAFT', 'READY', 'RUNNING', 'PAUSED', 'SUCCEEDED', 'FAILED', 'CANCELLED'))
        NOT VALID;
        """
    )
    # Add NOT VALID check constraint for autonomy_level to preserve historical forensic rows
    op.execute(
        """
        ALTER TABLE missions
        ADD CONSTRAINT ck_missions_autonomy_level
        CHECK (autonomy_level IN ('MANUAL', 'ASSISTED', 'SUPERVISED', 'AUTONOMOUS', 'STRATEGIC_AUTONOMOUS'))
        NOT VALID;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE missions DROP CONSTRAINT IF EXISTS ck_missions_autonomy_level;")
    op.execute("ALTER TABLE missions DROP CONSTRAINT IF EXISTS ck_missions_state;")
