"""Tests for GCRunner functionality.

Reference: docs/spec/05-data-plane.md (Archive GC)
Contract #9: GC Separation & Protection
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub.control.coordinator.scheduler_gc import GCRunner
from codehub.core.interfaces.runtime import (
    WorkspaceRuntime,
    WorkspaceState,
    ContainerStatus,
    VolumeStatus,
    ArchiveStatus,
    GCResult,
)


@pytest.fixture
def mock_conn() -> MagicMock:
    """Mock AsyncConnection."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    return conn


@pytest.fixture
def mock_runtime() -> MagicMock:
    """Mock WorkspaceRuntime."""
    runtime = MagicMock(spec=WorkspaceRuntime)
    runtime.observe = AsyncMock(return_value=[])
    runtime.delete = AsyncMock()
    runtime.run_gc = AsyncMock(return_value=GCResult(deleted_count=0, deleted_keys=[]))
    return runtime


@pytest.fixture
def runner(
    mock_conn: MagicMock,
    mock_runtime: MagicMock,
) -> GCRunner:
    """Create GCRunner with mocked dependencies."""
    return GCRunner(mock_conn, mock_runtime)


class TestGetProtectedArchives:
    """_get_protected_archives() tests."""

    async def test_returns_workspace_op_id_pairs(
        self,
        runner: GCRunner,
        mock_conn: MagicMock,
    ):
        """Returns (workspace_id, op_id) pairs from DB."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("ws-abc123", "op1"),
            ("ws-def456", "op2"),
        ]
        mock_conn.execute.return_value = mock_result

        result = await runner._get_protected_archives()

        assert result is not None
        assert len(result) == 2
        assert ("ws-abc123", "op1") in result
        assert ("ws-def456", "op2") in result

    async def test_empty_db(
        self,
        runner: GCRunner,
        mock_conn: MagicMock,
    ):
        """Returns empty list when no protected archives."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        result = await runner._get_protected_archives()

        assert result == []

    async def test_handles_db_error(
        self,
        runner: GCRunner,
        mock_conn: MagicMock,
    ):
        """Returns None on DB error."""
        mock_conn.execute.side_effect = Exception("DB error")

        result = await runner._get_protected_archives()

        assert result is None


class TestGetValidWorkspaceIds:
    """_get_valid_workspace_ids() tests."""

    async def test_returns_valid_ids(
        self,
        runner: GCRunner,
        mock_conn: MagicMock,
    ):
        """Returns workspace IDs from DB."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("ws-abc123",),
            ("ws-def456",),
        ]
        mock_conn.execute.return_value = mock_result

        result = await runner._get_valid_workspace_ids()

        assert result == {"ws-abc123", "ws-def456"}


class TestCleanupOrphanResources:
    """_cleanup_orphan_resources() tests."""

    async def test_no_orphans(
        self,
        runner: GCRunner,
        mock_runtime: MagicMock,
        mock_conn: MagicMock,
    ):
        """No deletion when all workspaces are valid."""
        # observe returns one workspace
        mock_runtime.observe.return_value = [
            WorkspaceState(
                workspace_id="ws-valid",
                container=ContainerStatus(running=True, healthy=True),
                volume=VolumeStatus(exists=True),
                archive=None,
            )
        ]

        # DB has the same workspace
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-valid",)]
        mock_conn.execute.return_value = mock_result

        await runner._cleanup_orphan_resources()

        mock_runtime.delete.assert_not_called()

    async def test_deletes_orphan_workspace(
        self,
        runner: GCRunner,
        mock_runtime: MagicMock,
        mock_conn: MagicMock,
    ):
        """Deletes workspace not in DB."""
        # observe returns orphan workspace
        mock_runtime.observe.return_value = [
            WorkspaceState(
                workspace_id="ws-orphan",
                container=ContainerStatus(running=True, healthy=True),
                volume=VolumeStatus(exists=True),
                archive=None,
            )
        ]

        # DB has no matching workspace
        mock_ws_result = MagicMock()
        mock_ws_result.fetchall.return_value = []

        # protected archives query
        mock_protected_result = MagicMock()
        mock_protected_result.fetchall.return_value = []

        mock_conn.execute.side_effect = [mock_ws_result, mock_protected_result]

        await runner._cleanup_orphan_resources()

        mock_runtime.delete.assert_called_once_with("ws-orphan")

    async def test_preserves_valid_workspaces(
        self,
        runner: GCRunner,
        mock_runtime: MagicMock,
        mock_conn: MagicMock,
    ):
        """Does not delete workspaces that exist in DB."""
        # observe returns valid workspace
        mock_runtime.observe.return_value = [
            WorkspaceState(
                workspace_id="ws-valid",
                container=ContainerStatus(running=True, healthy=True),
                volume=VolumeStatus(exists=True),
                archive=None,
            )
        ]

        # DB has matching workspace
        mock_ws_result = MagicMock()
        mock_ws_result.fetchall.return_value = [("ws-valid",)]

        mock_protected_result = MagicMock()
        mock_protected_result.fetchall.return_value = []

        mock_conn.execute.side_effect = [mock_ws_result, mock_protected_result]

        await runner._cleanup_orphan_resources()

        mock_runtime.delete.assert_not_called()


class TestRunGC:
    """Archive GC tests via runtime.run_gc()."""

    async def test_calls_run_gc_with_protected(
        self,
        runner: GCRunner,
        mock_runtime: MagicMock,
        mock_conn: MagicMock,
    ):
        """Calls runtime.run_gc with protected list."""
        mock_runtime.observe.return_value = []

        # DB returns protected archives
        mock_ws_result = MagicMock()
        mock_ws_result.fetchall.return_value = []

        mock_protected_result = MagicMock()
        mock_protected_result.fetchall.return_value = [
            ("ws-abc123", "op1"),
            ("ws-def456", "op2"),
        ]

        mock_conn.execute.side_effect = [mock_ws_result, mock_protected_result]

        await runner._cleanup_orphan_resources()

        mock_runtime.run_gc.assert_called_once()
        call_args = mock_runtime.run_gc.call_args
        protected = call_args[0][0]
        assert ("ws-abc123", "op1") in protected
        assert ("ws-def456", "op2") in protected

    async def test_handles_gc_result(
        self,
        runner: GCRunner,
        mock_runtime: MagicMock,
        mock_conn: MagicMock,
    ):
        """Handles GCResult from runtime.run_gc()."""
        mock_runtime.observe.return_value = []
        mock_runtime.run_gc.return_value = GCResult(
            deleted_count=2,
            deleted_keys=["ws-orphan1/op1/home.tar.zst", "ws-orphan2/op2/home.tar.zst"],
        )

        mock_ws_result = MagicMock()
        mock_ws_result.fetchall.return_value = []

        mock_protected_result = MagicMock()
        mock_protected_result.fetchall.return_value = []

        mock_conn.execute.side_effect = [mock_ws_result, mock_protected_result]

        # Should not raise
        await runner._cleanup_orphan_resources()

        mock_runtime.run_gc.assert_called_once()


class TestRun:
    """run() tests."""

    async def test_observe_error_skips_gc(
        self,
        runner: GCRunner,
        mock_runtime: MagicMock,
    ):
        """run() skips GC when observe fails."""
        mock_runtime.observe.side_effect = RuntimeError("Network error")

        await runner.run()

        mock_runtime.delete.assert_not_called()
        mock_runtime.run_gc.assert_not_called()

    async def test_runs_full_gc_cycle(
        self,
        runner: GCRunner,
        mock_runtime: MagicMock,
        mock_conn: MagicMock,
    ):
        """run() executes full GC cycle."""
        mock_runtime.observe.return_value = []

        mock_ws_result = MagicMock()
        mock_ws_result.fetchall.return_value = []

        mock_protected_result = MagicMock()
        mock_protected_result.fetchall.return_value = []

        mock_conn.execute.side_effect = [mock_ws_result, mock_protected_result]

        await runner.run()

        mock_runtime.observe.assert_called_once()
        mock_runtime.run_gc.assert_called_once()


class TestObserverPattern:
    """Observer pattern race condition safety tests."""

    async def test_race_condition_safe(
        self,
        runner: GCRunner,
        mock_runtime: MagicMock,
        mock_conn: MagicMock,
    ):
        """Resources queried first, then DB (Observer pattern prevents race condition).

        Scenario: New workspace created between observe and DB query.
        - T1: observe returns [A] (no B yet)
        - T2: workspace B created (DB + resources)
        - T3: valid_ws_ids = {A, B} from DB

        Result: orphan = {A} - {A, B} = {} -> B is safe!
        """
        # Only workspace A at time of observe
        mock_runtime.observe.return_value = [
            WorkspaceState(
                workspace_id="ws-a",
                container=ContainerStatus(running=True, healthy=True),
                volume=None,
                archive=None,
            )
        ]

        # DB has both A and B (B was created after observe)
        mock_ws_result = MagicMock()
        mock_ws_result.fetchall.return_value = [("ws-a",), ("ws-b",)]

        mock_protected_result = MagicMock()
        mock_protected_result.fetchall.return_value = []

        mock_conn.execute.side_effect = [mock_ws_result, mock_protected_result]

        await runner._cleanup_orphan_resources()

        # Neither should be deleted
        mock_runtime.delete.assert_not_called()
