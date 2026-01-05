"""Tests for WebSocket relay functions in transport module.

Verifies that WebSocket messages trigger activity recording.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codehub.app.proxy.transport import (
    _relay_client_to_backend,
    _relay_backend_to_client,
)


class TestRelayClientToBackend:
    """_relay_client_to_backend() tests."""

    async def test_records_activity_on_text_message(self):
        """record() is called when client sends text message."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = AsyncMock()
        workspace_id = "test-ws-123"

        # Simulate: receive text message, then disconnect
        mock_client_ws.receive = AsyncMock(
            side_effect=[
                {"type": "websocket.receive", "text": "hello"},
                {"type": "websocket.disconnect"},
            ]
        )

        with patch(
            "codehub.app.proxy.transport.get_activity_buffer"
        ) as mock_get_buffer:
            mock_buffer = MagicMock()
            mock_get_buffer.return_value = mock_buffer

            await _relay_client_to_backend(mock_client_ws, mock_backend_ws, workspace_id)

            # record() should be called once
            mock_buffer.record.assert_called_once_with(workspace_id)
            # Text should be sent to backend
            mock_backend_ws.send.assert_called_once_with("hello")

    async def test_records_activity_on_bytes_message(self):
        """record() is called when client sends bytes message."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = AsyncMock()
        workspace_id = "test-ws-123"

        mock_client_ws.receive = AsyncMock(
            side_effect=[
                {"type": "websocket.receive", "bytes": b"\x00\x01\x02"},
                {"type": "websocket.disconnect"},
            ]
        )

        with patch(
            "codehub.app.proxy.transport.get_activity_buffer"
        ) as mock_get_buffer:
            mock_buffer = MagicMock()
            mock_get_buffer.return_value = mock_buffer

            await _relay_client_to_backend(mock_client_ws, mock_backend_ws, workspace_id)

            mock_buffer.record.assert_called_once_with(workspace_id)
            mock_backend_ws.send.assert_called_once_with(b"\x00\x01\x02")

    async def test_records_activity_on_each_message(self):
        """record() is called for each message."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = AsyncMock()
        workspace_id = "test-ws-123"

        # 3 messages before disconnect
        mock_client_ws.receive = AsyncMock(
            side_effect=[
                {"type": "websocket.receive", "text": "msg1"},
                {"type": "websocket.receive", "text": "msg2"},
                {"type": "websocket.receive", "text": "msg3"},
                {"type": "websocket.disconnect"},
            ]
        )

        with patch(
            "codehub.app.proxy.transport.get_activity_buffer"
        ) as mock_get_buffer:
            mock_buffer = MagicMock()
            mock_get_buffer.return_value = mock_buffer

            await _relay_client_to_backend(mock_client_ws, mock_backend_ws, workspace_id)

            # record() should be called 3 times
            assert mock_buffer.record.call_count == 3

    async def test_stops_on_disconnect(self):
        """Relay stops when client disconnects."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = AsyncMock()
        workspace_id = "test-ws-123"

        mock_client_ws.receive = AsyncMock(
            return_value={"type": "websocket.disconnect"}
        )

        with patch(
            "codehub.app.proxy.transport.get_activity_buffer"
        ) as mock_get_buffer:
            mock_buffer = MagicMock()
            mock_get_buffer.return_value = mock_buffer

            await _relay_client_to_backend(mock_client_ws, mock_backend_ws, workspace_id)

            # No messages received, no record() calls
            mock_buffer.record.assert_not_called()


class TestRelayBackendToClient:
    """_relay_backend_to_client() tests."""

    async def test_records_activity_on_text_message(self):
        """record() is called when backend sends text message."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = MagicMock()
        workspace_id = "test-ws-123"

        # Mock async iterator
        async def mock_iter():
            yield "hello from backend"

        mock_backend_ws.__aiter__ = lambda self: mock_iter()

        with patch(
            "codehub.app.proxy.transport.get_activity_buffer"
        ) as mock_get_buffer:
            mock_buffer = MagicMock()
            mock_get_buffer.return_value = mock_buffer

            await _relay_backend_to_client(mock_client_ws, mock_backend_ws, workspace_id)

            mock_buffer.record.assert_called_once_with(workspace_id)
            mock_client_ws.send_text.assert_called_once_with("hello from backend")

    async def test_records_activity_on_bytes_message(self):
        """record() is called when backend sends bytes message."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = MagicMock()
        workspace_id = "test-ws-123"

        async def mock_iter():
            yield b"\x00\x01\x02"

        mock_backend_ws.__aiter__ = lambda self: mock_iter()

        with patch(
            "codehub.app.proxy.transport.get_activity_buffer"
        ) as mock_get_buffer:
            mock_buffer = MagicMock()
            mock_get_buffer.return_value = mock_buffer

            await _relay_backend_to_client(mock_client_ws, mock_backend_ws, workspace_id)

            mock_buffer.record.assert_called_once_with(workspace_id)
            mock_client_ws.send_bytes.assert_called_once_with(b"\x00\x01\x02")

    async def test_records_activity_on_each_message(self):
        """record() is called for each backend message."""
        mock_client_ws = AsyncMock()
        mock_backend_ws = MagicMock()
        workspace_id = "test-ws-123"

        async def mock_iter():
            yield "msg1"
            yield "msg2"
            yield b"msg3"

        mock_backend_ws.__aiter__ = lambda self: mock_iter()

        with patch(
            "codehub.app.proxy.transport.get_activity_buffer"
        ) as mock_get_buffer:
            mock_buffer = MagicMock()
            mock_get_buffer.return_value = mock_buffer

            await _relay_backend_to_client(mock_client_ws, mock_backend_ws, workspace_id)

            # record() should be called 3 times
            assert mock_buffer.record.call_count == 3
