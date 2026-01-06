"""Drop TTL columns from workspaces table.

Revision ID: 004_drop_ttl_columns
Revises: 003_phase_changed_at
Create Date: 2026-01-04

TTL settings moved from per-workspace DB columns to global environment variables.
- TTL_STANDBY_SECONDS: RUNNING -> STANDBY (default: 300)
- TTL_ARCHIVE_SECONDS: STANDBY -> ARCHIVED (default: 1800)

Reference: docs/architecture_v2/ttl-manager.md
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = '004_drop_ttl_columns'
down_revision = '003_phase_changed_at'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column('workspaces', 'standby_ttl_seconds')
    op.drop_column('workspaces', 'archive_ttl_seconds')


def downgrade() -> None:
    op.add_column(
        'workspaces',
        sa.Column('standby_ttl_seconds', sa.Integer(), nullable=False, server_default='300')
    )
    op.add_column(
        'workspaces',
        sa.Column('archive_ttl_seconds', sa.Integer(), nullable=False, server_default='86400')
    )
