"""SSE events endpoint for real-time workspace updates via Redis Pub/Sub."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.v1.dependencies import CurrentUser
from app.core.events import get_user_channel
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


async def _event_generator(
    request: Request,
    user_id: str,
) -> AsyncGenerator[str]:
    """Generate SSE events from Redis Pub/Sub."""
    redis_client = get_redis()
    pubsub = redis_client.pubsub()
    channel = get_user_channel(user_id)

    try:
        await pubsub.subscribe(channel)
        logger.debug("Subscribed to channel %s", channel)

        # Send initial connected event
        yield "event: connected\ndata: {}\n\n"

        heartbeat_interval = 30  # seconds

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                # Wait for messages with timeout for heartbeat
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=heartbeat_interval,
                )

                if message and message["type"] == "message":
                    try:
                        event = json.loads(message["data"])
                        event_type = event["type"]
                        workspace_data = event["data"]

                        # Remove internal field before sending
                        data = {
                            k: v
                            for k, v in workspace_data.items()
                            if k != "owner_user_id"
                        }
                        yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                    except (json.JSONDecodeError, KeyError, TypeError) as e:
                        logger.warning("Malformed event message, skipping: %s", e)
                        continue

            except TimeoutError:
                # Send heartbeat
                yield "event: heartbeat\ndata: {}\n\n"

    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        logger.debug("Unsubscribed from channel %s", channel)


@router.get("/events")
async def workspace_events(
    request: Request,
    current_user: CurrentUser,
) -> StreamingResponse:
    """
    SSE endpoint for real-time workspace updates.

    Events:
    - connected: Initial connection established
    - workspace_updated: Workspace status or data changed
    - workspace_deleted: Workspace was deleted
    - heartbeat: Keep-alive signal (every 30s)
    """
    return StreamingResponse(
        _event_generator(request, current_user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
