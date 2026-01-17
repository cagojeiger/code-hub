"""Unit tests for VolumeManager."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub_agent.runtimes.docker.naming import ResourceNaming
from codehub_agent.runtimes.docker.volume import VolumeManager, VolumeStatus


class TestVolumeManager:
    """Tests for VolumeManager."""

    @pytest.fixture
    def manager(
        self,
        mock_volume_api: AsyncMock,
        mock_agent_config: MagicMock,
        mock_naming: ResourceNaming,
    ) -> VolumeManager:
        """Create VolumeManager with mock dependencies."""
        return VolumeManager(
            config=mock_agent_config,
            naming=mock_naming,
            api=mock_volume_api,
        )

    async def test_list_all_empty(
        self,
        manager: VolumeManager,
        mock_volume_api: AsyncMock,
    ) -> None:
        """Test list_all returns empty list when no volumes."""
        mock_volume_api.list.return_value = []

        result = await manager.list_all()

        assert result == []
        mock_volume_api.list.assert_called_once()

    async def test_list_all_filters_by_prefix_and_suffix(
        self,
        manager: VolumeManager,
        mock_volume_api: AsyncMock,
    ) -> None:
        """Test list_all filters volumes by prefix and -home suffix."""
        mock_volume_api.list.return_value = [
            {"Name": "codehub-ws1-home"},
            {"Name": "codehub-ws2-home"},
            {"Name": "codehub-ws3-data"},  # Wrong suffix
            {"Name": "other-volume-home"},  # Wrong prefix
        ]

        result = await manager.list_all()

        assert len(result) == 2
        assert result[0]["workspace_id"] == "ws1"
        assert result[0]["exists"] is True
        assert result[1]["workspace_id"] == "ws2"

    async def test_create_volume(
        self,
        manager: VolumeManager,
        mock_volume_api: AsyncMock,
    ) -> None:
        """Test create creates volume with correct name."""
        # Volume doesn't exist
        mock_volume_api.inspect.return_value = None

        result = await manager.create("ws1")

        mock_volume_api.create.assert_called_once()
        call_args = mock_volume_api.create.call_args
        config = call_args[0][0]
        assert config.name == "codehub-ws1-home"
        assert result.status.value == "completed"

    async def test_create_volume_already_exists(
        self,
        manager: VolumeManager,
        mock_volume_api: AsyncMock,
    ) -> None:
        """Test create returns already_exists when volume exists."""
        mock_volume_api.inspect.return_value = {"Name": "codehub-ws1-home"}

        result = await manager.create("ws1")

        mock_volume_api.create.assert_not_called()
        assert result.status.value == "already_exists"

    async def test_delete_volume(
        self,
        manager: VolumeManager,
        mock_volume_api: AsyncMock,
    ) -> None:
        """Test delete removes volume with correct name."""
        # Volume exists
        mock_volume_api.inspect.return_value = {"Name": "codehub-ws1-home"}

        result = await manager.delete("ws1")

        mock_volume_api.remove.assert_called_once_with("codehub-ws1-home")
        assert result.status.value == "completed"

    async def test_delete_volume_not_found(
        self,
        manager: VolumeManager,
        mock_volume_api: AsyncMock,
    ) -> None:
        """Test delete returns already_deleted when volume not found."""
        mock_volume_api.inspect.return_value = None

        result = await manager.delete("ws1")

        mock_volume_api.remove.assert_not_called()
        assert result.status.value == "already_deleted"

    async def test_exists_true(
        self,
        manager: VolumeManager,
        mock_volume_api: AsyncMock,
    ) -> None:
        """Test exists returns True when volume found."""
        mock_volume_api.inspect.return_value = {"Name": "codehub-ws1-home"}

        result = await manager.exists("ws1")

        assert result.exists is True
        assert result.name == "codehub-ws1-home"
        mock_volume_api.inspect.assert_called_once_with("codehub-ws1-home")

    async def test_exists_false(
        self,
        manager: VolumeManager,
        mock_volume_api: AsyncMock,
    ) -> None:
        """Test exists returns False when volume not found."""
        mock_volume_api.inspect.return_value = None

        result = await manager.exists("ws1")

        assert result.exists is False
        assert result.name == "codehub-ws1-home"
