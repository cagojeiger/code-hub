"""Workspace proxy module for code-hub.

Provides HTTP and WebSocket reverse proxy to workspace containers.
Routes: /w/{workspace_id}/* -> code-server container

Auth is not implemented yet (M6).
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from typing import Annotated

import httpx
import websockets
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import ClientConnection

from app.api.v1.dependencies import get_instance_controller
from app.core.errors import UpstreamUnavailableError, WorkspaceNotFoundError
from app.db import Workspace, get_async_session
from app.services.instance.interface import InstanceController

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


async def _get_workspace(session: AsyncSession, workspace_id: str) -> Workspace:
    """Get workspace by ID. Raises WorkspaceNotFoundError if not found."""
    result = await session.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,  # type: ignore[arg-type]
            Workspace.deleted_at.is_(None),  # type: ignore[union-attr]
        )
    )
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFoundError()
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
    session: DbSession,
    instance: Instance,
) -> StreamingResponse:
    """Proxy HTTP requests to workspace container."""
    # Verify workspace exists (auth check will be added in M6)
    await _get_workspace(session, workspace_id)

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

    # Read request body
    body = await request.body() if request.method in ("POST", "PUT", "PATCH") else None

    # Proxy request
    http_client = await get_http_client()

    try:
        upstream_request = http_client.build_request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
        )
        upstream_response = await http_client.send(upstream_request, stream=True)

        # Filter response headers
        response_headers = _filter_headers(dict(upstream_response.headers))

        async def stream_response() -> AsyncGenerator[bytes]:
            try:
                async for chunk in upstream_response.aiter_bytes():
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
    session: DbSession,
    instance: Instance,
) -> None:
    """Proxy WebSocket connections to workspace container."""
    # Verify workspace exists (auth check will be added in M6)
    try:
        await _get_workspace(session, workspace_id)
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
