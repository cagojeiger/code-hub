"""Tests for Observer Coordinator."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub.core.interfaces.leader import LeaderElection
from codehub.infra.redis_pubsub import ChannelSubscriber
from codehub.control.coordinator.observer import ObserverCoordinator
from codehub.core.interfaces.runtime import (
    WorkspaceRuntime,
    WorkspaceState,
    ContainerStatus,
    VolumeStatus,
    ArchiveStatus,
)


@pytest.fixture
def mock_runtime() -> AsyncMock:
    runtime = AsyncMock(spec=WorkspaceRuntime)
    runtime.observe = AsyncMock(return_value=[])
    return runtime


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
    mock_runtime: AsyncMock,
) -> ObserverCoordinator:
    return ObserverCoordinator(mock_conn, mock_leader, mock_subscriber, mock_runtime)


class TestObserveWithTimeout:
    """_observe_with_timeout() 테스트."""

    async def test_returns_workspace_states(
        self, coordinator: ObserverCoordinator, mock_runtime: AsyncMock
    ):
        """성공 시 WorkspaceState 리스트 반환."""
        mock_runtime.observe.return_value = [
            WorkspaceState(
                workspace_id="ws-1",
                container=ContainerStatus(running=True, healthy=True),
                volume=VolumeStatus(exists=True),
                archive=None,
            )
        ]

        result = await coordinator._observe_with_timeout()

        assert result is not None
        assert len(result) == 1
        assert result[0].workspace_id == "ws-1"
        assert result[0].container.running is True

    async def test_timeout_returns_none(
        self, coordinator: ObserverCoordinator, mock_runtime: AsyncMock
    ):
        """타임아웃 시 None 반환."""
        mock_runtime.observe = AsyncMock(side_effect=asyncio.TimeoutError())

        result = await coordinator._observe_with_timeout()

        assert result is None

    async def test_exception_returns_none(
        self, coordinator: ObserverCoordinator, mock_runtime: AsyncMock
    ):
        """예외 시 None 반환."""
        mock_runtime.observe = AsyncMock(side_effect=Exception("Network error"))

        result = await coordinator._observe_with_timeout()

        assert result is None


class TestObserverTick:
    """tick() 동작 테스트."""

    async def test_skip_when_observation_fails(
        self, coordinator: ObserverCoordinator, mock_conn: MagicMock, mock_runtime: AsyncMock
    ):
        """observe 실패 → tick skip."""
        mock_ws_result = MagicMock()
        mock_ws_result.fetchall.return_value = [("ws-1",)]
        mock_conn.execute.return_value = mock_ws_result

        mock_runtime.observe = AsyncMock(side_effect=asyncio.TimeoutError())

        await coordinator.reconcile()

        mock_conn.commit.assert_not_called()

    async def test_updates_when_succeed(
        self, coordinator: ObserverCoordinator, mock_conn: MagicMock, mock_runtime: AsyncMock
    ):
        """성공 → DB 업데이트."""
        mock_ws_result = MagicMock()
        mock_ws_result.fetchall.return_value = [("ws-1",)]
        mock_update_result = MagicMock()
        mock_update_result.fetchall.return_value = [("ws-1",)]
        mock_conn.execute.side_effect = [mock_ws_result, mock_update_result]

        mock_runtime.observe.return_value = [
            WorkspaceState(
                workspace_id="ws-1",
                container=ContainerStatus(running=True, healthy=True),
                volume=VolumeStatus(exists=True),
                archive=None,
            )
        ]

        await coordinator.reconcile()

        mock_conn.commit.assert_called_once()


class TestBulkUpdateConditionsV2:
    """_bulk_update_conditions_v2() 테스트."""

    async def test_uses_single_query(
        self, coordinator: ObserverCoordinator, mock_conn: MagicMock
    ):
        """N개 workspace → 1회 쿼리."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-1",), ("ws-2",)]
        mock_conn.execute.return_value = mock_result

        states_map = {
            "ws-1": WorkspaceState(
                workspace_id="ws-1",
                container=ContainerStatus(running=True, healthy=True),
                volume=None,
                archive=None,
            ),
            "ws-2": WorkspaceState(
                workspace_id="ws-2",
                container=ContainerStatus(running=False, healthy=False),
                volume=None,
                archive=None,
            ),
        }

        result = await coordinator._bulk_update_conditions_v2({"ws-1", "ws-2"}, states_map)

        assert result == 2
        mock_conn.execute.assert_called_once()

    async def test_sets_null_for_missing_resources(
        self, coordinator: ObserverCoordinator, mock_conn: MagicMock
    ):
        """리소스 없는 workspace → null로 설정 (삭제 감지 핵심 로직)."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-1",)]
        mock_conn.execute.return_value = mock_result

        # ws-1은 DB에 있지만 observe 결과에 없음 → null로 덮어써야 삭제 감지
        await coordinator._bulk_update_conditions_v2({"ws-1"}, {})

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

        # ws-1만 container 있음, ws-2는 observe 결과에 없음
        states_map = {
            "ws-1": WorkspaceState(
                workspace_id="ws-1",
                container=ContainerStatus(running=True, healthy=True),
                volume=None,
                archive=None,
            )
        }

        await coordinator._bulk_update_conditions_v2({"ws-1", "ws-2"}, states_map)

        call_args = mock_conn.execute.call_args
        params = call_args[0][1]
        import json

        # ws-2의 모든 리소스는 null (observe 결과에 없음)
        ws_ids = params["ids"]
        ws2_idx = ws_ids.index("ws-2")
        cond = json.loads(params["conds"][ws2_idx])
        assert cond["container"] is None
        assert cond["volume"] is None
        assert cond["archive"] is None

    async def test_preserves_all_resource_fields(
        self, coordinator: ObserverCoordinator, mock_conn: MagicMock
    ):
        """WorkspaceState의 모든 필드가 conditions에 반영됨."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-1",)]
        mock_conn.execute.return_value = mock_result

        states_map = {
            "ws-1": WorkspaceState(
                workspace_id="ws-1",
                container=ContainerStatus(running=True, healthy=False),
                volume=VolumeStatus(exists=True),
                archive=ArchiveStatus(exists=True, archive_key="test-key"),
            )
        }

        await coordinator._bulk_update_conditions_v2({"ws-1"}, states_map)

        call_args = mock_conn.execute.call_args
        params = call_args[0][1]
        import json
        cond = json.loads(params["conds"][0])

        assert cond["container"]["running"] is True
        assert cond["container"]["healthy"] is False
        assert cond["volume"]["exists"] is True
        assert cond["archive"]["exists"] is True
        assert cond["archive"]["archive_key"] == "test-key"
