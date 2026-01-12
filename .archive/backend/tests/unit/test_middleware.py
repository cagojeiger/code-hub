"""Tests for middleware."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.middleware import REQUEST_ID_HEADER, RequestIdMiddleware


@pytest.fixture
def app_with_middleware():
    """Create a test app with RequestIdMiddleware."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    return app


@pytest.fixture
def client(app_with_middleware):
    """Create a test client."""
    return TestClient(app_with_middleware)


class TestRequestIdMiddleware:
    """Tests for RequestIdMiddleware."""

    def test_generates_request_id_when_not_provided(self, client):
        """Test that a request ID is generated when not provided."""
        response = client.get("/test")

        assert response.status_code == 200
        assert REQUEST_ID_HEADER in response.headers
        assert len(response.headers[REQUEST_ID_HEADER]) == 36  # UUID length

    def test_uses_provided_request_id(self, client):
        """Test that the provided request ID is used."""
        custom_request_id = "custom-request-id-123"
        response = client.get("/test", headers={REQUEST_ID_HEADER: custom_request_id})

        assert response.status_code == 200
        assert response.headers[REQUEST_ID_HEADER] == custom_request_id

    def test_request_id_in_response_header(self, client):
        """Test that request ID is included in response header."""
        response = client.get("/test")

        assert REQUEST_ID_HEADER in response.headers

    def test_multiple_requests_have_different_ids(self, client):
        """Test that different requests have different IDs."""
        response1 = client.get("/test")
        response2 = client.get("/test")

        request_id_1 = response1.headers[REQUEST_ID_HEADER]
        request_id_2 = response2.headers[REQUEST_ID_HEADER]

        assert request_id_1 != request_id_2
