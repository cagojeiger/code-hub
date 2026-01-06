"""SSE Events API endpoint.

Provides real-time workspace updates via Server-Sent Events.
Uses ChannelSubscriber (Redis PUB/SUB) for event delivery.

Data flow:
  PG TRIGGER -> EventListener (DB query) -> Redis PUB/SUB -> SSE endpoint

No DB queries in this module - full workspace data comes from EventListener.
Frontend checks deleted_at field to determine if workspace was deleted.

Configuration via SSEConfig (SSE_ env prefix).
Reference: docs/architecture_v2/event-listener.md
"""

import asyncio
import json
import logging
from typing import Annotated, AsyncGenerator

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from codehub.app.config import get_settings
from codehub.app.proxy.auth import get_user_id_from_session
from codehub.infra import get_redis, get_session
from codehub.infra.redis_pubsub import ChannelSubscriber

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

DbSession = Annotated[AsyncSession, Depends(get_session)]

_settings = get_settings()
_sse_config = _settings.sse
_channel_config = _settings.redis_channel


async def _event_generator(
    request: Request,
    user_id: str,
) -> AsyncGenerator[str, None]:
    """Generate SSE events for a user.

    Uses ChannelSubscriber (PUB/SUB) to receive events from '{sse_prefix}:{user_id}'.
    Yields:
    - workspace: full workspace data (frontend checks deleted_at)
    - heartbeat: every 30 seconds

    Includes deduplication to filter consecutive identical events.
    No DB queries - full data comes from EventListener via Redis PUB/SUB.
    """
    subscriber = ChannelSubscriber(get_redis())
    channel = f"{_channel_config.sse_prefix}:{user_id}"

    # Deduplication: track last sent state per workspace
    # Key: workspace_id, Value: (phase, operation, error_reason, name, desc, memo)
    last_sent_state: dict[str, tuple] = {}

    logger.info("[SSE] User %s connected (channel=%s)", user_id, channel)

    try:
        await subscriber.subscribe(channel)

        loop = asyncio.get_running_loop()
        last_heartbeat = loop.time()

        # Send initial connection event to establish the stream
        yield "event: connected\ndata: {}\n\n"

        while True:
            if await request.is_disconnected():
                break

            try:
                payload = await subscriber.get_message(timeout=1.0)
            except Exception as e:
                logger.warning("[SSE] Redis read error: %s", e)
                await asyncio.sleep(1)
                continue

            if payload is not None:
                logger.info("[SSE] Received message for user %s", user_id)
                try:
                    data = json.loads(payload)
                    workspace_id = data.get("id")

                    if data.get("deleted_at"):
                        last_sent_state.pop(workspace_id, None)
                        yield f"event: workspace\ndata: {payload}\n\n"
                    else:
                        current_state = (
                            data.get("phase"),
                            data.get("operation"),
                            data.get("error_reason"),
                            data.get("name"),
                            data.get("description"),
                            data.get("memo"),
                        )
                        if last_sent_state.get(workspace_id) == current_state:
                            continue

                        last_sent_state[workspace_id] = current_state
                        yield f"event: workspace\ndata: {payload}\n\n"

                except json.JSONDecodeError as e:
                    logger.warning("[SSE] Invalid JSON in message: %s", e)

            now = loop.time()
            if now - last_heartbeat >= _sse_config.heartbeat_interval:
                yield "event: heartbeat\ndata: {}\n\n"
                last_heartbeat = now

    except asyncio.CancelledError:
        pass
    finally:
        await subscriber.unsubscribe()
        logger.info("[SSE] User %s disconnected", user_id)


@router.get("/events")
async def sse_events(
    request: Request,
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> StreamingResponse:
    """SSE endpoint for real-time workspace updates.

    Streams events:
    - workspace: Full workspace object (frontend checks deleted_at field)
    - heartbeat: {} every 30 seconds

    Requires valid session cookie.

    Uses Redis PUB/SUB for event delivery.
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
