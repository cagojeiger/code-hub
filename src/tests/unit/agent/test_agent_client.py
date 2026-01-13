"""Unit tests for AgentClient."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from codehub.agent.client import AgentClient, AgentConfig


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

    async def test_list_all_instances(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test list_all returns parsed instances."""
        mock_response.json.return_value = {
            "instances": [
                {
                    "workspace_id": "ws1",
                    "running": True,
                    "reason": "Running",
                    "message": "Up 5 minutes",
                }
            ]
        }

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            result = await client.list_all("codehub-")

        assert len(result) == 1
        assert result[0].workspace_id == "ws1"
        assert result[0].running is True
        mock_req.assert_called_once_with("get", "/api/v1/instances")

    async def test_list_all_empty_on_none(self, client: AgentClient) -> None:
        """Test list_all returns empty list when response is None."""
        with patch.object(client, "_request", return_value=None):
            result = await client.list_all("codehub-")

        assert result == []

    async def test_start_instance(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test start sends correct request."""
        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.start("ws1", "image:latest")

        mock_req.assert_called_once_with(
            "post",
            "/api/v1/instances/ws1/start",
            json={"image_ref": "image:latest"},
        )

    async def test_delete_instance(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test delete sends correct request."""
        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.delete("ws1")

        mock_req.assert_called_once_with("delete", "/api/v1/instances/ws1")

    async def test_is_running_true(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test is_running returns True when running and healthy."""
        mock_response.json.return_value = {"running": True, "healthy": True}

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.is_running("ws1")

        assert result is True

    async def test_is_running_false_not_healthy(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test is_running returns False when not healthy."""
        mock_response.json.return_value = {"running": True, "healthy": False}

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.is_running("ws1")

        assert result is False

    async def test_is_running_false_on_none(self, client: AgentClient) -> None:
        """Test is_running returns False when response is None."""
        with patch.object(client, "_request", return_value=None):
            result = await client.is_running("ws1")

        assert result is False

    async def test_resolve_upstream(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test resolve_upstream returns UpstreamInfo."""
        mock_response.json.return_value = {"hostname": "codehub-ws1", "port": 8080}

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.resolve_upstream("ws1")

        assert result is not None
        assert result.hostname == "codehub-ws1"
        assert result.port == 8080

    async def test_resolve_upstream_none(self, client: AgentClient) -> None:
        """Test resolve_upstream returns None when not found."""
        with patch.object(client, "_request", return_value=None):
            result = await client.resolve_upstream("ws1")

        assert result is None

    async def test_volume_create(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test volume create extracts workspace_id correctly."""
        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.create("codehub-ws1-home")

        mock_req.assert_called_once_with("post", "/api/v1/volumes/ws1")

    async def test_volume_remove(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test volume remove sends correct request."""
        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            await client.remove("codehub-ws1-home")

        mock_req.assert_called_once_with("delete", "/api/v1/volumes/ws1")

    async def test_volume_exists_true(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test volume exists returns True."""
        mock_response.json.return_value = {"exists": True}

        with patch.object(client, "_request", return_value=mock_response):
            result = await client.exists("codehub-ws1-home")

        assert result is True

    async def test_volume_exists_false(self, client: AgentClient) -> None:
        """Test volume exists returns False when not found."""
        with patch.object(client, "_request", return_value=None):
            result = await client.exists("codehub-ws1-home")

        assert result is False

    async def test_run_archive(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test run_archive sends correct request."""
        mock_response.json.return_value = {"exit_code": 0, "logs": "Done"}

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            result = await client.run_archive(
                "s3://bucket/cluster/ws1/op123/home.tar.zst",
                "codehub-ws1-home",
                "http://minio:9000",
                "access",
                "secret",
            )

        assert result.exit_code == 0
        assert result.logs == "Done"
        mock_req.assert_called_once()

    async def test_run_restore(
        self, client: AgentClient, mock_response: MagicMock
    ) -> None:
        """Test run_restore sends correct request."""
        mock_response.json.return_value = {"exit_code": 0, "logs": "Restored"}

        with patch.object(client, "_request", return_value=mock_response) as mock_req:
            result = await client.run_restore(
                "s3://bucket/cluster/ws1/op123/home.tar.zst",
                "codehub-ws1-home",
                "http://minio:9000",
                "access",
                "secret",
            )

        assert result.exit_code == 0

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

    async def test_parse_archive_url(self) -> None:
        """Test archive URL parsing."""
        workspace_id, op_id = AgentClient._parse_archive_url(
            "s3://bucket/cluster/ws1/op123/home.tar.zst"
        )

        assert workspace_id == "ws1"
        assert op_id == "op123"

    async def test_parse_archive_url_invalid(self) -> None:
        """Test archive URL parsing with invalid format."""
        with pytest.raises(ValueError, match="Invalid archive_url format"):
            AgentClient._parse_archive_url("invalid-url")

    async def test_extract_workspace_id(self) -> None:
        """Test workspace_id extraction from volume name."""
        assert AgentClient._extract_workspace_id("codehub-ws1-home") == "ws1"
        assert AgentClient._extract_workspace_id("prefix-ws123-home") == "ws123"
        assert AgentClient._extract_workspace_id("ws1-home") == "ws1"

    async def test_close(self, client: AgentClient) -> None:
        """Test close cleans up HTTP client."""
        # Create internal client
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
