"""Tests for BulkObserver - 리소스 관측 로직.

Reference: docs/architecture_v2/wc-observer.md
"""

import pytest
from unittest.mock import AsyncMock

from codehub.control.coordinator.observer import BulkObserver
from codehub.core.interfaces.instance import ContainerInfo, InstanceController
from codehub.core.interfaces.storage import ArchiveInfo, StorageProvider, VolumeInfo


@pytest.fixture
def mock_ic() -> AsyncMock:
    """Mock InstanceController."""
    ic = AsyncMock(spec=InstanceController)
    ic.list_all = AsyncMock(return_value=[])
    return ic


@pytest.fixture
def mock_sp() -> AsyncMock:
    """Mock StorageProvider."""
    sp = AsyncMock(spec=StorageProvider)
    sp.list_volumes = AsyncMock(return_value=[])
    sp.list_archives = AsyncMock(return_value=[])
    return sp


class TestBulkObserverObserveAll:
    """BulkObserver.observe_all() 테스트."""

    async def test_obs_001_empty_resources_returns_empty(
        self, mock_ic: AsyncMock, mock_sp: AsyncMock
    ):
        """OBS-001: 빈 리소스 → 빈 dict."""
        observer = BulkObserver(mock_ic, mock_sp)

        result = await observer.observe_all()

        assert result == {}

    async def test_obs_002_container_only(
        self, mock_ic: AsyncMock, mock_sp: AsyncMock
    ):
        """OBS-002: container만 존재 → volume/archive는 None."""
        mock_ic.list_all.return_value = [
            ContainerInfo(
                workspace_id="ws-1",
                running=True,
                reason="Running",
                message="Up 5 minutes",
            )
        ]
        observer = BulkObserver(mock_ic, mock_sp)

        result = await observer.observe_all()

        assert "ws-1" in result
        assert result["ws-1"]["container"]["running"] is True
        assert result["ws-1"]["container"]["reason"] == "Running"
        assert result["ws-1"]["volume"] is None
        assert result["ws-1"]["archive"] is None

    async def test_obs_003_volume_only(
        self, mock_ic: AsyncMock, mock_sp: AsyncMock
    ):
        """OBS-003: volume만 존재."""
        mock_sp.list_volumes.return_value = [
            VolumeInfo(
                workspace_id="ws-1",
                exists=True,
                reason="VolumeExists",
                message="Volume ws-1-home exists",
            )
        ]
        observer = BulkObserver(mock_ic, mock_sp)

        result = await observer.observe_all()

        assert "ws-1" in result
        assert result["ws-1"]["container"] is None
        assert result["ws-1"]["volume"]["exists"] is True
        assert result["ws-1"]["archive"] is None

    async def test_obs_004_archive_only(
        self, mock_ic: AsyncMock, mock_sp: AsyncMock
    ):
        """OBS-004: archive만 존재."""
        mock_sp.list_archives.return_value = [
            ArchiveInfo(
                workspace_id="ws-1",
                archive_key="ws-1/op-123/home.tar.zst",
                exists=True,
                reason="ArchiveUploaded",
                message="Archive uploaded",
            )
        ]
        observer = BulkObserver(mock_ic, mock_sp)

        result = await observer.observe_all()

        assert "ws-1" in result
        assert result["ws-1"]["container"] is None
        assert result["ws-1"]["volume"] is None
        assert result["ws-1"]["archive"]["exists"] is True
        assert result["ws-1"]["archive"]["archive_key"] == "ws-1/op-123/home.tar.zst"

    async def test_obs_005_full_resources(
        self, mock_ic: AsyncMock, mock_sp: AsyncMock
    ):
        """OBS-005: 모든 리소스 존재."""
        mock_ic.list_all.return_value = [
            ContainerInfo(workspace_id="ws-1", running=True, reason="Running", message="")
        ]
        mock_sp.list_volumes.return_value = [
            VolumeInfo(workspace_id="ws-1", exists=True, reason="VolumeExists", message="")
        ]
        mock_sp.list_archives.return_value = [
            ArchiveInfo(
                workspace_id="ws-1",
                archive_key="ws-1/op/home.tar.zst",
                exists=True,
                reason="ArchiveUploaded",
                message="",
            )
        ]
        observer = BulkObserver(mock_ic, mock_sp)

        result = await observer.observe_all()

        assert "ws-1" in result
        assert result["ws-1"]["container"]["running"] is True
        assert result["ws-1"]["volume"]["exists"] is True
        assert result["ws-1"]["archive"]["exists"] is True

    async def test_obs_006_multiple_workspaces(
        self, mock_ic: AsyncMock, mock_sp: AsyncMock
    ):
        """OBS-006: 여러 workspace 처리."""
        mock_ic.list_all.return_value = [
            ContainerInfo(workspace_id="ws-1", running=True, reason="Running", message=""),
            ContainerInfo(workspace_id="ws-2", running=False, reason="Exited", message=""),
        ]
        mock_sp.list_volumes.return_value = [
            VolumeInfo(workspace_id="ws-1", exists=True, reason="VolumeExists", message=""),
            VolumeInfo(workspace_id="ws-3", exists=True, reason="VolumeExists", message=""),
        ]
        observer = BulkObserver(mock_ic, mock_sp)

        result = await observer.observe_all()

        # ws-1: container + volume
        assert result["ws-1"]["container"]["running"] is True
        assert result["ws-1"]["volume"]["exists"] is True
        assert result["ws-1"]["archive"] is None

        # ws-2: container only
        assert result["ws-2"]["container"]["running"] is False
        assert result["ws-2"]["volume"] is None

        # ws-3: volume only
        assert result["ws-3"]["volume"]["exists"] is True
        assert result["ws-3"]["container"] is None

    async def test_obs_007_stopped_container(
        self, mock_ic: AsyncMock, mock_sp: AsyncMock
    ):
        """OBS-007: 정지된 container."""
        mock_ic.list_all.return_value = [
            ContainerInfo(
                workspace_id="ws-1",
                running=False,
                reason="Exited",
                message="Exited (0) 2 hours ago",
            )
        ]
        observer = BulkObserver(mock_ic, mock_sp)

        result = await observer.observe_all()

        assert result["ws-1"]["container"]["running"] is False
        assert result["ws-1"]["container"]["reason"] == "Exited"

    async def test_obs_008_archive_with_error_reason(
        self, mock_ic: AsyncMock, mock_sp: AsyncMock
    ):
        """OBS-008: 오류 상태 archive."""
        mock_sp.list_archives.return_value = [
            ArchiveInfo(
                workspace_id="ws-1",
                archive_key=None,
                exists=False,
                reason="ArchiveCorrupted",
                message="Checksum mismatch",
            )
        ]
        observer = BulkObserver(mock_ic, mock_sp)

        result = await observer.observe_all()

        assert result["ws-1"]["archive"]["exists"] is False
        assert result["ws-1"]["archive"]["reason"] == "ArchiveCorrupted"


class TestBulkObserverModelDump:
    """model_dump() 형식 검증."""

    async def test_container_model_dump_format(
        self, mock_ic: AsyncMock, mock_sp: AsyncMock
    ):
        """container가 올바른 형식으로 직렬화."""
        mock_ic.list_all.return_value = [
            ContainerInfo(workspace_id="ws-1", running=True, reason="Running", message="msg")
        ]
        observer = BulkObserver(mock_ic, mock_sp)

        result = await observer.observe_all()
        container = result["ws-1"]["container"]

        # Pydantic model_dump() 결과 검증
        assert isinstance(container, dict)
        assert container == {
            "workspace_id": "ws-1",
            "running": True,
            "reason": "Running",
            "message": "msg",
        }

    async def test_volume_model_dump_format(
        self, mock_ic: AsyncMock, mock_sp: AsyncMock
    ):
        """volume이 올바른 형식으로 직렬화."""
        mock_sp.list_volumes.return_value = [
            VolumeInfo(workspace_id="ws-1", exists=True, reason="VolumeExists", message="msg")
        ]
        observer = BulkObserver(mock_ic, mock_sp)

        result = await observer.observe_all()
        volume = result["ws-1"]["volume"]

        assert volume == {
            "workspace_id": "ws-1",
            "exists": True,
            "reason": "VolumeExists",
            "message": "msg",
        }

    async def test_archive_model_dump_format(
        self, mock_ic: AsyncMock, mock_sp: AsyncMock
    ):
        """archive가 올바른 형식으로 직렬화."""
        mock_sp.list_archives.return_value = [
            ArchiveInfo(
                workspace_id="ws-1",
                archive_key="ws-1/op/home.tar.zst",
                exists=True,
                reason="ArchiveUploaded",
                message="msg",
            )
        ]
        observer = BulkObserver(mock_ic, mock_sp)

        result = await observer.observe_all()
        archive = result["ws-1"]["archive"]

        assert archive == {
            "workspace_id": "ws-1",
            "archive_key": "ws-1/op/home.tar.zst",
            "exists": True,
            "reason": "ArchiveUploaded",
            "message": "msg",
        }
