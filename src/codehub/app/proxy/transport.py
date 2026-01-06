"""HTTP and WebSocket transport to upstream containers.

Configuration via ProxyConfig (PROXY_ env prefix).
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator

import httpx
import websockets
from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from websockets.asyncio.client import ClientConnection

from codehub.app.config import get_settings
from codehub.core.errors import UpstreamUnavailableError
from codehub.core.interfaces import UpstreamInfo

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
            _activity_buffer.record(workspace_id)
            if "text" in data:
                await backend_ws.send(data["text"])
            elif "bytes" in data:
                await backend_ws.send(data["bytes"])
        elif data["type"] == "websocket.disconnect":
            break


async def _relay_backend_to_client(
    client_ws: WebSocket,
    backend_ws: ClientConnection,
    workspace_id: str,
) -> None:
    """Relay messages from backend WebSocket to client WebSocket."""
    async for message in backend_ws:
        _activity_buffer.record(workspace_id)
        if isinstance(message, str):
            await client_ws.send_text(message)
        else:
            await client_ws.send_bytes(message)


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
        logger.warning("Connection error to upstream %s: %s", workspace_id, exc)
        raise UpstreamUnavailableError() from exc
    except httpx.TimeoutException as exc:
        logger.warning("Timeout connecting to upstream %s: %s", workspace_id, exc)
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

    await websocket.accept()

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
                pass
    except Exception as exc:
        logger.error("WebSocket proxy error for %s: %s", workspace_id, exc)
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()
