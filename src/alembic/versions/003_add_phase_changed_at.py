"""Add phase_changed_at column for archive TTL tracking.

Revision ID: 003_phase_changed_at
Revises: 002_workspace_notify
Create Date: 2026-01-04

Adds phase_changed_at column to track when phase transitions occur.
Used by TTL Manager to calculate archive_ttl (STANDBY -> ARCHIVED).

Reference: docs/architecture_v2/ttl-manager.md
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic
revision = '003_phase_changed_at'
down_revision = '002_workspace_notify'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'workspaces',
        sa.Column('phase_changed_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('workspaces', 'phase_changed_at')
