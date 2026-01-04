"""WebSocket relay functions for workspace proxy."""

from starlette.websockets import WebSocket
from websockets.asyncio.client import ClientConnection

from .activity import get_activity_buffer


async def relay_client_to_backend(
    client_ws: WebSocket,
    backend_ws: ClientConnection,
    workspace_id: str,
) -> None:
    """Relay messages from client WebSocket to backend WebSocket."""
    while True:
        data = await client_ws.receive()
        if data["type"] == "websocket.receive":
            get_activity_buffer().record(workspace_id)
            if "text" in data:
                await backend_ws.send(data["text"])
            elif "bytes" in data:
                await backend_ws.send(data["bytes"])
        elif data["type"] == "websocket.disconnect":
            break


async def relay_backend_to_client(
    client_ws: WebSocket,
    backend_ws: ClientConnection,
    workspace_id: str,
) -> None:
    """Relay messages from backend WebSocket to client WebSocket."""
    async for message in backend_ws:
        get_activity_buffer().record(workspace_id)
        if isinstance(message, str):
            await client_ws.send_text(message)
        else:
            await client_ws.send_bytes(message)
