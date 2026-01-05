"""Workspace proxy routes.

Provides HTTP and WebSocket reverse proxy to workspace containers.
Routes: /w/{workspace_id}/* -> code-server container
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocket

from codehub.adapters.instance.docker import DockerInstanceController
from codehub.core.errors import (
    ForbiddenError,
    UnauthorizedError,
    UpstreamUnavailableError,
    WorkspaceNotFoundError,
)
from codehub.core.interfaces import InstanceController
from codehub.infra import get_session

from .activity import get_activity_buffer
from .auth import get_user_id_from_session, get_workspace_for_user
from .policy import ProxyDecision, decide_http, decide_ws
from .transport import proxy_http_to_upstream, proxy_ws_to_upstream

logger = logging.getLogger(__name__)

# Cache activity buffer at module level to avoid function call overhead per request
_activity_buffer = get_activity_buffer()

router = APIRouter(tags=["proxy"])

# =============================================================================
# Dependencies
# =============================================================================

DbSession = Annotated[AsyncSession, Depends(get_session)]

# InstanceController singleton
_instance_controller: InstanceController | None = None


def get_instance_controller() -> InstanceController:
    """Get InstanceController singleton."""
    global _instance_controller
    if _instance_controller is None:
        _instance_controller = DockerInstanceController()
    return _instance_controller


Instance = Annotated[InstanceController, Depends(get_instance_controller)]

# =============================================================================
# Routes
# =============================================================================


@router.get("/w/{workspace_id}")
async def trailing_slash_redirect(workspace_id: str) -> RedirectResponse:
    """308 Permanent Redirect to add trailing slash."""
    return RedirectResponse(
        url=f"/w/{workspace_id}/",
        status_code=308,
    )


@router.api_route(
    "/w/{workspace_id}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    response_model=None,
)
async def proxy_http(
    workspace_id: str,
    path: str,
    request: Request,
    db: DbSession,
    instance: Instance,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> StreamingResponse | RedirectResponse:
    """Proxy HTTP requests to workspace container."""
    # Authenticate user and verify workspace ownership
    user_id = await get_user_id_from_session(db, session)
    workspace = await get_workspace_for_user(db, workspace_id, user_id)

    # Phase-based policy decision (spec_v2/02-states.md)
    policy_result = await decide_http(db, workspace, user_id)
    if policy_result.decision != ProxyDecision.ALLOW:
        return policy_result.response

    # Record activity for TTL tracking
    _activity_buffer.record(workspace_id)

    # Resolve upstream via InstanceController
    upstream = await instance.resolve_upstream(workspace_id)
    if upstream is None:
        raise UpstreamUnavailableError()

    return await proxy_http_to_upstream(request, upstream, path, workspace_id)


@router.websocket("/w/{workspace_id}/{path:path}")
async def proxy_websocket(
    websocket: WebSocket,
    workspace_id: str,
    path: str,
    db: DbSession,
    instance: Instance,
) -> None:
    """Proxy WebSocket connections to workspace container."""
    # Authenticate user and verify workspace ownership
    session_cookie = websocket.cookies.get("session")
    try:
        user_id = await get_user_id_from_session(db, session_cookie)
        workspace = await get_workspace_for_user(db, workspace_id, user_id)
    except UnauthorizedError:
        await websocket.close(code=1008, reason="Authentication required")
        return
    except ForbiddenError:
        await websocket.close(code=1008, reason="Access denied")
        return
    except WorkspaceNotFoundError:
        await websocket.close(code=1008, reason="Workspace not found")
        return

    # Phase-based policy decision
    policy_result = decide_ws(workspace)
    if policy_result.decision != ProxyDecision.ALLOW:
        await websocket.close(
            code=policy_result.ws_close_code,
            reason=policy_result.ws_close_reason,
        )
        return

    # Record activity for TTL tracking
    _activity_buffer.record(workspace_id)

    # Resolve upstream via InstanceController
    upstream = await instance.resolve_upstream(workspace_id)
    if upstream is None:
        await websocket.close(code=1011, reason="Upstream unavailable")
        return

    await proxy_ws_to_upstream(websocket, upstream, path, workspace_id)
