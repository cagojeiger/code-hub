"""Workspace proxy routes: /w/{workspace_id}/* -> Agent -> container.

CP handles authentication/authorization, then forwards traffic to Agent via FRP.
Agent handles the actual proxy to workspace containers (Double Proxy).
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocket

from codehub.app.config import get_settings
from codehub.core.errors import (
    ForbiddenError,
    UnauthorizedError,
    WorkspaceNotFoundError,
)
from codehub.infra import get_session

from .activity import get_activity_buffer
from .auth import get_user_id_from_session, get_workspace_for_user
from .policy import ProxyDecision, decide_http, decide_ws
from .transport import forward_http_to_agent, forward_ws_to_agent

logger = logging.getLogger(__name__)

_activity_buffer = get_activity_buffer()
_settings = get_settings()
router = APIRouter(tags=["proxy"])

DbSession = Annotated[AsyncSession, Depends(get_session)]


@router.get("/w/{workspace_id}")
async def trailing_slash_redirect(
    workspace_id: str, request: Request
) -> RedirectResponse:
    qs = str(request.url.query)
    target = f"/w/{workspace_id}/"
    if qs:
        target = f"{target}?{qs}"
    return RedirectResponse(url=target, status_code=308)


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
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> StreamingResponse | RedirectResponse:
    user_id = await get_user_id_from_session(db, session)
    workspace = await get_workspace_for_user(db, workspace_id, user_id)

    policy_result = await decide_http(db, workspace, user_id)
    if policy_result.decision != ProxyDecision.ALLOW:
        return policy_result.response

    _activity_buffer.record(workspace_id)

    return await forward_http_to_agent(
        request, _settings.agent.endpoint, workspace_id, path
    )


@router.websocket("/w/{workspace_id}/{path:path}")
async def proxy_websocket(
    websocket: WebSocket,
    workspace_id: str,
    path: str,
    db: DbSession,
) -> None:
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

    await forward_ws_to_agent(
        websocket, _settings.agent.endpoint, workspace_id, path
    )
