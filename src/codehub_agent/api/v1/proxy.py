"""Workspace proxy routes for Agent (Data Plane Gateway).

Agent receives forwarded traffic from CP and proxies to local workspace containers.
CP handles authentication/authorization; Agent handles container routing.

Flow: User → CP (auth) → FRP tunnel → Agent (this module) → workspace container
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator

import httpx
import websockets
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import ClientConnection

from codehub_agent.api.dependencies import get_runtime
from codehub_agent.logging_schema import LogEvent
from codehub_agent.runtimes.protocols import RuntimeProtocol

logger = logging.getLogger(__name__)

router = APIRouter(tags=["proxy"])

# ---------------------------------------------------------------------------
# Hop-by-hop headers (must not be forwarded)
# ---------------------------------------------------------------------------

HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
    }
)

WS_HOP_BY_HOP_HEADERS = HOP_BY_HOP_HEADERS | frozenset(
    {"sec-websocket-key", "sec-websocket-version", "origin"}
)


def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    """Filter out hop-by-hop headers."""
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}


# ---------------------------------------------------------------------------
# Shared HTTP client
# ---------------------------------------------------------------------------

_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()


async def get_http_client() -> httpx.AsyncClient:
    """Get or create shared httpx AsyncClient for proxying."""
    global _http_client
    if _http_client is not None:
        return _http_client

    async with _http_client_lock:
        if _http_client is None:
            _http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    timeout=300.0,
                    connect=10.0,
                    read=300.0,
                    write=300.0,
                    pool=10.0,
                ),
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                    keepalive_expiry=30.0,
                ),
            )
    return _http_client


async def close_http_client() -> None:
    """Close shared httpx client. Called during shutdown."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


# ---------------------------------------------------------------------------
# HTTP streaming helper
# ---------------------------------------------------------------------------


async def _stream_response(
    upstream_response: httpx.Response,
) -> AsyncGenerator[bytes, None]:
    """Stream response chunks from upstream and ensure cleanup."""
    try:
        async for chunk in upstream_response.aiter_raw():
            yield chunk
    finally:
        await upstream_response.aclose()


# ---------------------------------------------------------------------------
# WebSocket relay helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# HTTP proxy endpoint
# ---------------------------------------------------------------------------


@router.api_route(
    "/w/{workspace_id}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    response_model=None,
)
async def proxy_http(
    workspace_id: str,
    path: str,
    request: Request,
    runtime: RuntimeProtocol = Depends(get_runtime),
) -> StreamingResponse | JSONResponse:
    """Proxy HTTP requests to local workspace container.

    Agent resolves the container name locally and proxies directly.
    No authentication here — CP already validated the request.
    """
    upstream = await runtime.instances.get_upstream(workspace_id)
    target_path = f"/{path}" if path else "/"
    if request.url.query:
        target_path = f"{target_path}?{request.url.query}"
    target_url = f"{upstream.url}{target_path}"

    headers = _filter_headers(dict(request.headers))
    http_client = await get_http_client()
    content = request.stream() if request.method in ("POST", "PUT", "PATCH") else None

    try:
        upstream_request = http_client.build_request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=content,
        )
        upstream_response = await http_client.send(upstream_request, stream=True)
        response_headers = _filter_headers(dict(upstream_response.headers))

        return StreamingResponse(
            _stream_response(upstream_response),
            status_code=upstream_response.status_code,
            headers=response_headers,
        )
    except httpx.ConnectError as exc:
        logger.warning(
            "Proxy connection error to workspace container",
            extra={
                "event": LogEvent.PROXY_ERROR,
                "ws_id": workspace_id,
                "target_url": target_url,
                "error_type": "connection_error",
                "error": str(exc),
            },
        )
        return JSONResponse(
            status_code=502,
            content={"detail": "Workspace container unavailable"},
        )
    except httpx.TimeoutException as exc:
        logger.warning(
            "Proxy timeout to workspace container",
            extra={
                "event": LogEvent.PROXY_ERROR,
                "ws_id": workspace_id,
                "target_url": target_url,
                "error_type": "timeout",
                "error": str(exc),
            },
        )
        return JSONResponse(
            status_code=504,
            content={"detail": "Workspace container timeout"},
        )


# ---------------------------------------------------------------------------
# WebSocket proxy endpoint
# ---------------------------------------------------------------------------


@router.websocket("/w/{workspace_id}/{path:path}")
async def proxy_websocket(
    websocket: WebSocket,
    workspace_id: str,
    path: str,
    runtime: RuntimeProtocol = Depends(get_runtime),
) -> None:
    """Proxy WebSocket connections to local workspace container.

    No authentication here — CP already validated the request.
    """
    upstream = await runtime.instances.get_upstream(workspace_id)
    target_path = f"/{path}" if path else "/"
    query_string = websocket.scope.get("query_string", b"").decode()
    if query_string:
        target_path = f"{target_path}?{query_string}"
    upstream_ws_uri = f"{upstream.ws_url}{target_path}"

    extra_headers = {
        k: v
        for k, v in websocket.headers.items()
        if k.lower() not in WS_HOP_BY_HOP_HEADERS
    }

    try:
        backend_ws = await websockets.connect(
            upstream_ws_uri,
            additional_headers=extra_headers,
            ping_interval=20,
            ping_timeout=20,
            max_size=16 * 1024 * 1024,  # 16MB
        )
    except Exception as exc:
        logger.warning(
            "WebSocket connection to workspace failed",
            extra={
                "event": LogEvent.PROXY_WS_ERROR,
                "ws_id": workspace_id,
                "upstream_url": upstream_ws_uri,
                "error": str(exc),
            },
        )
        await websocket.close(code=1011, reason="Upstream connection failed")
        return

    await websocket.accept()

    try:
        async with backend_ws:
            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(
                        _relay_client_to_backend(websocket, backend_ws)
                    )
                    tg.create_task(
                        _relay_backend_to_client(websocket, backend_ws)
                    )
            except* WebSocketDisconnect:
                pass
            except* websockets.ConnectionClosed:
                pass
    except Exception as exc:
        logger.error(
            "WebSocket proxy relay error",
            extra={
                "event": LogEvent.PROXY_WS_ERROR,
                "ws_id": workspace_id,
                "upstream_url": upstream_ws_uri,
                "error": str(exc),
            },
        )
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()
