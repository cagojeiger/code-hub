"""Unit tests for AgentClient.

Tests the WorkspaceRuntime interface implementation.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from codehub.agent.client import AgentClient, AgentConfig
from codehub.core.interfaces.runtime import (
    ContainerStatus,
    GCResult,
    UpstreamInfo,
    VolumeStatus,
    WorkspaceState,
)


class TestAgentClient:
    """Tests for AgentClient HTTP client."""

    @pytest.fixture
    def config(self) -> AgentConfig:
        """Create test configuration."""
        return AgentConfig(
            endpoint="http://agent:8000",
            api_key="test-api-key",
            timeout=30.0,
            job_timeout=600.0,
        )

    @pytest.fixture
    def client(self, config: AgentConfig) -> AgentClient:
        """Create AgentClient instance."""
        return AgentClient(config)

    @pytest.fixture
    def mock_response(self) -> MagicMock:
        """Create mock HTTP response."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.json.return_value = {}
        response.raise_for_status = MagicMock()
        return response

    # =========================================================================
    # HTTP Client Tests
    # =========================================================================

    async def test_get_headers_with_api_key(self, client: AgentClient) -> None:
        """Test headers include API key when configured."""
        headers = client._get_headers()

        assert headers["Authorization"] == "Bearer test-api-key"
        assert headers["Content-Type"] == "application/json"

    async def test_get_headers_without_api_key(self) -> None:
        """Test headers without API key."""
        config = AgentConfig(endpoint="http://agent:8000", api_key="")
        client = AgentClient(config)

        headers = client._get_headers()

        assert "Authorization" not in headers

    async def test_close(self, client: AgentClient) -> None:
        """Test close cleans up HTTP client."""
        await client._get_client()
        assert client._client is not None

        await client.close()
        assert client._client is None

    async def test_request_on_404_raise(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test _request raises on 404 with on_404='raise'."""
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not found", request=MagicMock(), response=mock_response
        )

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_client", return_value=mock_http_client):
            with pytest.raises(httpx.HTTPStatusError):
                await client._request("get", "/test", on_404="raise")

    async def test_request_on_404_none(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test _request returns None on 404 with on_404='none'."""
        mock_response.status_code = 404

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_client", return_value=mock_http_client):
            result = await client._request("get", "/test", on_404="none")

        assert result is None

    # =========================================================================
    # WorkspaceRuntime.observe() Tests
    # =========================================================================

    async def test_observe_returns_workspace_states(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test observe returns list of WorkspaceState."""
        mock_response.json.return_value = {
            "workspaces": [
                {
                    "workspace_id": "ws1",
                    "container": {"running": True, "healthy": True},
                    "volume": {"exists": True},
                    "archive": None,
                },
                {
                    "workspace_id": "ws2",
                    "container": None,
                    "volume": {"exists": True},
                    "archive": {"exists": True, "archive_key": "ws2/op1/home.tar.zst"},
                },
            ]
        }

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.observe()

        assert len(result) == 2
        assert result[0].workspace_id == "ws1"
        assert result[0].container == ContainerStatus(running=True, healthy=True)
        assert result[0].volume == VolumeStatus(exists=True)
        assert result[0].archive is None
        assert result[1].workspace_id == "ws2"
        assert result[1].archive.archive_key == "ws2/op1/home.tar.zst"

    async def test_observe_empty_on_none(self, client: AgentClient) -> None:
        """Test observe returns empty list when response is None."""
        with patch.object(client, "_request", return_value=None):
            result = await client.observe()

        assert result == []

    # =========================================================================
    # WorkspaceRuntime Lifecycle Tests
    # =========================================================================

    async def test_provision(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test provision sends correct request."""
        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.provision("ws1")

        mock_req.assert_called_once_with("post", "/api/v1/workspaces/ws1/provision")

    async def test_start(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test start sends correct request."""
        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.start("ws1", "image:latest")

        mock_req.assert_called_once_with(
            "post",
            "/api/v1/workspaces/ws1/start",
            json={"image": "image:latest"},
        )

    async def test_stop(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test stop sends correct request."""
        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.stop("ws1")

        mock_req.assert_called_once_with("post", "/api/v1/workspaces/ws1/stop")

    async def test_delete(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test delete sends correct request."""
        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.delete("ws1")

        mock_req.assert_called_once_with("delete", "/api/v1/workspaces/ws1")

    # =========================================================================
    # WorkspaceRuntime Persistence Tests
    # =========================================================================

    async def test_archive(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test archive returns archive key."""
        mock_response.json.return_value = {"archive_key": "ws1/op123/home.tar.zst"}

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            result = await client.archive("ws1", "op123")

        assert result == "ws1/op123/home.tar.zst"
        mock_req.assert_called_once_with(
            "post",
            "/api/v1/workspaces/ws1/archive",
            json={"op_id": "op123"},
            timeout=600.0,
        )

    async def test_restore(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test restore sends correct request."""
        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.restore("ws1", "ws1/op123/home.tar.zst")

        mock_req.assert_called_once_with(
            "post",
            "/api/v1/workspaces/ws1/restore",
            json={"archive_key": "ws1/op123/home.tar.zst"},
            timeout=600.0,
        )

    async def test_delete_archive(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test delete_archive returns success."""
        mock_response.json.return_value = {"deleted": True}

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.delete_archive("ws1/op123/home.tar.zst")

        assert result is True

    async def test_delete_archive_not_found(self, client: AgentClient) -> None:
        """Test delete_archive returns False when not found."""
        with patch.object(client, "_request", return_value=None):
            result = await client.delete_archive("ws1/op123/home.tar.zst")

        assert result is False

    # =========================================================================
    # WorkspaceRuntime Routing Tests
    # =========================================================================

    async def test_get_upstream(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test get_upstream returns UpstreamInfo."""
        mock_response.json.return_value = {"hostname": "codehub-ws1", "port": 8080}

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.get_upstream("ws1")

        assert result is not None
        assert result.hostname == "codehub-ws1"
        assert result.port == 8080

    async def test_get_upstream_none(self, client: AgentClient) -> None:
        """Test get_upstream returns None when not found."""
        with patch.object(client, "_request", return_value=None):
            result = await client.get_upstream("ws1")

        assert result is None

    # =========================================================================
    # WorkspaceRuntime GC Tests
    # =========================================================================

    async def test_run_gc(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test run_gc returns GCResult."""
        mock_response.json.return_value = {
            "deleted_count": 2,
            "deleted_keys": ["ws1/op1/home.tar.zst", "ws2/op2/home.tar.zst"],
        }

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            result = await client.run_gc([("ws3", "op3"), ("ws4", "op4")])

        assert result.deleted_count == 2
        assert len(result.deleted_keys) == 2
        mock_req.assert_called_once_with(
            "post",
            "/api/v1/workspaces/gc",
            json={
                "protected": [
                    {"workspace_id": "ws3", "op_id": "op3"},
                    {"workspace_id": "ws4", "op_id": "op4"},
                ]
            },
        )

    async def test_run_gc_empty_on_none(self, client: AgentClient) -> None:
        """Test run_gc returns empty result when response is None."""
        with patch.object(client, "_request", return_value=None):
            result = await client.run_gc([])

        assert result.deleted_count == 0
        assert result.deleted_keys == []

    # =========================================================================
    # Health Check Tests
    # =========================================================================

    async def test_health_check_success(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test health_check returns True on success."""
        mock_response.status_code = 200

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.health_check()

        assert result is True

    async def test_health_check_failure(self, client: AgentClient) -> None:
        """Test health_check returns False on failure."""
        with patch.object(client, "_request", side_effect=Exception("Connection failed")):
            result = await client.health_check()

        assert result is False
