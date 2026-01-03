"""M2 initial schema migration.

Revision ID: 001_m2_workspace
Revises:
Create Date: 2026-01-03

Creates all tables for M2 schema:
- users: User accounts
- sessions: Login sessions
- workspaces: Workspace with M2 state machine columns
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
    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('username', sa.String(), unique=True, index=True, nullable=False),
        sa.Column('password_hash', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('failed_login_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_failed_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Create sessions table
    op.create_table(
        'sessions',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), index=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    )

    # Create workspaces table (M2 schema)
    op.create_table(
        'workspaces',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('owner_user_id', sa.String(), sa.ForeignKey('users.id'), index=True, nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.String(500), nullable=True),
        sa.Column('memo', sa.Text(), nullable=True),
        sa.Column('image_ref', sa.String(512), nullable=False),
        sa.Column('instance_backend', sa.String(), nullable=False),
        sa.Column('storage_backend', sa.String(), nullable=False),
        sa.Column('home_store_key', sa.String(512), nullable=False),
        sa.Column('home_ctx', postgresql.JSONB(), nullable=True),
        # M2 state columns
        sa.Column('conditions', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('phase', sa.String(), nullable=False, server_default='PENDING'),
        sa.Column('operation', sa.String(), nullable=False, server_default='NONE'),
        sa.Column('op_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('op_id', sa.String(), nullable=True),
        sa.Column('desired_state', sa.String(), nullable=False, server_default='RUNNING'),
        sa.Column('archive_key', sa.String(512), nullable=True),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_access_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('standby_ttl_seconds', sa.Integer(), nullable=False, server_default='300'),
        sa.Column('archive_ttl_seconds', sa.Integer(), nullable=False, server_default='86400'),
        sa.Column('error_reason', sa.String(), nullable=True),
        sa.Column('error_count', sa.Integer(), nullable=False, server_default='0'),
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True, index=True),
    )

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
    # Drop indexes
    op.drop_index('idx_workspaces_error')
    op.drop_index('idx_workspaces_running')
    op.drop_index('idx_workspaces_user_running')
    op.drop_index('idx_workspaces_operation')
    op.drop_index('idx_workspaces_reconcile')
    op.drop_index('idx_workspaces_ttl_check')

    # Drop tables in reverse order (respect foreign keys)
    op.drop_table('workspaces')
    op.drop_table('sessions')
    op.drop_table('users')
