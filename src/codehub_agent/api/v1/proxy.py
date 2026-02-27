"""Workspace proxy routes for Agent (Data Plane Gateway).

Agent receives forwarded traffic from CP and proxies to local workspace containers.
CP handles authentication/authorization; Agent handles container routing.

Flow: User → CP (auth) → FRP tunnel → Agent (this module) → workspace container
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator
from typing import Annotated, cast

import httpx
import websockets
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import ClientConnection

from codehub_agent.api.dependencies import get_runtime
from codehub_agent.logging_schema import LogEvent
from codehub_agent.metrics import (
    AGENT_PROXY_BYTES,
    AGENT_PROXY_IN_FLIGHT,
    AGENT_PROXY_REQUEST_DURATION,
    AGENT_PROXY_REQUESTS_TOTAL,
    AGENT_PROXY_UPSTREAM_CONNECT_DURATION,
    AGENT_PROXY_UPSTREAM_ERRORS,
    AGENT_PROXY_WS_ACTIVE,
    AGENT_PROXY_WS_CLOSE_TOTAL,
    AGENT_PROXY_WS_CONNECT_TOTAL,
    AGENT_PROXY_WS_ERRORS_TOTAL,
    AGENT_PROXY_WS_MESSAGES_TOTAL,
    AGENT_PROXY_WS_SESSION_DURATION,
)
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


def _status_class(status_code: int) -> str:
    if status_code < 200:
        return "1xx"
    if status_code < 300:
        return "2xx"
    if status_code < 400:
        return "3xx"
    if status_code < 500:
        return "4xx"
    return "5xx"


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
            AGENT_PROXY_BYTES.labels(direction="out").inc(len(chunk))
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
            text_payload = data.get("text")
            bytes_payload = data.get("bytes")
            if isinstance(text_payload, str):
                await backend_ws.send(text_payload)
                AGENT_PROXY_WS_MESSAGES_TOTAL.labels(direction="upstream").inc()
            elif isinstance(bytes_payload, bytes):
                await backend_ws.send(bytes_payload)
                AGENT_PROXY_WS_MESSAGES_TOTAL.labels(direction="upstream").inc()
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
            AGENT_PROXY_WS_MESSAGES_TOTAL.labels(direction="downstream").inc()
        else:
            await client_ws.send_bytes(message)
            AGENT_PROXY_WS_MESSAGES_TOTAL.labels(direction="downstream").inc()


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
    runtime: Annotated[RuntimeProtocol, Depends(get_runtime)],
) -> StreamingResponse | JSONResponse:
    """Proxy HTTP requests to local workspace container.

    Agent resolves the container name locally and proxies directly.
    No authentication here — CP already validated the request.
    """
    AGENT_PROXY_IN_FLIGHT.inc()
    request_started_at = time.monotonic()
    method = request.method.upper()
    try:
        upstream = await runtime.instances.get_upstream(workspace_id)
        target_path = f"/{path}" if path else "/"
        if request.url.query:
            target_path = f"{target_path}?{request.url.query}"
        target_url = f"{upstream.url}{target_path}"

        headers = _filter_headers(dict(request.headers))
        http_client = await get_http_client()
        content = request.stream() if method in ("POST", "PUT", "PATCH") else None
        content_length = request.headers.get("content-length")
        if content_length is not None:
            with contextlib.suppress(ValueError):
                AGENT_PROXY_BYTES.labels(direction="in").inc(int(content_length))

        try:
            upstream_request = http_client.build_request(
                method=method,
                url=target_url,
                headers=headers,
                content=content,
            )
            connect_started_at = time.monotonic()
            upstream_response = await http_client.send(upstream_request, stream=True)
            AGENT_PROXY_UPSTREAM_CONNECT_DURATION.observe(time.monotonic() - connect_started_at)
            response_headers = _filter_headers(dict(upstream_response.headers))
            AGENT_PROXY_REQUESTS_TOTAL.labels(
                method=method,
                status_class=_status_class(upstream_response.status_code),
            ).inc()

            return StreamingResponse(
                _stream_response(upstream_response),
                status_code=upstream_response.status_code,
                headers=response_headers,
            )
        except httpx.ConnectError as exc:
            AGENT_PROXY_UPSTREAM_ERRORS.labels(error_type="refused").inc()
            AGENT_PROXY_REQUESTS_TOTAL.labels(method=method, status_class="5xx").inc()
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
            AGENT_PROXY_UPSTREAM_ERRORS.labels(error_type="timeout").inc()
            AGENT_PROXY_REQUESTS_TOTAL.labels(method=method, status_class="5xx").inc()
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
    finally:
        AGENT_PROXY_REQUEST_DURATION.labels(method=method).observe(time.monotonic() - request_started_at)
        AGENT_PROXY_IN_FLIGHT.dec()


# ---------------------------------------------------------------------------
# WebSocket proxy endpoint
# ---------------------------------------------------------------------------


@router.websocket("/w/{workspace_id}/{path:path}")
async def proxy_websocket(
    websocket: WebSocket,
    workspace_id: str,
    path: str,
    runtime: Annotated[RuntimeProtocol, Depends(get_runtime)],
) -> None:
    """Proxy WebSocket connections to local workspace container.

    No authentication here — CP already validated the request.
    """
    upstream = await runtime.instances.get_upstream(workspace_id)
    target_path = f"/{path}" if path else "/"
    query_bytes = cast(bytes, websocket.scope.get("query_string", b""))
    query_string = query_bytes.decode()
    if query_string:
        target_path = f"{target_path}?{query_string}"
    upstream_ws_uri = f"{upstream.ws_url}{target_path}"

    extra_headers = {
        k: v
        for k, v in websocket.headers.items()
        if k.lower() not in WS_HOP_BY_HOP_HEADERS
    }

    try:
        connect_started_at = time.monotonic()
        backend_ws = await websockets.connect(
            upstream_ws_uri,
            additional_headers=extra_headers,
            ping_interval=20,
            ping_timeout=20,
            max_size=16 * 1024 * 1024,  # 16MB
        )
        AGENT_PROXY_UPSTREAM_CONNECT_DURATION.observe(time.monotonic() - connect_started_at)
    except Exception as exc:
        AGENT_PROXY_WS_ERRORS_TOTAL.labels(error_type="connect_failed").inc()
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
    AGENT_PROXY_WS_CONNECT_TOTAL.inc()
    AGENT_PROXY_WS_ACTIVE.inc()
    session_started_at = time.monotonic()
    close_code_class = "normal"

    try:
        async with backend_ws:
            try:
                async with asyncio.TaskGroup() as tg:
                    _ = tg.create_task(_relay_client_to_backend(websocket, backend_ws))
                    _ = tg.create_task(_relay_backend_to_client(websocket, backend_ws))
            except* WebSocketDisconnect:
                pass
            except* websockets.ConnectionClosed:
                pass
    except Exception as exc:
        AGENT_PROXY_WS_ERRORS_TOTAL.labels(error_type="relay_error").inc()
        close_code_class = "error"
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
        AGENT_PROXY_WS_CLOSE_TOTAL.labels(close_code_class=close_code_class).inc()
        AGENT_PROXY_WS_SESSION_DURATION.observe(time.monotonic() - session_started_at)
        AGENT_PROXY_WS_ACTIVE.dec()
        with contextlib.suppress(Exception):
            await websocket.close()
