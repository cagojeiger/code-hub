"""Tests for WebSocket relay and HTTP forwarding in transport module.

CP transport forwards requests to Agent (via FRP tunnel).
Activity recording is handled by the router, not the relay functions.
"""

from unittest.mock import AsyncMock, MagicMock

from codehub.app.proxy.transport import (
    _relay_client_to_backend,
    _relay_backend_to_client,
)


class TestRelayClientToBackend:
    """_relay_client_to_backend() tests."""

    async def test_forwards_text_message(self):
        """Text message is forwarded to backend."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = AsyncMock()

        mock_client_ws.receive = AsyncMock(
            side_effect=[
                {"type": "websocket.receive", "text": "hello"},
                {"type": "websocket.disconnect"},
            ]
        )

        await _relay_client_to_backend(mock_client_ws, mock_backend_ws)

        mock_backend_ws.send.assert_called_once_with("hello")

    async def test_forwards_bytes_message(self):
        """Bytes message is forwarded to backend."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = AsyncMock()

        mock_client_ws.receive = AsyncMock(
            side_effect=[
                {"type": "websocket.receive", "bytes": b"\x00\x01\x02"},
                {"type": "websocket.disconnect"},
            ]
        )

        await _relay_client_to_backend(mock_client_ws, mock_backend_ws)

        mock_backend_ws.send.assert_called_once_with(b"\x00\x01\x02")

    async def test_forwards_multiple_messages(self):
        """Multiple messages are all forwarded."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = AsyncMock()

        mock_client_ws.receive = AsyncMock(
            side_effect=[
                {"type": "websocket.receive", "text": "msg1"},
                {"type": "websocket.receive", "text": "msg2"},
                {"type": "websocket.receive", "text": "msg3"},
                {"type": "websocket.disconnect"},
            ]
        )

        await _relay_client_to_backend(mock_client_ws, mock_backend_ws)

        assert mock_backend_ws.send.call_count == 3

    async def test_stops_on_disconnect(self):
        """Relay stops when client disconnects."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = AsyncMock()

        mock_client_ws.receive = AsyncMock(
            return_value={"type": "websocket.disconnect"}
        )

        await _relay_client_to_backend(mock_client_ws, mock_backend_ws)

        mock_backend_ws.send.assert_not_called()


class TestRelayBackendToClient:
    """_relay_backend_to_client() tests."""

    async def test_forwards_text_message(self):
        """Text message from backend is forwarded to client."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = MagicMock()

        async def mock_iter():
            yield "hello from backend"

        mock_backend_ws.__aiter__ = lambda self: mock_iter()

        await _relay_backend_to_client(mock_client_ws, mock_backend_ws)

        mock_client_ws.send_text.assert_called_once_with("hello from backend")

    async def test_forwards_bytes_message(self):
        """Bytes message from backend is forwarded to client."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = MagicMock()

        async def mock_iter():
            yield b"\x00\x01\x02"

        mock_backend_ws.__aiter__ = lambda self: mock_iter()

        await _relay_backend_to_client(mock_client_ws, mock_backend_ws)

        mock_client_ws.send_bytes.assert_called_once_with(b"\x00\x01\x02")

    async def test_forwards_multiple_messages(self):
        """Multiple backend messages are all forwarded."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = MagicMock()

        async def mock_iter():
            yield "msg1"
            yield "msg2"
            yield b"msg3"

        mock_backend_ws.__aiter__ = lambda self: mock_iter()

        await _relay_backend_to_client(mock_client_ws, mock_backend_ws)

        assert mock_client_ws.send_text.call_count == 2
        mock_client_ws.send_bytes.assert_called_once_with(b"msg3")
