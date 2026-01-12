"""Unify ws_deleted into ws_sse channel.

Revision ID: 006_unify_sse_channel
Revises: 005_metadata_notify
Create Date: 2026-01-06

Changes:
- Add desired_state to ws_sse trigger conditions
- Add deleted_at (NULL -> NOT NULL) to ws_sse trigger conditions
- Remove ws_deleted channel (unified into ws_sse)

Reference: docs/architecture_v2/event-listener.md
"""

from alembic import op


# revision identifiers, used by Alembic
revision = '006_unify_sse_channel'
down_revision = '005_metadata_notify'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Replace trigger function with unified version
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
                -- ws_sse: UI update (all UI-visible fields + deleted_at)
                IF OLD.phase IS DISTINCT FROM NEW.phase OR
                   OLD.operation IS DISTINCT FROM NEW.operation OR
                   OLD.error_reason IS DISTINCT FROM NEW.error_reason OR
                   OLD.name IS DISTINCT FROM NEW.name OR
                   OLD.description IS DISTINCT FROM NEW.description OR
                   OLD.memo IS DISTINCT FROM NEW.memo OR
                   OLD.desired_state IS DISTINCT FROM NEW.desired_state OR
                   (OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL) THEN
                    PERFORM pg_notify('ws_sse', json_build_object(
                        'id', NEW.id,
                        'owner_user_id', NEW.owner_user_id
                    )::text);
                END IF;

                -- Wake: Coordinator trigger (desired_state)
                IF OLD.desired_state IS DISTINCT FROM NEW.desired_state THEN
                    PERFORM pg_notify('ws_wake', '{}'::text);
                END IF;

                -- ws_deleted removed (unified into ws_sse)
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    # Restore previous trigger function (with ws_deleted channel)
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
