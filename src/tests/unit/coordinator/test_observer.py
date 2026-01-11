"""Tests for Observer Coordinator."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub.core.interfaces.leader import LeaderElection
from codehub.infra.redis_pubsub import ChannelSubscriber
from codehub.control.coordinator.observer import BulkObserver, ObserverCoordinator
from codehub.core.interfaces.instance import ContainerInfo, InstanceController
from codehub.core.interfaces.storage import StorageProvider, VolumeInfo


@pytest.fixture
def mock_ic() -> AsyncMock:
    ic = AsyncMock(spec=InstanceController)
    ic.list_all = AsyncMock(return_value=[])
    return ic


@pytest.fixture
def mock_sp() -> AsyncMock:
    sp = AsyncMock(spec=StorageProvider)
    sp.list_volumes = AsyncMock(return_value=[])
    sp.list_archives = AsyncMock(return_value=[])
    return sp


class TestBulkObserver:
    """병렬 API 호출 테스트."""

    async def test_empty_returns_empty_dicts(self, mock_ic: AsyncMock, mock_sp: AsyncMock):
        observer = BulkObserver(mock_ic, mock_sp)
        containers, volumes, archives = await observer.observe_all()
        assert containers == {}
        assert volumes == {}
        assert archives == {}

    async def test_indexes_by_workspace_id(self, mock_ic: AsyncMock, mock_sp: AsyncMock):
        mock_ic.list_all.return_value = [
            ContainerInfo(workspace_id="ws-1", running=True, reason="Running", message="")
        ]
        mock_sp.list_volumes.return_value = [
            VolumeInfo(workspace_id="ws-1", exists=True, reason="VolumeExists", message="")
        ]
        observer = BulkObserver(mock_ic, mock_sp)

        containers, volumes, archives = await observer.observe_all()

        assert containers["ws-1"].running is True
        assert volumes["ws-1"].exists is True
        assert archives == {}

    async def test_timeout_returns_none(self, mock_ic: AsyncMock, mock_sp: AsyncMock):
        mock_ic.list_all = AsyncMock(side_effect=asyncio.TimeoutError())
        observer = BulkObserver(mock_ic, mock_sp)

        containers, volumes, archives = await observer.observe_all()

        assert containers is None
        assert volumes == {}
        assert archives == {}

    async def test_exception_returns_none(self, mock_ic: AsyncMock, mock_sp: AsyncMock):
        mock_sp.list_volumes = AsyncMock(side_effect=Exception("S3 error"))
        observer = BulkObserver(mock_ic, mock_sp)

        containers, volumes, archives = await observer.observe_all()

        assert containers == {}
        assert volumes is None
        assert archives == {}


@pytest.fixture
def mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    return conn


@pytest.fixture
def mock_leader() -> MagicMock:
    leader = MagicMock(spec=LeaderElection)
    leader.is_leader = True
    return leader


@pytest.fixture
def mock_subscriber() -> MagicMock:
    return MagicMock(spec=ChannelSubscriber)


@pytest.fixture
def coordinator(
    mock_conn: MagicMock,
    mock_leader: MagicMock,
    mock_subscriber: MagicMock,
    mock_ic: AsyncMock,
    mock_sp: AsyncMock,
) -> ObserverCoordinator:
    return ObserverCoordinator(mock_conn, mock_leader, mock_subscriber, mock_ic, mock_sp)


class TestObserverTick:
    """tick() 동작 테스트."""

    async def test_skip_when_any_observation_fails(
        self, coordinator: ObserverCoordinator, mock_conn: MagicMock, mock_ic: AsyncMock
    ):
        """하나라도 실패 → tick skip."""
        mock_ws_result = MagicMock()
        mock_ws_result.fetchall.return_value = [("ws-1",)]
        mock_conn.execute.return_value = mock_ws_result

        mock_ic.list_all = AsyncMock(side_effect=asyncio.TimeoutError())

        await coordinator.reconcile()

        mock_conn.commit.assert_not_called()

    async def test_updates_when_all_succeed(
        self, coordinator: ObserverCoordinator, mock_conn: MagicMock, mock_ic: AsyncMock
    ):
        """전체 성공 → DB 업데이트."""
        mock_ws_result = MagicMock()
        mock_ws_result.fetchall.return_value = [("ws-1",)]
        mock_update_result = MagicMock()
        mock_update_result.fetchall.return_value = [("ws-1",)]
        mock_conn.execute.side_effect = [mock_ws_result, mock_update_result]

        mock_ic.list_all.return_value = [
            ContainerInfo(workspace_id="ws-1", running=True, reason="Running", message="")
        ]

        await coordinator.reconcile()

        mock_conn.commit.assert_called_once()


class TestBulkUpdateConditions:
    """_bulk_update_conditions() 테스트."""

    async def test_uses_single_query(self, coordinator: ObserverCoordinator, mock_conn: MagicMock):
        """N개 workspace → 1회 쿼리."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-1",), ("ws-2",)]
        mock_conn.execute.return_value = mock_result

        containers = {
            "ws-1": ContainerInfo(workspace_id="ws-1", running=True, reason="Running", message=""),
            "ws-2": ContainerInfo(workspace_id="ws-2", running=False, reason="Exited", message=""),
        }

        result = await coordinator._bulk_update_conditions({"ws-1", "ws-2"}, containers, {}, {})

        assert result == 2
        mock_conn.execute.assert_called_once()

    async def test_sets_null_for_missing_resources(
        self, coordinator: ObserverCoordinator, mock_conn: MagicMock
    ):
        """리소스 없는 workspace → null로 설정 (삭제 감지 핵심 로직)."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-1",)]
        mock_conn.execute.return_value = mock_result

        # ws-1은 DB에 있지만 관측된 리소스 없음 → null로 덮어써야 삭제 감지
        await coordinator._bulk_update_conditions({"ws-1"}, {}, {}, {})

        call_args = mock_conn.execute.call_args
        params = call_args[0][1]
        import json
        cond = json.loads(params["conds"][0])
        assert cond == {"container": None, "volume": None, "archive": None}

    async def test_overwrites_deleted_resource_with_null(
        self, coordinator: ObserverCoordinator, mock_conn: MagicMock
    ):
        """이전에 container 있었다가 삭제 → null로 덮어씀."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-1",), ("ws-2",)]
        mock_conn.execute.return_value = mock_result

        # ws-1만 container 있음, ws-2는 삭제됨
        containers = {
            "ws-1": ContainerInfo(workspace_id="ws-1", running=True, reason="Running", message="")
        }

        await coordinator._bulk_update_conditions({"ws-1", "ws-2"}, containers, {}, {})

        call_args = mock_conn.execute.call_args
        params = call_args[0][1]
        import json

        # ws-2의 container는 null (삭제됨)
        ws_ids = params["ids"]
        ws2_idx = ws_ids.index("ws-2")
        cond = json.loads(params["conds"][ws2_idx])
        assert cond["container"] is None
