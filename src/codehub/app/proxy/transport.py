"""HTTP and WebSocket transport to upstream containers.

Configuration via ProxyConfig (PROXY_ env prefix).
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator

import httpx
import websockets
from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from websockets.asyncio.client import ClientConnection

from codehub.app.config import get_settings
from codehub.app.metrics.collector import (
    PROXY_WS_ACTIVE_CONNECTIONS,
    PROXY_WS_ERRORS,
    PROXY_WS_MESSAGE_LATENCY,
)
from codehub.core.errors import UpstreamUnavailableError
from codehub.core.interfaces import UpstreamInfo
from codehub.core.logging_schema import LogEvent

from .activity import get_activity_buffer
from .client import WS_HOP_BY_HOP_HEADERS, filter_headers, get_http_client

logger = logging.getLogger(__name__)

_proxy_config = get_settings().proxy
_activity_buffer = get_activity_buffer()


async def _relay_client_to_backend(
    client_ws: WebSocket,
    backend_ws: ClientConnection,
    workspace_id: str,
) -> None:
    """Relay messages from client WebSocket to backend WebSocket."""
    while True:
        data = await client_ws.receive()
        if data["type"] == "websocket.receive":
            start = time.perf_counter()
            _activity_buffer.record(workspace_id)
            if "text" in data:
                await backend_ws.send(data["text"])
            elif "bytes" in data:
                await backend_ws.send(data["bytes"])
            PROXY_WS_MESSAGE_LATENCY.labels(direction="client_to_backend").observe(
                time.perf_counter() - start
            )
        elif data["type"] == "websocket.disconnect":
            break


async def _relay_backend_to_client(
    client_ws: WebSocket,
    backend_ws: ClientConnection,
    workspace_id: str,
) -> None:
    """Relay messages from backend WebSocket to client WebSocket."""
    async for message in backend_ws:
        start = time.perf_counter()
        _activity_buffer.record(workspace_id)
        if isinstance(message, str):
            await client_ws.send_text(message)
        else:
            await client_ws.send_bytes(message)
        PROXY_WS_MESSAGE_LATENCY.labels(direction="backend_to_client").observe(
            time.perf_counter() - start
        )


async def proxy_http_to_upstream(
    request: Request,
    upstream: UpstreamInfo,
    path: str,
    workspace_id: str,
) -> StreamingResponse:
    """Proxy HTTP request to upstream. Raises UpstreamUnavailableError on failure."""
    target_path = f"/{path}" if path else "/"
    if request.url.query:
        target_path = f"{target_path}?{request.url.query}"
    target_url = f"{upstream.url}{target_path}"

    headers = filter_headers(dict(request.headers))
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
        response_headers = filter_headers(dict(upstream_response.headers))

        async def stream_response() -> AsyncGenerator[bytes]:
            try:
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
        logger.warning(
            "Connection error to upstream",
            extra={
                "event": LogEvent.UPSTREAM_ERROR,
                "ws_id": workspace_id,
                "target_url": target_url,
                "error_type": "connection_error",
                "error": str(exc),
            },
        )
        raise UpstreamUnavailableError() from exc
    except httpx.TimeoutException as exc:
        logger.warning(
            "Timeout connecting to upstream",
            extra={
                "event": LogEvent.UPSTREAM_ERROR,
                "ws_id": workspace_id,
                "target_url": target_url,
                "error_type": "timeout",
                "error": str(exc),
            },
        )
        raise UpstreamUnavailableError() from exc


async def proxy_ws_to_upstream(
    websocket: WebSocket,
    upstream: UpstreamInfo,
    path: str,
    workspace_id: str,
) -> None:
    """Proxy WebSocket to upstream and relay messages."""
    target_path = f"/{path}" if path else "/"
    query_string = websocket.scope.get("query_string", b"").decode()
    if query_string:
        target_path = f"{target_path}?{query_string}"
    upstream_ws_uri = f"{upstream.ws_url}{target_path}"

    extra_headers = {
        k: v for k, v in websocket.headers.items() if k.lower() not in WS_HOP_BY_HOP_HEADERS
    }

    try:
        backend_ws = await websockets.connect(
            upstream_ws_uri,
            additional_headers=extra_headers,
            ping_interval=_proxy_config.ws_ping_interval,
            ping_timeout=_proxy_config.ws_ping_timeout,
            max_size=_proxy_config.ws_max_size,
            max_queue=_proxy_config.ws_max_queue,
        )
    except websockets.InvalidURI as exc:
        PROXY_WS_ERRORS.labels(error_type="invalid_uri").inc()
        logger.warning(
            "Invalid WebSocket URI",
            extra={
                "event": LogEvent.WS_ERROR,
                "ws_id": workspace_id,
                "upstream_url": upstream_ws_uri,
                "error_type": "invalid_uri",
                "error": str(exc),
            },
        )
        await websocket.close(code=1011, reason="Invalid upstream URI")
        return
    except websockets.InvalidHandshake as exc:
        PROXY_WS_ERRORS.labels(error_type="handshake_failed").inc()
        logger.warning(
            "WebSocket handshake failed",
            extra={
                "event": LogEvent.WS_ERROR,
                "ws_id": workspace_id,
                "upstream_url": upstream_ws_uri,
                "error_type": "handshake_failed",
                "error": str(exc),
            },
        )
        await websocket.close(code=1011, reason="Upstream handshake failed")
        return
    except Exception as exc:
        PROXY_WS_ERRORS.labels(error_type="connection_failed").inc()
        logger.warning(
            "Failed to connect to upstream WebSocket",
            extra={
                "event": LogEvent.WS_ERROR,
                "ws_id": workspace_id,
                "upstream_url": upstream_ws_uri,
                "error_type": "connection_failed",
                "error": str(exc),
            },
        )
        await websocket.close(code=1011, reason="Upstream connection failed")
        return

    await websocket.accept()
    PROXY_WS_ACTIVE_CONNECTIONS.inc()

    try:
        async with backend_ws:
            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(
                        _relay_client_to_backend(websocket, backend_ws, workspace_id)
                    )
                    tg.create_task(
                        _relay_backend_to_client(websocket, backend_ws, workspace_id)
                    )
            except* WebSocketDisconnect:
                pass
            except* websockets.ConnectionClosed:
                PROXY_WS_ERRORS.labels(error_type="connection_closed").inc()
    except Exception as exc:
        PROXY_WS_ERRORS.labels(error_type="relay_error").inc()
        logger.error(
            "WebSocket proxy error",
            extra={
                "event": LogEvent.WS_ERROR,
                "ws_id": workspace_id,
                "upstream_url": upstream_ws_uri,
                "error_type": "relay_error",
                "error": str(exc),
            },
        )
    finally:
        PROXY_WS_ACTIVE_CONNECTIONS.dec()
        with contextlib.suppress(Exception):
            await websocket.close()
