"""Workspace proxy routes.

Provides HTTP and WebSocket reverse proxy to workspace containers.
Routes: /w/{workspace_id}/* -> code-server container

TODO: InstanceController에 resolve_upstream() 메서드 추가 필요
      또는 ContainerInfo에 hostname, port 추가 필요
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from typing import Annotated

import httpx
import websockets
from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocket, WebSocketDisconnect

from codehub.app.config import get_settings
from codehub.core.domain import Phase
from codehub.core.errors import (
    ForbiddenError,
    UnauthorizedError,
    UpstreamUnavailableError,
    WorkspaceNotFoundError,
)
from codehub.infra import get_session
from codehub.services.workspace_service import (
    list_running_workspaces,
    request_start,
    RunningLimitExceededError,
)

from .activity import get_activity_buffer
from .auth import get_user_id_from_session, get_workspace_for_user
from .pages import archived_page, error_page, limit_exceeded_page, starting_page
from .client import (
    WS_HOP_BY_HOP_HEADERS,
    filter_headers,
    get_http_client,
)
from .websocket import relay_backend_to_client, relay_client_to_backend

logger = logging.getLogger(__name__)

router = APIRouter(tags=["proxy"])

# =============================================================================
# Dependencies
# =============================================================================

DbSession = Annotated[AsyncSession, Depends(get_session)]

# TODO: InstanceController DI 추가
# Instance = Annotated[InstanceController, Depends(get_instance_controller)]

# =============================================================================
# Settings-based configuration
# =============================================================================
_settings = get_settings()
_docker_config = _settings.docker
_limits_config = _settings.limits


def _get_container_hostname(workspace_id: str) -> str:
    """Get container hostname for workspace.

    TODO: InstanceController.resolve_upstream() 구현 후 대체
    """
    return f"{_docker_config.resource_prefix}{workspace_id}"


def _get_container_port() -> int:
    """Get container port for workspace."""
    return _docker_config.container_port


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
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> StreamingResponse | RedirectResponse:
    """Proxy HTTP requests to workspace container."""
    # Authenticate user and verify workspace ownership
    user_id = await get_user_id_from_session(db, session)
    workspace = await get_workspace_for_user(db, workspace_id, user_id)

    # Phase check (spec_v2/02-states.md: Proxy behavior by phase)
    if workspace.phase == Phase.RUNNING.value:
        # Normal proxy - continue below
        pass
    elif workspace.phase == Phase.STANDBY.value:
        # Auto-wake (STANDBY only) - use request_start() single entry point
        try:
            await request_start(db, workspace_id, user_id)
        except RunningLimitExceededError:
            running_workspaces = await list_running_workspaces(db, user_id)
            return limit_exceeded_page(
                running_workspaces, _limits_config.max_running_per_user
            )
        # Return starting page (polling-based auto-refresh)
        return starting_page(workspace)
    elif workspace.phase == Phase.ARCHIVED.value:
        # 502 + restore needed (no auto-wake)
        return archived_page(workspace)
    else:
        # PENDING, ERROR, DELETED, etc -> 502 + status page
        return error_page(workspace)

    # Record activity for TTL tracking
    get_activity_buffer().record(workspace_id)

    # Resolve upstream
    # TODO: InstanceController 사용
    hostname = _get_container_hostname(workspace_id)
    port = _get_container_port()
    upstream_url = f"http://{hostname}:{port}"

    # Build target URL
    target_path = f"/{path}" if path else "/"

    # Include query string if present
    if request.url.query:
        target_path = f"{target_path}?{request.url.query}"

    target_url = f"{upstream_url}{target_path}"

    # Filter headers
    headers = filter_headers(dict(request.headers))

    # Proxy request with streaming body (memory-efficient for large uploads)
    http_client = await get_http_client()

    # Use request.stream() for streaming - avoids buffering entire body in memory
    content = request.stream() if request.method in ("POST", "PUT", "PATCH") else None

    try:
        upstream_request = http_client.build_request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=content,
        )
        upstream_response = await http_client.send(upstream_request, stream=True)

        # Filter response headers
        response_headers = filter_headers(dict(upstream_response.headers))

        async def stream_response() -> AsyncGenerator[bytes]:
            try:
                # Use aiter_raw() to preserve original encoding (gzip, br, etc.)
                # aiter_bytes() would decompress, causing mismatch with Content-Encoding header
                async for chunk in upstream_response.aiter_raw():
                    yield chunk
            finally:
                await upstream_response.aclose()

        return StreamingResponse(
            stream_response(),
            status_code=upstream_response.status_code,
            headers=response_headers,
        )
    except httpx.ConnectError as exc:
        logger.warning("Connection error to upstream %s: %s", workspace_id, exc)
        raise UpstreamUnavailableError() from exc
    except httpx.TimeoutException as exc:
        logger.warning("Timeout connecting to upstream %s: %s", workspace_id, exc)
        raise UpstreamUnavailableError() from exc


@router.websocket("/w/{workspace_id}/{path:path}")
async def proxy_websocket(
    websocket: WebSocket,
    workspace_id: str,
    path: str,
    db: DbSession,
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

    # Phase check - WebSocket only works for RUNNING workspaces
    # Non-RUNNING states are handled by HTTP endpoint (returns HTML pages)
    if workspace.phase != Phase.RUNNING.value:
        await websocket.close(code=1008, reason="Workspace not running")
        return

    # Record activity for TTL tracking
    get_activity_buffer().record(workspace_id)

    # Resolve upstream
    # TODO: InstanceController 사용
    hostname = _get_container_hostname(workspace_id)
    port = _get_container_port()

    # Build WebSocket URI with query string
    target_path = f"/{path}" if path else "/"
    query_string = websocket.scope.get("query_string", b"").decode()
    if query_string:
        target_path = f"{target_path}?{query_string}"
    upstream_ws_uri = f"ws://{hostname}:{port}{target_path}"

    # Forward all headers except hop-by-hop (RFC 6455/7230 compliance)
    client_headers = dict(websocket.headers)
    extra_headers = {
        k: v for k, v in client_headers.items() if k.lower() not in WS_HOP_BY_HOP_HEADERS
    }

    # Connect to upstream first (before accepting client)
    try:
        backend_ws = await websockets.connect(
            upstream_ws_uri, additional_headers=extra_headers
        )
    except websockets.InvalidURI as exc:
        logger.warning("Invalid WebSocket URI for %s: %s", workspace_id, exc)
        await websocket.close(code=1011, reason="Invalid upstream URI")
        return
    except websockets.InvalidHandshake as exc:
        logger.warning("WebSocket handshake failed for %s: %s", workspace_id, exc)
        await websocket.close(code=1011, reason="Upstream handshake failed")
        return
    except Exception as exc:
        logger.warning("Failed to connect to upstream %s: %s", workspace_id, exc)
        await websocket.close(code=1011, reason="Upstream connection failed")
        return

    # Accept client connection after successful upstream connection
    await websocket.accept()

    try:
        async with backend_ws:
            # Use TaskGroup for proper exception handling (Python 3.11+)
            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(relay_client_to_backend(websocket, backend_ws, workspace_id))
                    tg.create_task(relay_backend_to_client(websocket, backend_ws, workspace_id))
            except* WebSocketDisconnect:
                pass  # Normal client disconnect
            except* websockets.ConnectionClosed:
                pass  # Normal backend close
    except Exception as exc:
        # Unexpected error - log as error level
        logger.error("WebSocket proxy error for %s: %s", workspace_id, exc)
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()
