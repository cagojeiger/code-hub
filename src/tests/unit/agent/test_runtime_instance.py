"""Unit tests for InstanceManager."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from codehub_agent.runtimes.docker.instance import InstanceManager, InstanceStatus
from codehub_agent.runtimes.docker.naming import ResourceNaming


class TestInstanceManager:
    """Tests for InstanceManager."""

    @pytest.fixture
    def manager(
        self,
        mock_container_api: AsyncMock,
        mock_image_api: AsyncMock,
        mock_agent_config: MagicMock,
        mock_naming: ResourceNaming,
    ) -> InstanceManager:
        """Create InstanceManager with mock dependencies."""
        return InstanceManager(
            config=mock_agent_config,
            naming=mock_naming,
            containers=mock_container_api,
            images=mock_image_api,
        )

    async def test_list_all_empty(
        self,
        manager: InstanceManager,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test list_all returns empty list when no containers."""
        mock_container_api.list.return_value = []

        result = await manager.list_all()

        assert result == []
        mock_container_api.list.assert_called_once()

    async def test_list_all_filters_by_prefix(
        self,
        manager: InstanceManager,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test list_all filters containers by prefix."""
        mock_container_api.list.return_value = [
            {"Names": ["/codehub-ws1"], "State": "running", "Status": "Up 5 minutes"},
            {"Names": ["/codehub-ws2"], "State": "exited", "Status": "Exited"},
            {"Names": ["/other-container"], "State": "running", "Status": "Up"},
        ]

        result = await manager.list_all()

        assert len(result) == 2
        assert result[0]["workspace_id"] == "ws1"
        assert result[0]["running"] is True
        assert result[1]["workspace_id"] == "ws2"
        assert result[1]["running"] is False

    async def test_list_all_excludes_job_containers(
        self,
        manager: InstanceManager,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test list_all excludes job containers."""
        mock_container_api.list.return_value = [
            {"Names": ["/codehub-ws1"], "State": "running", "Status": "Up"},
            {"Names": ["/codehub-job-archive-abc123"], "State": "running", "Status": "Up"},
        ]

        result = await manager.list_all()

        assert len(result) == 1
        assert result[0]["workspace_id"] == "ws1"

    async def test_start_existing_container(
        self,
        manager: InstanceManager,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test start starts existing container."""
        mock_container_api.inspect.return_value = {"Id": "abc123"}

        await manager.start("ws1")

        mock_container_api.start.assert_called_once_with("codehub-ws1")
        mock_container_api.create.assert_not_called()

    async def test_start_creates_new_container(
        self,
        manager: InstanceManager,
        mock_container_api: AsyncMock,
        mock_image_api: AsyncMock,
    ) -> None:
        """Test start creates container when not exists."""
        mock_container_api.inspect.return_value = None

        await manager.start("ws1", "custom-image:latest")

        mock_image_api.ensure.assert_called_once_with("custom-image:latest")
        mock_container_api.create.assert_called_once()
        assert mock_container_api.start.call_count == 1

    async def test_start_recreates_on_404(
        self,
        manager: InstanceManager,
        mock_container_api: AsyncMock,
        mock_image_api: AsyncMock,
    ) -> None:
        """Test start recreates container on 404 error."""
        mock_container_api.inspect.return_value = {"Id": "abc123"}

        # First start fails with 404
        response = MagicMock()
        response.status_code = 404
        mock_container_api.start.side_effect = [
            httpx.HTTPStatusError("Not found", request=MagicMock(), response=response),
            None,  # Second call succeeds
        ]

        await manager.start("ws1")

        mock_container_api.remove.assert_called_once_with("codehub-ws1")
        mock_image_api.ensure.assert_called_once()
        mock_container_api.create.assert_called_once()

    async def test_delete_stops_and_removes(
        self,
        manager: InstanceManager,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test delete stops and removes container."""
        await manager.delete("ws1")

        mock_container_api.stop.assert_called_once_with("codehub-ws1")
        mock_container_api.remove.assert_called_once_with("codehub-ws1")

    async def test_get_status_not_found(
        self,
        manager: InstanceManager,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test get_status returns NotFound status."""
        mock_container_api.inspect.return_value = None

        result = await manager.get_status("ws1")

        assert result.exists is False
        assert result.running is False
        assert result.healthy is False
        assert result.reason == "NotFound"

    async def test_get_status_running_healthy(
        self,
        manager: InstanceManager,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test get_status returns running healthy status."""
        mock_container_api.inspect.return_value = {
            "State": {
                "Running": True,
                "Health": {"Status": "healthy"},
            }
        }

        result = await manager.get_status("ws1")

        assert result.exists is True
        assert result.running is True
        assert result.healthy is True
        assert result.reason == "Running"

    async def test_get_status_running_unhealthy(
        self,
        manager: InstanceManager,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test get_status returns running unhealthy status."""
        mock_container_api.inspect.return_value = {
            "State": {
                "Running": True,
                "Health": {"Status": "unhealthy"},
            }
        }

        result = await manager.get_status("ws1")

        assert result.exists is True
        assert result.running is True
        assert result.healthy is False

    async def test_get_status_stopped(
        self,
        manager: InstanceManager,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test get_status returns stopped status."""
        mock_container_api.inspect.return_value = {
            "State": {
                "Running": False,
            }
        }

        result = await manager.get_status("ws1")

        assert result.exists is True
        assert result.running is False
        assert result.healthy is False
        assert result.reason == "Stopped"

    async def test_get_upstream(
        self,
        manager: InstanceManager,
    ) -> None:
        """Test get_upstream returns correct info."""
        result = await manager.get_upstream("ws1")

        assert result.hostname == "codehub-ws1"
        assert result.port == 8080
        assert result.url == "http://codehub-ws1:8080"
