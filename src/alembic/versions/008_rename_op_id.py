"""Rename op_id to archive_op_id.

Revision ID: 008_rename_op_id
Revises: 007_optimize_indexes
Create Date: 2026-01-14

Clarifies that op_id is specifically for archiving operations (ARCHIVING, CREATE_EMPTY_ARCHIVE).
This column stores the operation ID used for S3 path construction during archiving.

Reference: docs/spec/04-control-plane.md
"""

from alembic import op

revision = '008_rename_op_id'
down_revision = '007_optimize_indexes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('workspaces', 'op_id', new_column_name='archive_op_id')


def downgrade() -> None:
    op.alter_column('workspaces', 'archive_op_id', new_column_name='op_id')
