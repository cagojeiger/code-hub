"""Add restore_op_id column for restoring idempotency.

Revision ID: 009_add_restore_op_id
Revises: 008_rename_op_id
Create Date: 2026-01-25

Adds restore_op_id column for symmetric handling with archive_op_id.
This column stores the operation ID for restoring operations,
enabling proper Dual Check (op_id matching) like archiving.

Previously, restore_op_id was stored in home_ctx.restore_marker (JSON field).
This migration adds a dedicated column for consistency and query efficiency.

Reference: docs/spec/05-data-plane.md
"""

from alembic import op
import sqlalchemy as sa


revision = '009_add_restore_op_id'
down_revision = '008_rename_op_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new column
    op.add_column(
        'workspaces',
        sa.Column('restore_op_id', sa.String(36), nullable=True)
    )

    # Migrate existing data from home_ctx.restore_marker
    # This extracts restore_marker from JSONB and copies to new column
    op.execute("""
        UPDATE workspaces
        SET restore_op_id = home_ctx->>'restore_marker'
        WHERE home_ctx->>'restore_marker' IS NOT NULL
    """)

    # Remove restore_marker from home_ctx (cleanup)
    op.execute("""
        UPDATE workspaces
        SET home_ctx = home_ctx - 'restore_marker'
        WHERE home_ctx ? 'restore_marker'
    """)


def downgrade() -> None:
    # Migrate restore_op_id back to home_ctx.restore_marker
    op.execute("""
        UPDATE workspaces
        SET home_ctx = COALESCE(home_ctx, '{}'::jsonb) ||
            jsonb_build_object('restore_marker', restore_op_id)
        WHERE restore_op_id IS NOT NULL
    """)

    op.drop_column('workspaces', 'restore_op_id')
