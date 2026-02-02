"""Workspace proxy routes: /w/{workspace_id}/* -> container."""

import logging
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocket

from codehub.agent.client import AgentClient, AgentConfig
from codehub.app.config import get_settings
from codehub.core.errors import (
    ForbiddenError,
    UnauthorizedError,
    UpstreamUnavailableError,
    WorkspaceNotFoundError,
)
from codehub.core.interfaces.runtime import WorkspaceRuntime
from codehub.infra import get_session

from .activity import get_activity_buffer
from .auth import get_user_id_from_session, get_workspace_for_user
from .policy import ProxyDecision, decide_http, decide_ws
from .transport import proxy_http_to_upstream, proxy_ws_to_upstream

logger = logging.getLogger(__name__)

_activity_buffer = get_activity_buffer()
router = APIRouter(tags=["proxy"])

DbSession = Annotated[AsyncSession, Depends(get_session)]

_runtime: WorkspaceRuntime | None = None


def get_runtime() -> WorkspaceRuntime:
    global _runtime
    if _runtime is None:
        settings = get_settings()
        config = AgentConfig(
            endpoint=settings.agent.endpoint,
            api_key=settings.agent.api_key,
            timeout=settings.agent.timeout,
            job_timeout=settings.agent.job_timeout,
        )
        _runtime = AgentClient(config)
    return _runtime


Runtime = Annotated[WorkspaceRuntime, Depends(get_runtime)]


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
    runtime: Runtime,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> StreamingResponse | RedirectResponse:
    """Proxy HTTP requests to workspace container."""
    user_id = await get_user_id_from_session(db, session)
    workspace = await get_workspace_for_user(db, workspace_id, user_id)

    policy_result = await decide_http(db, workspace, user_id)
    if policy_result.decision != ProxyDecision.ALLOW:
        return policy_result.response

    _activity_buffer.record(workspace_id)

    upstream = await runtime.get_upstream(workspace_id)
    if upstream is None:
        raise UpstreamUnavailableError()

    return await proxy_http_to_upstream(request, upstream, path, workspace_id)


@router.websocket("/w/{workspace_id}/{path:path}")
async def proxy_websocket(
    websocket: WebSocket,
    workspace_id: str,
    path: str,
    db: DbSession,
    runtime: Runtime,
) -> None:
    """Proxy WebSocket connections to workspace container."""
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

    policy_result = decide_ws(workspace)
    if policy_result.decision != ProxyDecision.ALLOW:
        await websocket.close(
            code=policy_result.ws_close_code,
            reason=policy_result.ws_close_reason,
        )
        return

    _activity_buffer.record(workspace_id)

    upstream = await runtime.get_upstream(workspace_id)
    if upstream is None:
        await websocket.close(code=1011, reason="Upstream unavailable")
        return

    await proxy_ws_to_upstream(websocket, upstream, path, workspace_id)
