"""SSE events endpoint for real-time workspace updates."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.v1.dependencies import CurrentUser
from app.db import Workspace

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

# Simple in-memory event queue for MVP
# In production, use Redis pub/sub or similar
_event_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}


def publish_workspace_event(event_type: str, workspace_data: dict[str, Any]) -> None:
    """Publish a workspace event to all connected clients."""
    event = {"type": event_type, "data": workspace_data}
    for queue in _event_queues.values():
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping event")


async def _event_generator(
    request: Request,
    user_id: str,
) -> AsyncGenerator[str, None]:
    """Generate SSE events for a connected client."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
    queue_id = f"{user_id}_{id(queue)}"
    _event_queues[queue_id] = queue

    try:
        # Send initial connected event
        yield "event: connected\ndata: {}\n\n"

        heartbeat_interval = 30  # seconds
        last_heartbeat = asyncio.get_event_loop().time()

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                # Wait for events with timeout for heartbeat
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)

                # Filter events by owner
                if event["type"] in ("workspace_updated", "workspace_deleted"):
                    workspace_data = event["data"]
                    # Only send events for workspaces owned by this user
                    if workspace_data.get("owner_user_id") != user_id:
                        continue

                    # Remove internal fields before sending
                    data = {k: v for k, v in workspace_data.items() if k != "owner_user_id"}
                    yield f"event: {event['type']}\ndata: {json.dumps(data)}\n\n"

            except asyncio.TimeoutError:
                # Send heartbeat
                current_time = asyncio.get_event_loop().time()
                if current_time - last_heartbeat >= heartbeat_interval:
                    yield "event: heartbeat\ndata: {}\n\n"
                    last_heartbeat = current_time

    finally:
        # Cleanup
        del _event_queues[queue_id]


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


# Helper functions to be called from workspace service
def notify_workspace_updated(workspace: Workspace, public_base_url: str) -> None:
    """Notify clients about workspace update."""
    data = {
        "id": workspace.id,
        "name": workspace.name,
        "description": workspace.description,
        "memo": workspace.memo,
        "status": workspace.status.value,
        "url": f"{public_base_url}/w/{workspace.id}/",
        "created_at": workspace.created_at.isoformat() if workspace.created_at else None,
        "updated_at": workspace.updated_at.isoformat() if workspace.updated_at else None,
        "owner_user_id": workspace.owner_user_id,  # For filtering, not sent to client
    }
    publish_workspace_event("workspace_updated", data)


def notify_workspace_deleted(workspace_id: str, owner_user_id: str) -> None:
    """Notify clients about workspace deletion."""
    data = {
        "id": workspace_id,
        "owner_user_id": owner_user_id,  # For filtering, not sent to client
    }
    publish_workspace_event("workspace_deleted", data)
