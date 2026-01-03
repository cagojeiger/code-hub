"""M2 workspace schema migration.

Revision ID: 001_m2_workspace
Revises:
Create Date: 2026-01-03

Updates workspaces table from M1 to M2 schema:
- Remove old 'status' column
- Add state machine columns (phase, operation, desired_state, etc.)
- Add TTL and archive columns
- Add new indexes for reconciler/TTL queries
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic
revision = '001_m2_workspace'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old status column and enum type
    op.drop_column('workspaces', 'status')
    op.execute('DROP TYPE IF EXISTS workspacestatus')

    # Clear home_ctx (old paths not valid for M2) and change to JSONB
    op.execute('UPDATE workspaces SET home_ctx = NULL')
    op.execute('ALTER TABLE workspaces ALTER COLUMN home_ctx TYPE JSONB USING NULL::jsonb')

    # Add M2 state machine columns
    op.add_column('workspaces', sa.Column(
        'conditions', postgresql.JSONB(),
        nullable=False, server_default='{}'
    ))
    op.add_column('workspaces', sa.Column(
        'phase', sa.String(), nullable=False, server_default='PENDING'
    ))
    op.add_column('workspaces', sa.Column(
        'operation', sa.String(), nullable=False, server_default='NONE'
    ))
    op.add_column('workspaces', sa.Column(
        'op_started_at', sa.DateTime(timezone=True), nullable=True
    ))
    op.add_column('workspaces', sa.Column(
        'op_id', sa.String(), nullable=True
    ))
    op.add_column('workspaces', sa.Column(
        'desired_state', sa.String(), nullable=False, server_default='RUNNING'
    ))
    op.add_column('workspaces', sa.Column(
        'archive_key', sa.String(512), nullable=True
    ))
    op.add_column('workspaces', sa.Column(
        'observed_at', sa.DateTime(timezone=True), nullable=True
    ))
    op.add_column('workspaces', sa.Column(
        'last_access_at', sa.DateTime(timezone=True), nullable=True
    ))
    op.add_column('workspaces', sa.Column(
        'standby_ttl_seconds', sa.Integer(), nullable=False, server_default='300'
    ))
    op.add_column('workspaces', sa.Column(
        'archive_ttl_seconds', sa.Integer(), nullable=False, server_default='86400'
    ))
    op.add_column('workspaces', sa.Column(
        'error_reason', sa.String(), nullable=True
    ))
    op.add_column('workspaces', sa.Column(
        'error_count', sa.Integer(), nullable=False, server_default='0'
    ))

    # Create partial indexes for efficient queries
    op.create_index(
        'idx_workspaces_ttl_check',
        'workspaces',
        ['phase', 'operation'],
        postgresql_where="deleted_at IS NULL AND phase IN ('RUNNING', 'STANDBY') AND operation = 'NONE'"
    )
    op.create_index(
        'idx_workspaces_reconcile',
        'workspaces',
        ['phase', 'desired_state', 'operation'],
        postgresql_where='deleted_at IS NULL'
    )
    op.create_index(
        'idx_workspaces_operation',
        'workspaces',
        ['operation'],
        postgresql_where="deleted_at IS NULL AND operation != 'NONE'"
    )
    op.create_index(
        'idx_workspaces_user_running',
        'workspaces',
        ['owner_user_id'],
        postgresql_where="deleted_at IS NULL AND phase = 'RUNNING'"
    )
    op.create_index(
        'idx_workspaces_running',
        'workspaces',
        ['phase'],
        postgresql_where="deleted_at IS NULL AND phase = 'RUNNING'"
    )
    op.create_index(
        'idx_workspaces_error',
        'workspaces',
        ['phase'],
        postgresql_where="deleted_at IS NULL AND phase = 'ERROR'"
    )


def downgrade() -> None:
    # Drop new indexes
    op.drop_index('idx_workspaces_error')
    op.drop_index('idx_workspaces_running')
    op.drop_index('idx_workspaces_user_running')
    op.drop_index('idx_workspaces_operation')
    op.drop_index('idx_workspaces_reconcile')
    op.drop_index('idx_workspaces_ttl_check')

    # Drop M2 columns
    op.drop_column('workspaces', 'error_count')
    op.drop_column('workspaces', 'error_reason')
    op.drop_column('workspaces', 'archive_ttl_seconds')
    op.drop_column('workspaces', 'standby_ttl_seconds')
    op.drop_column('workspaces', 'last_access_at')
    op.drop_column('workspaces', 'observed_at')
    op.drop_column('workspaces', 'archive_key')
    op.drop_column('workspaces', 'desired_state')
    op.drop_column('workspaces', 'op_id')
    op.drop_column('workspaces', 'op_started_at')
    op.drop_column('workspaces', 'operation')
    op.drop_column('workspaces', 'phase')
    op.drop_column('workspaces', 'conditions')

    # Recreate status enum and column
    op.execute("CREATE TYPE workspacestatus AS ENUM ('creating', 'running', 'stopped', 'error')")
    op.add_column('workspaces', sa.Column(
        'status',
        sa.Enum('creating', 'running', 'stopped', 'error', name='workspacestatus'),
        nullable=False,
        server_default='creating'
    ))
