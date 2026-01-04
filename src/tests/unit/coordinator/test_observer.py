"""Tests for BulkObserver and ObserverCoordinator.

Reference: docs/architecture_v2/wc-observer.md
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub.control.coordinator.base import LeaderElection, NotifySubscriber
from codehub.control.coordinator.observer import BulkObserver, ObserverCoordinator
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


# =============================================================================
# ObserverCoordinator._bulk_update_conditions() Tests
# =============================================================================


@pytest.fixture
def mock_conn() -> MagicMock:
    """Mock AsyncConnection."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    return conn


@pytest.fixture
def mock_leader() -> MagicMock:
    """Mock LeaderElection (always leader)."""
    leader = MagicMock(spec=LeaderElection)
    leader.is_leader = True
    leader.try_acquire = MagicMock(return_value=True)
    return leader


@pytest.fixture
def mock_notify() -> MagicMock:
    """Mock NotifySubscriber."""
    return MagicMock(spec=NotifySubscriber)


@pytest.fixture
def observer_coordinator(
    mock_conn: MagicMock,
    mock_leader: MagicMock,
    mock_notify: MagicMock,
    mock_ic: AsyncMock,
    mock_sp: AsyncMock,
) -> ObserverCoordinator:
    """Create ObserverCoordinator with mocked dependencies."""
    return ObserverCoordinator(mock_conn, mock_leader, mock_notify, mock_ic, mock_sp)


class TestObserverCoordinatorBulkUpdate:
    """_bulk_update_conditions() tests - PostgreSQL unnest bulk update."""

    async def test_empty_list_returns_zero(
        self,
        observer_coordinator: ObserverCoordinator,
        mock_conn: MagicMock,
    ):
        """Empty updates list returns 0 without DB call."""
        result = await observer_coordinator._bulk_update_conditions([])

        assert result == 0
        mock_conn.execute.assert_not_called()

    async def test_uses_single_query(
        self,
        observer_coordinator: ObserverCoordinator,
        mock_conn: MagicMock,
    ):
        """N updates use single execute() call (O(1) round-trips)."""
        now = datetime.now(UTC)
        updates = [
            ("ws-1", {"container": None, "volume": None, "archive": None}, now),
            ("ws-2", {"container": None, "volume": None, "archive": None}, now),
            ("ws-3", {"container": None, "volume": None, "archive": None}, now),
        ]

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-1",), ("ws-2",), ("ws-3",)]
        mock_conn.execute.return_value = mock_result

        result = await observer_coordinator._bulk_update_conditions(updates)

        assert result == 3
        mock_conn.execute.assert_called_once()  # Single query!

    async def test_returns_updated_count(
        self,
        observer_coordinator: ObserverCoordinator,
        mock_conn: MagicMock,
    ):
        """Returns count of actually updated rows."""
        now = datetime.now(UTC)
        updates = [
            ("ws-1", {"container": None}, now),
            ("ws-2", {"container": None}, now),
            ("ws-not-exists", {"container": None}, now),  # doesn't exist in DB
        ]

        # Only 2 rows updated (ws-not-exists not found)
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-1",), ("ws-2",)]
        mock_conn.execute.return_value = mock_result

        result = await observer_coordinator._bulk_update_conditions(updates)

        assert result == 2  # Only 2 actually updated

    async def test_jsonb_serialization(
        self,
        observer_coordinator: ObserverCoordinator,
        mock_conn: MagicMock,
    ):
        """conditions dict is JSON serialized for PostgreSQL."""
        now = datetime.now(UTC)
        conditions = {
            "container": {"workspace_id": "ws-1", "running": True},
            "volume": {"workspace_id": "ws-1", "exists": True},
            "archive": None,
        }
        updates = [("ws-1", conditions, now)]

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-1",)]
        mock_conn.execute.return_value = mock_result

        await observer_coordinator._bulk_update_conditions(updates)

        # Verify execute was called with proper parameters
        call_args = mock_conn.execute.call_args
        params = call_args[0][1]  # Second positional arg is params dict

        assert params["ids"] == ["ws-1"]
        assert len(params["conditions"]) == 1
        # conditions should be JSON string
        import json
        parsed = json.loads(params["conditions"][0])
        assert parsed["container"]["running"] is True
        assert parsed["volume"]["exists"] is True
        assert parsed["archive"] is None
