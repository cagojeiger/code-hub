"""Add name/description/memo to workspace notify trigger.

Revision ID: 005_metadata_notify
Revises: 004_drop_ttl_columns
Create Date: 2026-01-04

Extends ws_sse NOTIFY to include metadata changes:
- name: workspace name changes
- description: workspace description changes
- memo: workspace memo changes

This enables real-time SSE updates when users edit workspace metadata.
Reference: docs/architecture_v2/event-listener.md
"""

from alembic import op


# revision identifiers, used by Alembic
revision = '005_metadata_notify'
down_revision = '004_drop_ttl_columns'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Replace trigger function with updated version
    op.execute("""
        CREATE OR REPLACE FUNCTION notify_workspace_changes()
        RETURNS trigger AS $$
        BEGIN
            -- INSERT: new workspace notification
            IF TG_OP = 'INSERT' THEN
                -- SSE: new workspace event
                PERFORM pg_notify('ws_sse', json_build_object(
                    'id', NEW.id,
                    'owner_user_id', NEW.owner_user_id
                )::text);

                -- Wake: if desired_state is set
                IF NEW.desired_state IS NOT NULL THEN
                    PERFORM pg_notify('ws_wake', '{}'::text);
                END IF;

                RETURN NEW;
            END IF;

            -- UPDATE: column-specific notifications
            IF TG_OP = 'UPDATE' THEN
                -- SSE: UI update (phase, operation, error_reason, name, description, memo)
                IF OLD.phase IS DISTINCT FROM NEW.phase OR
                   OLD.operation IS DISTINCT FROM NEW.operation OR
                   OLD.error_reason IS DISTINCT FROM NEW.error_reason OR
                   OLD.name IS DISTINCT FROM NEW.name OR
                   OLD.description IS DISTINCT FROM NEW.description OR
                   OLD.memo IS DISTINCT FROM NEW.memo THEN
                    PERFORM pg_notify('ws_sse', json_build_object(
                        'id', NEW.id,
                        'owner_user_id', NEW.owner_user_id
                    )::text);
                END IF;

                -- Wake: Coordinator trigger (desired_state)
                IF OLD.desired_state IS DISTINCT FROM NEW.desired_state THEN
                    PERFORM pg_notify('ws_wake', '{}'::text);
                END IF;

                -- Deleted: delete notification (deleted_at NULL -> NOT NULL)
                IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN
                    PERFORM pg_notify('ws_deleted', json_build_object(
                        'id', NEW.id,
                        'owner_user_id', NEW.owner_user_id
                    )::text);
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    # Restore original trigger function (without name/description/memo)
    op.execute("""
        CREATE OR REPLACE FUNCTION notify_workspace_changes()
        RETURNS trigger AS $$
        BEGIN
            -- INSERT: new workspace notification
            IF TG_OP = 'INSERT' THEN
                -- SSE: new workspace event
                PERFORM pg_notify('ws_sse', json_build_object(
                    'id', NEW.id,
                    'owner_user_id', NEW.owner_user_id
                )::text);

                -- Wake: if desired_state is set
                IF NEW.desired_state IS NOT NULL THEN
                    PERFORM pg_notify('ws_wake', '{}'::text);
                END IF;

                RETURN NEW;
            END IF;

            -- UPDATE: column-specific notifications
            IF TG_OP = 'UPDATE' THEN
                -- SSE: UI update (phase, operation, error_reason)
                IF OLD.phase IS DISTINCT FROM NEW.phase OR
                   OLD.operation IS DISTINCT FROM NEW.operation OR
                   OLD.error_reason IS DISTINCT FROM NEW.error_reason THEN
                    PERFORM pg_notify('ws_sse', json_build_object(
                        'id', NEW.id,
                        'owner_user_id', NEW.owner_user_id
                    )::text);
                END IF;

                -- Wake: Coordinator trigger (desired_state)
                IF OLD.desired_state IS DISTINCT FROM NEW.desired_state THEN
                    PERFORM pg_notify('ws_wake', '{}'::text);
                END IF;

                -- Deleted: delete notification (deleted_at NULL -> NOT NULL)
                IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN
                    PERFORM pg_notify('ws_deleted', json_build_object(
                        'id', NEW.id,
                        'owner_user_id', NEW.owner_user_id
                    )::text);
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
