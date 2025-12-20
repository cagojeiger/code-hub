"""Workspace proxy module for code-hub.

Provides HTTP and WebSocket reverse proxy to workspace containers.
Routes: /w/{workspace_id}/* -> code-server container

Authentication and authorization is enforced via session cookies.
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import ClientConnection

from app.api.v1.dependencies import get_instance_controller
from app.core.errors import (
    ForbiddenError,
    UnauthorizedError,
    UpstreamUnavailableError,
    WorkspaceNotFoundError,
)
from app.db import Workspace, get_async_session
from app.services.instance.interface import InstanceController
from app.services.session_service import SessionService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["proxy"])

# =============================================================================
# Constants
# =============================================================================

# Proxy timeouts (seconds)
PROXY_TIMEOUT_TOTAL = 30.0  # Total request timeout
PROXY_TIMEOUT_CONNECT = 10.0  # Connection timeout

# Shared httpx client for connection pooling
_http_client: httpx.AsyncClient | None = None

# HTTP hop-by-hop headers to remove before forwarding (RFC 7230)
HOP_BY_HOP_HEADERS = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
})

# WebSocket hop-by-hop headers (RFC 7230 + websockets library handles)
WS_HOP_BY_HOP_HEADERS = HOP_BY_HOP_HEADERS | frozenset({
    "sec-websocket-key",      # websockets library generates
    "sec-websocket-version",  # websockets library sets
    "origin",                 # Don't forward - causes 403 on code-server
})

# =============================================================================
# HTTP Client Management
# =============================================================================


async def get_http_client() -> httpx.AsyncClient:
    """Get or create shared httpx AsyncClient."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(PROXY_TIMEOUT_TOTAL, connect=PROXY_TIMEOUT_CONNECT)
        )
    return _http_client


async def close_http_client() -> None:
    """Close shared httpx client. Call on application shutdown."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


# =============================================================================
# Dependencies
# =============================================================================

Instance = Annotated[InstanceController, Depends(get_instance_controller)]
DbSession = Annotated[AsyncSession, Depends(get_async_session)]


# =============================================================================
# Helper Functions
# =============================================================================


async def _get_user_id_from_session(
    db: AsyncSession, session_cookie: str | None
) -> str:
    """Get user ID from session cookie. Raises UnauthorizedError if invalid."""
    if session_cookie is None:
        raise UnauthorizedError()

    result = await SessionService.get_valid_with_user(db, session_cookie)
    if result is None:
        raise UnauthorizedError()

    _, user = result
    return user.id


async def _get_workspace_for_user(
    session: AsyncSession, workspace_id: str, user_id: str
) -> Workspace:
    """Get workspace by ID and verify owner. Raises appropriate errors."""
    result = await session.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,  # type: ignore[arg-type]
            Workspace.deleted_at.is_(None),  # type: ignore[union-attr]
        )
    )
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFoundError()

    # Verify owner
    if workspace.owner_user_id != user_id:
        raise ForbiddenError("You don't have access to this workspace")

    return workspace


def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    """Filter out hop-by-hop headers."""
    return {
        k: v for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS
    }


# =============================================================================
# WebSocket Relay Functions (module-level for better performance)
# =============================================================================


async def _relay_client_to_backend(
    client_ws: WebSocket,
    backend_ws: ClientConnection,
) -> None:
    """Relay messages from client WebSocket to backend WebSocket."""
    while True:
        data = await client_ws.receive()
        if data["type"] == "websocket.receive":
            if "text" in data:
                await backend_ws.send(data["text"])
            elif "bytes" in data:
                await backend_ws.send(data["bytes"])
        elif data["type"] == "websocket.disconnect":
            break


async def _relay_backend_to_client(
    client_ws: WebSocket,
    backend_ws: ClientConnection,
) -> None:
    """Relay messages from backend WebSocket to client WebSocket."""
    async for message in backend_ws:
        if isinstance(message, str):
            await client_ws.send_text(message)
        else:
            await client_ws.send_bytes(message)


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
)
async def proxy_http(
    workspace_id: str,
    path: str,
    request: Request,
    db: DbSession,
    instance: Instance,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> StreamingResponse:
    """Proxy HTTP requests to workspace container."""
    # Authenticate user and verify workspace ownership
    user_id = await _get_user_id_from_session(db, session)
    await _get_workspace_for_user(db, workspace_id, user_id)

    # Resolve upstream
    try:
        upstream = await instance.resolve_upstream(workspace_id)
    except Exception as exc:
        logger.warning("Failed to resolve upstream for %s: %s", workspace_id, exc)
        raise UpstreamUnavailableError() from exc

    # Build target URL
    upstream_url = f"http://{upstream.host}:{upstream.port}"
    target_path = f"/{path}" if path else "/"

    # Include query string if present
    if request.url.query:
        target_path = f"{target_path}?{request.url.query}"

    target_url = f"{upstream_url}{target_path}"

    # Filter headers
    headers = _filter_headers(dict(request.headers))

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
        response_headers = _filter_headers(dict(upstream_response.headers))

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
    instance: Instance,
) -> None:
    """Proxy WebSocket connections to workspace container."""
    # Get session cookie from WebSocket headers
    session_cookie = websocket.cookies.get("session")

    # Authenticate user and verify workspace ownership
    try:
        user_id = await _get_user_id_from_session(db, session_cookie)
        await _get_workspace_for_user(db, workspace_id, user_id)
    except UnauthorizedError:
        await websocket.close(code=1008, reason="Authentication required")
        return
    except ForbiddenError:
        await websocket.close(code=1008, reason="Access denied")
        return
    except WorkspaceNotFoundError:
        await websocket.close(code=1008, reason="Workspace not found")
        return

    # Resolve upstream
    try:
        upstream = await instance.resolve_upstream(workspace_id)
    except Exception as exc:
        logger.warning("Failed to resolve upstream for %s: %s", workspace_id, exc)
        await websocket.close(code=1011, reason="Upstream unavailable")
        return

    # Build WebSocket URI with query string
    target_path = f"/{path}" if path else "/"
    query_string = websocket.scope.get("query_string", b"").decode()
    if query_string:
        target_path = f"{target_path}?{query_string}"
    upstream_ws_uri = f"ws://{upstream.host}:{upstream.port}{target_path}"

    # Forward all headers except hop-by-hop (RFC 6455/7230 compliance)
    client_headers = dict(websocket.headers)
    extra_headers = {
        k: v for k, v in client_headers.items()
        if k.lower() not in WS_HOP_BY_HOP_HEADERS
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
                    tg.create_task(_relay_client_to_backend(websocket, backend_ws))
                    tg.create_task(_relay_backend_to_client(websocket, backend_ws))
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
