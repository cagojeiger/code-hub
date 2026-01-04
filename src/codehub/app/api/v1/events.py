"""SSE Events API endpoint.

Provides real-time workspace updates via Server-Sent Events.
Uses SSEStreamReader (Redis Streams) for reliable message delivery.

Reference: docs/architecture_v2/event-listener.md
"""

import asyncio
import json
import logging
from typing import Annotated, AsyncGenerator

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codehub.app.proxy.auth import get_user_id_from_session
from codehub.core.models import Workspace
from codehub.infra import get_redis, get_session, get_session_factory
from codehub.infra.redis import SSEStreamReader

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

DbSession = Annotated[AsyncSession, Depends(get_session)]

# Constants
HEARTBEAT_INTERVAL_SEC = 30.0

# SSE payload fields (subset of Workspace)
SSE_WORKSPACE_FIELDS = {
    "id",
    "owner_user_id",
    "name",
    "description",
    "memo",
    "image_ref",
    "phase",
    "operation",
    "desired_state",
    "archive_key",
    "error_reason",
    "error_count",
    "created_at",
    "updated_at",
    "last_access_at",
}


def _workspace_to_dict(ws: Workspace) -> dict:
    """Convert workspace model to SSE payload dict.

    Uses Pydantic's model_dump with mode='json' for automatic:
    - datetime → ISO string conversion
    - Enum → value conversion
    """
    return ws.model_dump(mode="json", include=SSE_WORKSPACE_FIELDS)


async def _get_workspace_by_id(workspace_id: str) -> Workspace | None:
    """Fetch workspace by ID using a fresh session.

    Creates a new session for each query to ensure fresh data,
    as SSE connections are long-lived.
    """
    session_factory = get_session_factory()
    async with session_factory() as db:
        result = await db.execute(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()


async def _event_generator(
    request: Request,
    user_id: str,
) -> AsyncGenerator[str, None]:
    """Generate SSE events for a user.

    Uses SSEStreamReader to read from 'events:{user_id}' and yields:
    - workspace_updated: when phase/operation changes
    - workspace_deleted: when workspace is soft-deleted
    - heartbeat: every 30 seconds

    Includes deduplication to filter consecutive identical events.
    """
    reader = SSEStreamReader(get_redis(), user_id)

    # Deduplication: track last sent state per workspace
    # Key: workspace_id, Value: (phase, operation, error_reason)
    last_sent_state: dict[str, tuple[str, str, str | None]] = {}

    logger.info("[SSE] User %s connected", user_id)

    try:
        loop = asyncio.get_running_loop()
        last_heartbeat = loop.time()

        # Send initial connection event to establish the stream
        yield "event: connected\ndata: {}\n\n"

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            # Read messages using SSEStreamReader
            try:
                messages = await reader.read()
            except Exception as e:
                logger.warning("[SSE] Redis read error: %s", e)
                await asyncio.sleep(1)
                continue

            for _msg_id, fields in messages:
                try:
                    # Get data field (may be bytes)
                    data_raw = fields.get(b"data") or fields.get("data")
                    if isinstance(data_raw, bytes):
                        data_raw = data_raw.decode()

                    data = json.loads(data_raw)
                    workspace_id = data.get("id")

                    if data.get("deleted"):
                        # Workspace deleted event
                        last_sent_state.pop(workspace_id, None)
                        yield f"event: workspace_deleted\ndata: {json.dumps({'id': workspace_id})}\n\n"
                    else:
                        # Workspace updated - fetch full data
                        workspace = await _get_workspace_by_id(workspace_id)
                        if workspace:
                            # Deduplication check (includes metadata fields)
                            current_state = (
                                workspace.phase,
                                workspace.operation,
                                workspace.error_reason,
                                workspace.name,
                                workspace.description,
                                workspace.memo,
                            )
                            if last_sent_state.get(workspace_id) == current_state:
                                continue

                            # Update cache and send
                            last_sent_state[workspace_id] = current_state
                            ws_data = _workspace_to_dict(workspace)
                            yield f"event: workspace_updated\ndata: {json.dumps(ws_data)}\n\n"

                except json.JSONDecodeError as e:
                    logger.warning("[SSE] Invalid JSON in message: %s", e)

            # Send heartbeat
            now = loop.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SEC:
                yield "event: heartbeat\ndata: {}\n\n"
                last_heartbeat = now

    except asyncio.CancelledError:
        pass
    finally:
        logger.info("[SSE] User %s disconnected", user_id)


@router.get("/events")
async def sse_events(
    request: Request,
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> StreamingResponse:
    """SSE endpoint for real-time workspace updates.

    Streams events:
    - workspace_updated: Full workspace object when phase/operation changes
    - workspace_deleted: {id: string} when workspace is deleted
    - heartbeat: {} every 30 seconds

    Requires valid session cookie.

    Uses Redis Streams for reliable message delivery.
    """
    user_id = await get_user_id_from_session(db, session)

    return StreamingResponse(
        _event_generator(request, user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
