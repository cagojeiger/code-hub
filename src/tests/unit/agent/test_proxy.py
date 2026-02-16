"""Unit tests for Agent workspace proxy (proxy.py).

Tests cover:
- Header filtering (hop-by-hop removal)
- HTTP proxy (upstream routing, streaming, error handling)
- WebSocket proxy (relay, upstream connection failure)
- HTTP client singleton lifecycle
"""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from codehub_agent.api.v1.proxy import (
    HOP_BY_HOP_HEADERS,
    WS_HOP_BY_HOP_HEADERS,
    _filter_headers,
    _relay_backend_to_client,
    _relay_client_to_backend,
    close_http_client,
    get_http_client,
)


async def _async_iter(items: list[Any]) -> AsyncIterator[Any]:
    for item in items:
        yield item


class TestFilterHeaders:
    """_filter_headers() tests."""

    def test_removes_hop_by_hop_headers(self):
        """All hop-by-hop headers are removed."""
        headers = {
            "content-type": "application/json",
            "connection": "keep-alive",
            "keep-alive": "timeout=5",
            "transfer-encoding": "chunked",
            "host": "localhost",
            "x-custom": "value",
        }

        result = _filter_headers(headers)

        assert "content-type" in result
        assert "x-custom" in result
        assert "connection" not in result
        assert "keep-alive" not in result
        assert "transfer-encoding" not in result
        assert "host" not in result

    def test_case_insensitive_filtering(self):
        """Header filtering is case-insensitive."""
        headers = {
            "Connection": "keep-alive",
            "Host": "localhost",
            "Content-Type": "text/html",
        }

        result = _filter_headers(headers)

        assert "Content-Type" in result
        assert len(result) == 1

    def test_empty_headers(self):
        """Empty headers dict returns empty dict."""
        assert _filter_headers({}) == {}

    def test_no_hop_by_hop_headers(self):
        """Headers without hop-by-hop pass through unchanged."""
        headers = {
            "content-type": "application/json",
            "authorization": "Bearer token",
            "x-forwarded-for": "1.2.3.4",
        }

        result = _filter_headers(headers)

        assert result == headers

    def test_ws_hop_by_hop_superset(self):
        """WS hop-by-hop headers are a superset of HTTP hop-by-hop."""
        assert HOP_BY_HOP_HEADERS.issubset(WS_HOP_BY_HOP_HEADERS)
        assert "sec-websocket-key" in WS_HOP_BY_HOP_HEADERS
        assert "sec-websocket-version" in WS_HOP_BY_HOP_HEADERS
        assert "origin" in WS_HOP_BY_HOP_HEADERS


class TestRelayClientToBackend:
    """Agent proxy _relay_client_to_backend() tests."""

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
                {"type": "websocket.receive", "bytes": b"\x00\x01"},
                {"type": "websocket.disconnect"},
            ]
        )

        await _relay_client_to_backend(mock_client_ws, mock_backend_ws)

        mock_backend_ws.send.assert_called_once_with(b"\x00\x01")

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
    """Agent proxy _relay_backend_to_client() tests."""

    async def test_forwards_text_message(self) -> None:
        """Text message from backend is forwarded to client."""
        mock_client_ws: Any = AsyncMock()
        mock_backend_ws: Any = _async_iter(["hello from backend"])

        await _relay_backend_to_client(mock_client_ws, mock_backend_ws)

        mock_client_ws.send_text.assert_called_once_with("hello from backend")

    async def test_forwards_bytes_message(self) -> None:
        """Bytes message from backend is forwarded to client."""
        mock_client_ws: Any = AsyncMock()
        mock_backend_ws: Any = _async_iter([b"\x00\x01\x02"])

        await _relay_backend_to_client(mock_client_ws, mock_backend_ws)

        mock_client_ws.send_bytes.assert_called_once_with(b"\x00\x01\x02")

    async def test_forwards_multiple_messages(self) -> None:
        """Multiple messages are all forwarded."""
        mock_client_ws: Any = AsyncMock()
        mock_backend_ws: Any = _async_iter(["msg1", b"\x00", "msg3"])

        await _relay_backend_to_client(mock_client_ws, mock_backend_ws)

        assert mock_client_ws.send_text.call_count == 2
        assert mock_client_ws.send_bytes.call_count == 1


class TestHttpClient:
    """get_http_client() / close_http_client() singleton tests."""

    async def test_creates_client_on_first_call(self):
        """First call creates a new httpx.AsyncClient."""
        import codehub_agent.api.v1.proxy as proxy_module

        proxy_module._http_client = None

        client = await get_http_client()

        assert isinstance(client, httpx.AsyncClient)

        await close_http_client()

    async def test_returns_same_client_on_subsequent_calls(self):
        """Subsequent calls return the same client instance."""
        import codehub_agent.api.v1.proxy as proxy_module

        proxy_module._http_client = None

        client1 = await get_http_client()
        client2 = await get_http_client()

        assert client1 is client2

        await close_http_client()

    async def test_close_sets_none(self):
        """close_http_client sets the global to None."""
        import codehub_agent.api.v1.proxy as proxy_module

        proxy_module._http_client = None

        await get_http_client()
        await close_http_client()

        assert proxy_module._http_client is None

    async def test_close_noop_when_none(self):
        """close_http_client is safe to call when no client exists."""
        import codehub_agent.api.v1.proxy as proxy_module

        proxy_module._http_client = None

        await close_http_client()

        assert proxy_module._http_client is None


class TestProxyHttp:
    """proxy_http() endpoint tests via ASGI TestClient."""

    @pytest.fixture
    def mock_runtime(self):
        """Mock RuntimeProtocol with UpstreamInfo."""
        from codehub_agent.runtimes.docker.instance import UpstreamInfo

        runtime = AsyncMock()
        runtime.instances = AsyncMock()
        runtime.instances.get_upstream = AsyncMock(
            return_value=UpstreamInfo(hostname="codehub-ws1", port=8080)
        )
        return runtime

    async def test_proxy_http_success(self, mock_runtime):
        """Successful HTTP proxy returns upstream response."""
        from codehub_agent.api.v1.proxy import proxy_http

        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.headers = {"content-type": "text/html", "host": "localhost"}
        mock_request.url = MagicMock()
        mock_request.url.query = ""

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html", "x-custom": "value"}

        async def fake_aiter_raw():
            yield b"<html>OK</html>"

        mock_response.aiter_raw = fake_aiter_raw
        mock_response.aclose = AsyncMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.send = AsyncMock(return_value=mock_response)

        with patch(
            "codehub_agent.api.v1.proxy.get_http_client",
            return_value=mock_client,
        ):
            result = await proxy_http("ws1", "some/path", mock_request, mock_runtime)

        assert result.status_code == 200
        mock_runtime.instances.get_upstream.assert_called_once_with("ws1")

    async def test_proxy_http_with_query_string(self, mock_runtime):
        """Query string is preserved in upstream request."""
        from codehub_agent.api.v1.proxy import proxy_http

        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.headers = {"content-type": "text/html"}
        mock_request.url = MagicMock()
        mock_request.url.query = "folder=/home/coder&tab=1"

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}

        async def fake_aiter_raw():
            yield b"OK"

        mock_response.aiter_raw = fake_aiter_raw
        mock_response.aclose = AsyncMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.send = AsyncMock(return_value=mock_response)

        with patch(
            "codehub_agent.api.v1.proxy.get_http_client",
            return_value=mock_client,
        ):
            await proxy_http("ws1", "path", mock_request, mock_runtime)

        build_call = mock_client.build_request.call_args
        target_url = build_call.kwargs.get("url") or build_call[1].get("url")
        assert "folder=/home/coder&tab=1" in target_url

    async def test_proxy_http_connect_error(self, mock_runtime):
        """ConnectError returns 502 with proper message."""
        from codehub_agent.api.v1.proxy import proxy_http

        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.headers = {}
        mock_request.url = MagicMock()
        mock_request.url.query = ""

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.send = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with patch(
            "codehub_agent.api.v1.proxy.get_http_client",
            return_value=mock_client,
        ):
            result = await proxy_http("ws1", "path", mock_request, mock_runtime)

        assert result.status_code == 502

    async def test_proxy_http_timeout_error(self, mock_runtime):
        """TimeoutException returns 504 with proper message."""
        from codehub_agent.api.v1.proxy import proxy_http

        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.headers = {}
        mock_request.url = MagicMock()
        mock_request.url.query = ""

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.send = AsyncMock(
            side_effect=httpx.TimeoutException("Read timed out")
        )

        with patch(
            "codehub_agent.api.v1.proxy.get_http_client",
            return_value=mock_client,
        ):
            result = await proxy_http("ws1", "path", mock_request, mock_runtime)

        assert result.status_code == 504

    async def test_proxy_http_empty_path(self, mock_runtime):
        """Empty path resolves to root /."""
        from codehub_agent.api.v1.proxy import proxy_http

        mock_request = MagicMock()
        mock_request.method = "GET"
        mock_request.headers = {}
        mock_request.url = MagicMock()
        mock_request.url.query = ""

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.headers = {}

        async def fake_aiter_raw():
            yield b"OK"

        mock_response.aiter_raw = fake_aiter_raw
        mock_response.aclose = AsyncMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.send = AsyncMock(return_value=mock_response)

        with patch(
            "codehub_agent.api.v1.proxy.get_http_client",
            return_value=mock_client,
        ):
            await proxy_http("ws1", "", mock_request, mock_runtime)

        build_call = mock_client.build_request.call_args
        target_url = build_call.kwargs.get("url") or build_call[1].get("url")
        assert target_url == "http://codehub-ws1:8080/"

    async def test_proxy_http_post_streams_body(self, mock_runtime):
        """POST request streams request body to upstream."""
        from codehub_agent.api.v1.proxy import proxy_http

        mock_stream = AsyncMock()
        mock_request = MagicMock()
        mock_request.method = "POST"
        mock_request.headers = {"content-type": "application/json"}
        mock_request.url = MagicMock()
        mock_request.url.query = ""
        mock_request.stream = MagicMock(return_value=mock_stream)

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 201
        mock_response.headers = {}

        async def fake_aiter_raw():
            yield b'{"ok": true}'

        mock_response.aiter_raw = fake_aiter_raw
        mock_response.aclose = AsyncMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.send = AsyncMock(return_value=mock_response)

        with patch(
            "codehub_agent.api.v1.proxy.get_http_client",
            return_value=mock_client,
        ):
            result = await proxy_http("ws1", "api/data", mock_request, mock_runtime)

        assert result.status_code == 201
        build_call = mock_client.build_request.call_args
        content = build_call.kwargs.get("content") or build_call[1].get("content")
        assert content is mock_stream
