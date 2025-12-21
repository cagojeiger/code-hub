"""Tests for the errors module."""

import pytest
from fastapi.testclient import TestClient

from app.core.errors import (
    CodeHubError,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    ForbiddenError,
    InternalError,
    InvalidRequestError,
    InvalidStateError,
    UnauthorizedError,
    UpstreamUnavailableError,
    WorkspaceNotFoundError,
)


class TestErrorCode:
    """Tests for ErrorCode enum."""

    def test_error_codes_are_strings(self):
        """Error codes should be string values."""
        assert ErrorCode.INVALID_REQUEST == "INVALID_REQUEST"
        assert ErrorCode.UNAUTHORIZED == "UNAUTHORIZED"
        assert ErrorCode.FORBIDDEN == "FORBIDDEN"
        assert ErrorCode.WORKSPACE_NOT_FOUND == "WORKSPACE_NOT_FOUND"
        assert ErrorCode.INVALID_STATE == "INVALID_STATE"
        assert ErrorCode.TOO_MANY_REQUESTS == "TOO_MANY_REQUESTS"
        assert ErrorCode.UPSTREAM_UNAVAILABLE == "UPSTREAM_UNAVAILABLE"
        assert ErrorCode.INTERNAL_ERROR == "INTERNAL_ERROR"

    def test_all_spec_error_codes_defined(self):
        """All error codes from spec.md should be defined."""
        expected_codes = {
            "INVALID_REQUEST",
            "UNAUTHORIZED",
            "FORBIDDEN",
            "WORKSPACE_NOT_FOUND",
            "INVALID_STATE",
            "TOO_MANY_REQUESTS",
            "UPSTREAM_UNAVAILABLE",
            "INTERNAL_ERROR",
        }
        actual_codes = {code.value for code in ErrorCode}
        assert actual_codes == expected_codes


class TestErrorResponse:
    """Tests for ErrorResponse model."""

    def test_error_response_format(self):
        """ErrorResponse should match spec format."""
        response = ErrorResponse(
            error=ErrorDetail(code="WORKSPACE_NOT_FOUND", message="Workspace not found")
        )
        dumped = response.model_dump()
        assert dumped == {
            "error": {"code": "WORKSPACE_NOT_FOUND", "message": "Workspace not found"}
        }

    def test_error_response_json(self):
        """ErrorResponse JSON should match spec format."""
        response = ErrorResponse(
            error=ErrorDetail(code="INVALID_STATE", message="Cannot start")
        )
        json_str = response.model_dump_json()
        assert '"code":"INVALID_STATE"' in json_str
        assert '"message":"Cannot start"' in json_str


class TestCodeHubError:
    """Tests for CodeHubError base class."""

    def test_base_error_attributes(self):
        """CodeHubError should have code, message, and status_code."""
        error = CodeHubError(ErrorCode.INVALID_REQUEST, "Test message", 400)
        assert error.code == ErrorCode.INVALID_REQUEST
        assert error.message == "Test message"
        assert error.status_code == 400
        assert str(error) == "Test message"

    def test_to_response(self):
        """to_response should return ErrorResponse."""
        error = CodeHubError(ErrorCode.FORBIDDEN, "Access denied", 403)
        response = error.to_response()
        assert isinstance(response, ErrorResponse)
        assert response.error.code == "FORBIDDEN"
        assert response.error.message == "Access denied"


class TestSpecificErrors:
    """Tests for specific error classes."""

    @pytest.mark.parametrize(
        "error_class,expected_code,expected_status,default_message",
        [
            (InvalidRequestError, ErrorCode.INVALID_REQUEST, 400, "Invalid request"),
            (UnauthorizedError, ErrorCode.UNAUTHORIZED, 401, "Authentication required"),
            (ForbiddenError, ErrorCode.FORBIDDEN, 403, "Permission denied"),
            (
                WorkspaceNotFoundError,
                ErrorCode.WORKSPACE_NOT_FOUND,
                404,
                "Workspace not found",
            ),
            (
                InvalidStateError,
                ErrorCode.INVALID_STATE,
                409,
                "Invalid state for this operation",
            ),
            (
                UpstreamUnavailableError,
                ErrorCode.UPSTREAM_UNAVAILABLE,
                502,
                "Upstream service unavailable",
            ),
            (
                InternalError,
                ErrorCode.INTERNAL_ERROR,
                500,
                "Internal server error",
            ),
        ],
    )
    def test_error_defaults(
        self, error_class, expected_code, expected_status, default_message
    ):
        """Each error class should have correct defaults."""
        error = error_class()
        assert error.code == expected_code
        assert error.status_code == expected_status
        assert error.message == default_message

    @pytest.mark.parametrize(
        "error_class",
        [
            InvalidRequestError,
            UnauthorizedError,
            ForbiddenError,
            WorkspaceNotFoundError,
            InvalidStateError,
            UpstreamUnavailableError,
            InternalError,
        ],
    )
    def test_custom_message(self, error_class):
        """Each error class should accept custom message."""
        custom_msg = "Custom error message"
        error = error_class(custom_msg)
        assert error.message == custom_msg

    def test_invalid_state_error_use_case(self):
        """InvalidStateError for state transition failure."""
        error = InvalidStateError("Cannot start workspace in RUNNING state")
        assert error.status_code == 409
        assert error.code == ErrorCode.INVALID_STATE
        assert "RUNNING" in error.message

    def test_workspace_not_found_error_use_case(self):
        """WorkspaceNotFoundError for missing workspace."""
        workspace_id = "01HXYZ123"
        error = WorkspaceNotFoundError(f"Workspace {workspace_id} not found")
        assert error.status_code == 404
        assert workspace_id in error.message


class TestExceptionHandler:
    """Tests for FastAPI exception handler integration."""

    @pytest.fixture
    def test_app(self):
        """Create test FastAPI app with exception handler."""
        from app.main import app

        return app

    @pytest.fixture
    def client(self, test_app):
        """Create test client."""
        return TestClient(test_app)

    def test_exception_handler_returns_json(self, test_app, client):
        """Exception handler should return JSON response."""

        # Add a test endpoint that raises an error
        @test_app.get("/test-error")
        async def test_error():
            raise WorkspaceNotFoundError("Test workspace not found")

        response = client.get("/test-error")
        assert response.status_code == 404
        assert response.headers["content-type"] == "application/json"

        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "WORKSPACE_NOT_FOUND"
        assert data["error"]["message"] == "Test workspace not found"

    def test_exception_handler_invalid_state(self, test_app, client):
        """Exception handler should handle InvalidStateError."""

        @test_app.get("/test-invalid-state")
        async def test_invalid_state():
            raise InvalidStateError("Cannot stop workspace in CREATED state")

        response = client.get("/test-invalid-state")
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "INVALID_STATE"

    def test_generic_exception_handler(self, monkeypatch):
        """Generic handler returns INTERNAL_ERROR for unexpected exceptions."""
        # Set required env var for config
        monkeypatch.setenv("CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR", "/tmp/test")

        # Clear cached settings to use new env var
        from app.core.config import get_settings

        get_settings.cache_clear()

        # Import app after setting env var
        from app.main import app as test_app

        @test_app.get("/test-unexpected-error")
        async def test_unexpected_error():
            raise ValueError("This is an unexpected error")

        # Use raise_server_exceptions=False to test exception handler behavior
        with TestClient(test_app, raise_server_exceptions=False) as client:
            response = client.get("/test-unexpected-error")
            assert response.status_code == 500
            assert response.headers["content-type"] == "application/json"

            data = response.json()
            assert "error" in data
            assert data["error"]["code"] == "INTERNAL_ERROR"
            assert data["error"]["message"] == "Internal server error"
