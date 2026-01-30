"""Add templates table and workspace.template_id column.

Revision ID: 010_add_templates
Revises: 009_add_restore_op_id
Create Date: 2026-01-30

Adds support for workspace templates (v0.2.1 feature).

Templates allow users to save a workspace's initial state (from archive)
and reuse it when creating new workspaces. Templates reuse the existing
archive/restore infrastructure.

Schema changes:
1. Create 'templates' table with archive metadata
2. Add 'template_id' foreign key to 'workspaces' table
3. Add indexes for template queries

Reference: docs/spec/03-schema.md (to be updated)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = '010_add_templates'
down_revision = '009_add_restore_op_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create templates table
    op.execute("""
        CREATE TABLE IF NOT EXISTS templates (
            id VARCHAR(26) PRIMARY KEY,  -- ULID
            owner_user_id VARCHAR(26) NOT NULL REFERENCES users(id),
            name VARCHAR(255) NOT NULL,
            description TEXT,
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            
            -- Archive metadata (reused from workspace archiving)
            source_workspace_id VARCHAR(26) NOT NULL,
            archive_key VARCHAR(512) NOT NULL,
            image_ref VARCHAR(512) NOT NULL,
            storage_backend VARCHAR(50) NOT NULL,
            
            -- Metadata
            archive_size_bytes BIGINT,
            file_count INTEGER,
            
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            deleted_at TIMESTAMPTZ
        )
    """)
    
    # Add indexes for template queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_templates_owner
        ON templates(owner_user_id)
        WHERE deleted_at IS NULL
    """)
    
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_templates_name
        ON templates(owner_user_id, name)
        WHERE deleted_at IS NULL
    """)
    
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_templates_tags
        ON templates USING GIN(tags)
        WHERE deleted_at IS NULL
    """)
    
    # Add template_id column to workspaces
    op.execute("""
        ALTER TABLE workspaces
        ADD COLUMN IF NOT EXISTS template_id VARCHAR(26) REFERENCES templates(id)
    """)
    
    # Add index for template usage tracking
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_workspaces_template
        ON workspaces(template_id)
        WHERE deleted_at IS NULL AND template_id IS NOT NULL
    """)


def downgrade() -> None:
    # Remove template_id column and index from workspaces
    op.drop_index('idx_workspaces_template', table_name='workspaces')
    op.drop_column('workspaces', 'template_id')
    
    # Drop templates table indexes
    op.drop_index('idx_templates_tags', table_name='templates')
    op.drop_index('idx_templates_name', table_name='templates')
    op.drop_index('idx_templates_owner', table_name='templates')
    
    # Drop templates table
    op.drop_table('templates')
