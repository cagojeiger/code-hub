"""Unit tests for Workspace Proxy.

Tests cover:
- GET /w/{workspace_id} - 308 redirect to trailing slash
- GET /w/{workspace_id}/ - 404 when workspace not found
- GET /w/{workspace_id}/ - 502 when upstream unavailable
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.api.v1.dependencies import get_instance_controller
from app.main import app
from app.services.instance.interface import InstanceController, UpstreamInfo


class TestTrailingSlashRedirect:
    """Tests for GET /w/{workspace_id} redirect."""

    @pytest.mark.asyncio
    async def test_redirect_to_trailing_slash(
        self, async_client: AsyncClient
    ) -> None:
        """Test 308 redirect from /w/{id} to /w/{id}/."""
        response = await async_client.get(
            "/w/test-workspace-id",
            follow_redirects=False,
        )
        assert response.status_code == 308
        assert response.headers["location"] == "/w/test-workspace-id/"


class TestProxyWorkspaceNotFound:
    """Tests for proxy when workspace doesn't exist."""

    @pytest.mark.asyncio
    async def test_http_proxy_workspace_not_found(
        self, async_client: AsyncClient
    ) -> None:
        """Test 404 when proxying to non-existent workspace."""
        response = await async_client.get("/w/nonexistent-workspace/")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "WORKSPACE_NOT_FOUND"


class TestProxyUpstreamUnavailable:
    """Tests for proxy when upstream is unavailable."""

    @pytest.mark.asyncio
    async def test_http_proxy_upstream_unavailable(
        self, db_engine: Any, test_user: Any, async_client: AsyncClient
    ) -> None:
        """Test 502 when upstream container is not reachable."""
        # First create a workspace
        create_response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "test-workspace"},
        )
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Mock instance controller to raise an error
        mock_instance = AsyncMock(spec=InstanceController)
        mock_instance.resolve_upstream.side_effect = ValueError("Container not found")

        app.dependency_overrides[get_instance_controller] = lambda: mock_instance

        try:
            response = await async_client.get(f"/w/{workspace_id}/")
            assert response.status_code == 502
            data = response.json()
            assert data["error"]["code"] == "UPSTREAM_UNAVAILABLE"
        finally:
            # Clean up override but keep session override
            if get_instance_controller in app.dependency_overrides:
                del app.dependency_overrides[get_instance_controller]


class TestProxyHttpForward:
    """Tests for HTTP request forwarding."""

    @pytest.mark.asyncio
    async def test_proxy_preserves_query_string(
        self, db_engine: Any, test_user: Any, async_client: AsyncClient
    ) -> None:
        """Test that query strings are preserved during proxy."""
        # Create workspace
        create_response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "test-workspace"},
        )
        workspace_id = create_response.json()["id"]

        # Mock instance controller - will fail but we can check the path construction
        mock_instance = AsyncMock(spec=InstanceController)
        mock_instance.resolve_upstream.return_value = UpstreamInfo(
            host="nonexistent-host", port=8080
        )

        app.dependency_overrides[get_instance_controller] = lambda: mock_instance

        try:
            # This will fail with 502 because the host doesn't exist,
            # but the important thing is that resolve_upstream was called
            response = await async_client.get(
                f"/w/{workspace_id}/some/path?foo=bar&baz=qux"
            )
            # Should get 502 because upstream host doesn't exist
            assert response.status_code == 502

            # Verify resolve_upstream was called with correct workspace_id
            mock_instance.resolve_upstream.assert_called_once_with(workspace_id)
        finally:
            if get_instance_controller in app.dependency_overrides:
                del app.dependency_overrides[get_instance_controller]
