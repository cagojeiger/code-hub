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
from codehub.app.metrics.collector import (
    SSE_ACTIVE_CONNECTIONS,
    SSE_DEDUP_SKIPPED_TOTAL,
    SSE_ERRORS_TOTAL,
    SSE_MESSAGES_TOTAL,
)
from codehub.app.proxy.auth import get_user_id_from_session
from codehub.core.logging_schema import LogEvent
from codehub.infra import get_redis, get_session
from codehub.infra.redis_pubsub import ChannelSubscriber

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

DbSession = Annotated[AsyncSession, Depends(get_session)]

_settings = get_settings()
_sse_config = _settings.sse
_channel_config = _settings.redis_channel


def _process_payload(
    payload: str,
    last_sent_state: dict[str, tuple],
    user_id: str,
) -> tuple[str | None, dict[str, tuple]]:
    """Process SSE payload with early return pattern.

    Returns:
        (event_to_yield, updated_state): event_to_yield is None if skipped.
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        SSE_ERRORS_TOTAL.labels(error_type="json_decode").inc()
        logger.warning(
            "Invalid JSON in message",
            extra={
                "event": LogEvent.SSE_RECEIVED,
                "user_id": user_id,
                "error": str(e),
            },
        )
        return None, last_sent_state

    workspace_id = data.get("id")

    # Deleted workspace - always send, remove from dedup cache
    if data.get("deleted_at"):
        last_sent_state.pop(workspace_id, None)
        return f"event: workspace\ndata: {payload}\n\n", last_sent_state

    # Build current state for deduplication
    current_state = (
        data.get("phase"),
        data.get("operation"),
        data.get("error_reason"),
        data.get("name"),
        data.get("description"),
        data.get("memo"),
    )

    # Duplicate check - skip if same as last sent
    if last_sent_state.get(workspace_id) == current_state:
        SSE_DEDUP_SKIPPED_TOTAL.inc()
        return None, last_sent_state

    # Update state and return event
    last_sent_state[workspace_id] = current_state
    return f"event: workspace\ndata: {payload}\n\n", last_sent_state


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
    last_sent_state: dict[str, tuple] = {}

    logger.info(
        "User connected",
        extra={
            "event": LogEvent.SSE_CONNECTED,
            "user_id": user_id,
            "channel": channel,
        },
    )

    SSE_ACTIVE_CONNECTIONS.inc()
    try:
        await subscriber.subscribe(channel)

        loop = asyncio.get_running_loop()
        last_heartbeat = loop.time()

        # Send initial connection event
        yield "event: connected\ndata: {}\n\n"
        SSE_MESSAGES_TOTAL.labels(event_type="connected").inc()

        while True:
            if await request.is_disconnected():
                break

            # Get message from Redis
            try:
                payload = await subscriber.get_message(timeout=1.0)
            except Exception as e:
                SSE_ERRORS_TOTAL.labels(error_type="redis_read").inc()
                logger.warning(
                    "Redis read error",
                    extra={
                        "event": LogEvent.SSE_RECEIVED,
                        "user_id": user_id,
                        "error": str(e),
                    },
                )
                await asyncio.sleep(1)
                continue

            # Process payload if received
            if payload is not None:
                logger.debug(
                    "Received message",
                    extra={
                        "event": LogEvent.SSE_RECEIVED,
                        "user_id": user_id,
                    },
                )
                event, last_sent_state = _process_payload(payload, last_sent_state, user_id)
                if event is not None:
                    yield event
                    SSE_MESSAGES_TOTAL.labels(event_type="workspace").inc()

            # Heartbeat check
            now = loop.time()
            if now - last_heartbeat >= _sse_config.heartbeat_interval:
                yield "event: heartbeat\ndata: {}\n\n"
                SSE_MESSAGES_TOTAL.labels(event_type="heartbeat").inc()
                last_heartbeat = now

    except asyncio.CancelledError:
        pass
    finally:
        SSE_ACTIVE_CONNECTIONS.dec()
        await subscriber.unsubscribe()
        logger.info(
            "User disconnected",
            extra={
                "event": LogEvent.SSE_DISCONNECTED,
                "user_id": user_id,
            },
        )


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
