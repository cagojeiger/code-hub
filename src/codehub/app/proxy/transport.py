"""Forward proxy transport: CP -> Agent (via FRP tunnel).

CP authenticates the user and forwards the request to Agent.
Agent handles the actual proxy to workspace containers.
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
from codehub.core.logging_schema import LogEvent

from .client import WS_HOP_BY_HOP_HEADERS, filter_headers, get_http_client

logger = logging.getLogger(__name__)

_proxy_config = get_settings().proxy


async def _stream_response(
    upstream_response: httpx.Response,
) -> AsyncGenerator[bytes, None]:
    try:
        async for chunk in upstream_response.aiter_raw():
            yield chunk
    finally:
        await upstream_response.aclose()


async def forward_http_to_agent(
    request: Request,
    agent_endpoint: str,
    workspace_id: str,
    path: str,
) -> StreamingResponse:
    target_path = f"/w/{workspace_id}/{path}" if path else f"/w/{workspace_id}/"
    if request.url.query:
        target_path = f"{target_path}?{request.url.query}"
    target_url = f"{agent_endpoint}{target_path}"

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

        return StreamingResponse(
            _stream_response(upstream_response),
            status_code=upstream_response.status_code,
            headers=response_headers,
        )
    except httpx.ConnectError as exc:
        logger.warning(
            "Connection error to Agent",
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
            "Timeout connecting to Agent",
            extra={
                "event": LogEvent.UPSTREAM_ERROR,
                "ws_id": workspace_id,
                "target_url": target_url,
                "error_type": "timeout",
                "error": str(exc),
            },
        )
        raise UpstreamUnavailableError() from exc


async def _relay_client_to_backend(
    client_ws: WebSocket,
    backend_ws: ClientConnection,
) -> None:
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
    async for message in backend_ws:
        if isinstance(message, str):
            await client_ws.send_text(message)
        else:
            await client_ws.send_bytes(message)


async def forward_ws_to_agent(
    websocket: WebSocket,
    agent_endpoint: str,
    workspace_id: str,
    path: str,
) -> None:
    target_path = f"/w/{workspace_id}/{path}" if path else f"/w/{workspace_id}/"
    query_string = websocket.scope.get("query_string", b"").decode()
    if query_string:
        target_path = f"{target_path}?{query_string}"

    ws_endpoint = agent_endpoint.replace("http://", "ws://").replace(
        "https://", "wss://"
    )
    upstream_ws_uri = f"{ws_endpoint}{target_path}"

    extra_headers = {
        k: v
        for k, v in websocket.headers.items()
        if k.lower() not in WS_HOP_BY_HOP_HEADERS
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
    except Exception as exc:
        logger.warning(
            "WebSocket connection to Agent failed",
            extra={
                "event": LogEvent.WS_ERROR,
                "ws_id": workspace_id,
                "upstream_url": upstream_ws_uri,
                "error": str(exc),
            },
        )
        await websocket.close(code=1011, reason="Agent connection failed")
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
            "WebSocket relay error",
            extra={
                "event": LogEvent.WS_ERROR,
                "ws_id": workspace_id,
                "upstream_url": upstream_ws_uri,
                "error": str(exc),
            },
        )
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()
