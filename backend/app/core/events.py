"""Event publishing for real-time workspace updates.

This module is separate from api/v1/events.py to avoid circular imports.
The SSE endpoint imports from here, and services can also import from here.
"""

import asyncio
import logging
from typing import Any

from app.db import Workspace

logger = logging.getLogger(__name__)

# Simple in-memory event queue for MVP
# In production, use Redis pub/sub or similar
_event_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}


def get_event_queues() -> dict[str, asyncio.Queue[dict[str, Any]]]:
    """Get the global event queues dictionary."""
    return _event_queues


def publish_workspace_event(event_type: str, workspace_data: dict[str, Any]) -> None:
    """Publish a workspace event to all connected clients."""
    event = {"type": event_type, "data": workspace_data}
    for queue in _event_queues.values():
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping event")


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
