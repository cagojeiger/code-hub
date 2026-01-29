"""Tests for middleware and exception handlers."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.responses import Response

from codehub.core.errors import CodeHubError, WorkspaceNotFoundError


class TestNormalizePath:
    """_normalize_path function tests."""

    def test_known_endpoint_unchanged(self):
        """Known endpoints are returned unchanged."""
        from codehub.app.middleware.logging import _normalize_path

        assert _normalize_path("/api/v1/login") == "/api/v1/login"
        assert _normalize_path("/api/v1/workspaces") == "/api/v1/workspaces"
        assert _normalize_path("/api/v1/events") == "/api/v1/events"

    def test_workspace_id_normalized(self):
        """Workspace IDs are replaced with :id placeholder."""
        from codehub.app.middleware.logging import _normalize_path

        result = _normalize_path(
            "/api/v1/workspaces/550e8400-e29b-41d4-a716-446655440000"
        )
        assert result == "/api/v1/workspaces/:id"

    def test_vscode_proxy_normalized(self):
        """VS Code proxy paths are normalized to /w/*."""
        from codehub.app.middleware.logging import _normalize_path

        result = _normalize_path("/w/550e8400-e29b-41d4-a716/some/deep/path")
        assert result == "/w/*"

    def test_unknown_path_becomes_other(self):
        """Unknown paths become 'other' for cardinality control."""
        from codehub.app.middleware.logging import _normalize_path

        assert _normalize_path("/unknown/random/path") == "other"
        assert _normalize_path("/api/v2/something") == "other"


class TestLoggingMiddleware:
    """LoggingMiddleware tests."""

    @pytest.fixture
    def mock_request(self):
        """Create mock Request."""
        request = MagicMock(spec=Request)
        request.method = "GET"
        request.url = MagicMock()
        request.url.path = "/api/v1/workspaces"
        request.headers = {}
        return request

    @pytest.fixture
    def mock_response(self):
        """Create mock Response."""
        response = MagicMock(spec=Response)
        response.status_code = 200
        response.headers = {}
        return response

    @pytest.mark.asyncio
    async def test_sets_trace_id_header_in_response(self, mock_request, mock_response):
        """Middleware adds X-Trace-ID header to response."""
        from codehub.app.logging import clear_trace_context
        from codehub.app.middleware.logging import LoggingMiddleware

        async def call_next(request):
            return mock_response

        middleware = LoggingMiddleware(app=MagicMock())

        with patch("codehub.app.middleware.logging.HTTP_REQUESTS_TOTAL"), patch(
            "codehub.app.middleware.logging.HTTP_REQUEST_DURATION"
        ):
            response = await middleware.dispatch(mock_request, call_next)

        assert "X-Trace-ID" in response.headers
        clear_trace_context()

    @pytest.mark.asyncio
    async def test_uses_provided_trace_id(self, mock_request, mock_response):
        """Middleware uses X-Trace-ID from request header if provided."""
        from codehub.app.logging import clear_trace_context
        from codehub.app.middleware.logging import LoggingMiddleware

        mock_request.headers = {"x-trace-id": "custom-trace-123"}

        async def call_next(request):
            return mock_response

        middleware = LoggingMiddleware(app=MagicMock())

        with patch("codehub.app.middleware.logging.HTTP_REQUESTS_TOTAL"), patch(
            "codehub.app.middleware.logging.HTTP_REQUEST_DURATION"
        ):
            response = await middleware.dispatch(mock_request, call_next)

        assert response.headers["X-Trace-ID"] == "custom-trace-123"
        clear_trace_context()

    @pytest.mark.asyncio
    async def test_logs_request_on_exception(self, mock_request, caplog):
        """Middleware logs failed request with exception info."""
        from codehub.app.logging import clear_trace_context
        from codehub.app.middleware.logging import LoggingMiddleware

        async def call_next(request):
            raise ValueError("Test error")

        middleware = LoggingMiddleware(app=MagicMock())

        with pytest.raises(ValueError):
            with caplog.at_level(logging.ERROR):
                await middleware.dispatch(mock_request, call_next)

        assert "Request failed" in caplog.text
        clear_trace_context()


class TestCodeHubErrorHandler:
    """codehub_error_handler tests."""

    @pytest.fixture
    def mock_request(self):
        """Create mock Request."""
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/api/v1/workspaces/123"
        request.method = "GET"
        return request

    @pytest.mark.asyncio
    async def test_returns_correct_status_code(self, mock_request):
        """Handler returns the error's status code."""
        from codehub.app.main import codehub_error_handler

        exc = WorkspaceNotFoundError("Workspace ws-123 not found")
        response = await codehub_error_handler(mock_request, exc)

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_error_response_body(self, mock_request):
        """Handler returns error code and message in body."""
        from codehub.app.main import codehub_error_handler

        exc = WorkspaceNotFoundError("Workspace ws-123 not found")
        response = await codehub_error_handler(mock_request, exc)

        body = json.loads(response.body)
        assert "error" in body
        assert "code" in body["error"]
        assert "message" in body["error"]


class TestUnhandledExceptionHandler:
    """unhandled_exception_handler tests."""

    @pytest.fixture
    def mock_request(self):
        """Create mock Request."""
        request = MagicMock(spec=Request)
        request.url = MagicMock()
        request.url.path = "/api/v1/workspaces"
        request.method = "POST"
        return request

    @pytest.mark.asyncio
    async def test_returns_500_status(self, mock_request):
        """Handler returns 500 for unhandled exceptions."""
        from codehub.app.main import unhandled_exception_handler

        with patch("codehub.app.main.get_trace_id", return_value="trace-123"):
            exc = RuntimeError("Unexpected error")
            response = await unhandled_exception_handler(mock_request, exc)

        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_returns_generic_message(self, mock_request):
        """Handler returns generic message (no leak of internal details)."""
        from codehub.app.main import unhandled_exception_handler

        with patch("codehub.app.main.get_trace_id", return_value="trace-123"):
            exc = RuntimeError("Secret database credentials")
            response = await unhandled_exception_handler(mock_request, exc)

        body = json.loads(response.body)
        assert body["detail"] == "Internal server error"
        assert "Secret" not in response.body.decode()

    @pytest.mark.asyncio
    async def test_logs_exception(self, mock_request, caplog):
        """Handler logs the exception with stack trace."""
        from codehub.app.main import unhandled_exception_handler

        with patch("codehub.app.main.get_trace_id", return_value="trace-123"):
            with caplog.at_level(logging.ERROR):
                exc = RuntimeError("Test unhandled")
                await unhandled_exception_handler(mock_request, exc)

        assert "Unhandled exception" in caplog.text
