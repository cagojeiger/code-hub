"""Add workspace notify trigger for CDC.

Revision ID: 002_workspace_notify
Revises: 001_m2_workspace
Create Date: 2026-01-04

Creates PG triggers for real-time event notification:
- ws_sse: UI updates (phase, operation, error_reason changes)
- ws_wake: Coordinator wake (desired_state changes)
- ws_deleted: Delete notifications (deleted_at set)

Reference: docs/architecture_v2/event-listener.md
"""

from alembic import op


# revision identifiers, used by Alembic
revision = '002_workspace_notify'
down_revision = '001_m2_workspace'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create trigger function
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

    # Create trigger on workspaces table
    op.execute("""
        CREATE TRIGGER workspace_notify_trigger
        AFTER INSERT OR UPDATE ON workspaces
        FOR EACH ROW EXECUTE FUNCTION notify_workspace_changes();
    """)


def downgrade() -> None:
    # Drop trigger first
    op.execute("DROP TRIGGER IF EXISTS workspace_notify_trigger ON workspaces")
    # Drop function
    op.execute("DROP FUNCTION IF EXISTS notify_workspace_changes()")
