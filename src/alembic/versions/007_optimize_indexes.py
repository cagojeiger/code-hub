"""Optimize workspace indexes.

Revision ID: 007_optimize_indexes
Revises: 006_unify_sse_channel
Create Date: 2026-01-06

Changes:
- Remove unused indexes (running, error) - no queries use them
- Add user list index for API performance (list_workspaces)
- Add archive_key index for GC performance (_get_protected_paths)
"""

from alembic import op

revision = '007_optimize_indexes'
down_revision = '006_unify_sse_channel'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 불필요한 인덱스 제거 (사용처 없음)
    op.drop_index('idx_workspaces_running', table_name='workspaces')
    op.drop_index('idx_workspaces_error', table_name='workspaces')

    # 사용자별 workspace 목록 (가장 자주 호출되는 API)
    # workspace_service.py:124 list_workspaces()
    op.create_index(
        'idx_workspaces_user_list',
        'workspaces',
        ['owner_user_id', 'created_at'],
        postgresql_where="deleted_at IS NULL"
    )

    # GC archive_key 보호 조회
    # gc.py:168 _get_protected_paths()
    op.create_index(
        'idx_workspaces_archive_key',
        'workspaces',
        ['archive_key'],
        postgresql_where="deleted_at IS NULL AND archive_key IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index('idx_workspaces_archive_key', table_name='workspaces')
    op.drop_index('idx_workspaces_user_list', table_name='workspaces')

    # 원래 인덱스 복원
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
