"""Event publishing for real-time workspace updates via Redis Pub/Sub.

Channel naming convention: {domain}:{scope}:{scope_id}
- events:user:{user_id} - User-scoped events (workspace updates for owner)
- events:workspace:{workspace_id} - Workspace-scoped events (future)
- events:system:global - System-wide broadcasts (future)
"""

import json
import logging
from typing import Any

from app.core.redis import get_redis
from app.db import Workspace
from app.schemas.workspace import WorkspaceDeletedEvent, WorkspaceResponse

logger = logging.getLogger(__name__)


def _get_user_channel(user_id: str) -> str:
    """Get Redis channel name for user events."""
    return f"events:user:{user_id}"


async def publish_workspace_event(
    event_type: str,
    workspace_data: dict[str, Any],
) -> None:
    """Publish a workspace event to the owner's channel."""
    owner_user_id = workspace_data.get("owner_user_id")
    if not owner_user_id:
        logger.warning("Cannot publish event without owner_user_id")
        return

    channel = _get_user_channel(owner_user_id)
    event = {"type": event_type, "data": workspace_data}

    try:
        redis_client = get_redis()
        await redis_client.publish(channel, json.dumps(event))
        logger.debug("Published %s event to channel %s", event_type, channel)
    except Exception:
        logger.exception("Failed to publish event to Redis")


async def notify_workspace_updated(workspace: Workspace, public_base_url: str) -> None:
    """Notify clients about workspace update."""
    response = WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        description=workspace.description,
        memo=workspace.memo,
        status=workspace.status,
        url=f"{public_base_url}/w/{workspace.id}/",
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )
    data = response.model_dump(mode="json")
    data["owner_user_id"] = workspace.owner_user_id  # For channel routing
    await publish_workspace_event("workspace_updated", data)


async def notify_workspace_deleted(workspace_id: str, owner_user_id: str) -> None:
    """Notify clients about workspace deletion."""
    event = WorkspaceDeletedEvent(id=workspace_id)
    data = event.model_dump()
    data["owner_user_id"] = owner_user_id  # For channel routing
    await publish_workspace_event("workspace_deleted", data)
