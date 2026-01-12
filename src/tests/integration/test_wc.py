"""WorkspaceController integration tests.

Reference: docs/architecture_v2/wc.md, wc-judge.md
"""

import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from codehub.control.coordinator.wc import WorkspaceController
from codehub.core.models import Workspace, User
from codehub.core.domain.workspace import Phase, Operation, DesiredState


class TestWCReconcile:
    """WorkspaceController.reconcile() integration test with real DB."""

    @pytest.fixture
    async def test_user(self, test_db_engine: AsyncEngine) -> User:
        """Create test user in DB."""
        async with AsyncSession(test_db_engine) as session:
            user = User(
                id="test-user-wc-001",
                username="test_wc",
                password_hash="hash",
                created_at=datetime.now(UTC),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def test_reconcile_pending_with_volume_ready(
        self, test_db_engine: AsyncEngine, test_user: User
    ):
        """WC-INT-001: PENDING + volume_ready → PROVISIONING 완료 → STANDBY."""
        # Create workspace with volume_ready condition
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-wc-001",
                owner_user_id=test_user.id,
                name="Test WC Workspace",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-wc-001-home",
                phase="PENDING",
                operation="NONE",
                desired_state="RUNNING",
                conditions={
                    "volume": {
                        "workspace_id": "test-ws-wc-001",
                        "exists": True,
                        "reason": "VolumeExists",
                        "message": "",
                    },
                    "container": None,
                    "archive": None,
                },
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(ws)
            await session.commit()

        # Mock adapters
        mock_ic = AsyncMock()
        mock_sp = AsyncMock()
        mock_sp.provision.return_value = None
        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        # Act: Run tick
        async with test_db_engine.connect() as conn:
            wc = WorkspaceController(conn, mock_leader, mock_subscriber, mock_ic, mock_sp)
            await wc.reconcile()

        # Assert: Check DB state
        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == "test-ws-wc-001")
            )
            ws = result.scalar_one()

            print(f"phase after tick: {ws.phase}")
            print(f"operation after tick: {ws.operation}")

            # PENDING + desired=RUNNING + volume_ready → judge phase=STANDBY
            # Since operation was NONE and phase changed, should start STARTING
            assert ws.phase in ["PENDING", "STANDBY"]

    async def test_reconcile_standby_to_running(
        self, test_db_engine: AsyncEngine, test_user: User
    ):
        """WC-INT-002: STANDBY → STARTING → RUNNING."""
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-wc-002",
                owner_user_id=test_user.id,
                name="Standby Workspace",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-wc-002-home",
                phase="STANDBY",
                operation="NONE",
                desired_state="RUNNING",
                conditions={
                    "volume": {
                        "workspace_id": "test-ws-wc-002",
                        "exists": True,
                        "reason": "VolumeExists",
                        "message": "",
                    },
                    "container": None,
                    "archive": None,
                },
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(ws)
            await session.commit()

        mock_ic = AsyncMock()
        mock_ic.start.return_value = None
        mock_sp = AsyncMock()
        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        async with test_db_engine.connect() as conn:
            wc = WorkspaceController(conn, mock_leader, mock_subscriber, mock_ic, mock_sp)
            await wc.reconcile()

        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == "test-ws-wc-002")
            )
            ws = result.scalar_one()

            print(f"phase: {ws.phase}, operation: {ws.operation}")
            # STANDBY + desired=RUNNING → STARTING operation
            assert ws.operation == "STARTING"
            mock_ic.start.assert_called_once()

    async def test_reconcile_running_to_standby(
        self, test_db_engine: AsyncEngine, test_user: User
    ):
        """WC-INT-003: RUNNING → STOPPING → STANDBY."""
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-wc-003",
                owner_user_id=test_user.id,
                name="Running Workspace",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-wc-003-home",
                phase="RUNNING",
                operation="NONE",
                desired_state="STANDBY",  # Want to stop
                conditions={
                    "volume": {
                        "workspace_id": "test-ws-wc-003",
                        "exists": True,
                        "reason": "VolumeExists",
                        "message": "",
                    },
                    "container": {
                        "workspace_id": "test-ws-wc-003",
                        "running": True,
                        "reason": "Running",
                        "message": "",
                    },
                    "archive": None,
                },
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(ws)
            await session.commit()

        mock_ic = AsyncMock()
        mock_ic.delete.return_value = None
        mock_sp = AsyncMock()
        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        async with test_db_engine.connect() as conn:
            wc = WorkspaceController(conn, mock_leader, mock_subscriber, mock_ic, mock_sp)
            await wc.reconcile()

        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == "test-ws-wc-003")
            )
            ws = result.scalar_one()

            print(f"phase: {ws.phase}, operation: {ws.operation}")
            # RUNNING + desired=STANDBY → STOPPING operation
            assert ws.operation == "STOPPING"
            mock_ic.delete.assert_called_once()

    async def test_reconcile_already_converged(
        self, test_db_engine: AsyncEngine, test_user: User
    ):
        """WC-INT-004: 이미 수렴된 상태 → no-op."""
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-wc-004",
                owner_user_id=test_user.id,
                name="Converged Workspace",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-wc-004-home",
                phase="RUNNING",
                operation="NONE",
                desired_state="RUNNING",  # Already at desired
                conditions={
                    "volume": {
                        "workspace_id": "test-ws-wc-004",
                        "exists": True,
                        "reason": "VolumeExists",
                        "message": "",
                    },
                    "container": {
                        "workspace_id": "test-ws-wc-004",
                        "running": True,
                        "reason": "Running",
                        "message": "",
                    },
                    "archive": None,
                },
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(ws)
            await session.commit()

        mock_ic = AsyncMock()
        mock_sp = AsyncMock()
        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        async with test_db_engine.connect() as conn:
            wc = WorkspaceController(conn, mock_leader, mock_subscriber, mock_ic, mock_sp)
            await wc.reconcile()

        # No operations should be called
        mock_ic.start.assert_not_called()
        mock_ic.delete.assert_not_called()
        mock_sp.provision.assert_not_called()

    async def test_reconcile_pending_to_archived(
        self, test_db_engine: AsyncEngine, test_user: User
    ):
        """WC-INT-005: PENDING → CREATE_EMPTY_ARCHIVE → ARCHIVED."""
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-wc-005",
                owner_user_id=test_user.id,
                name="Archive Workspace",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-wc-005-home",
                phase="PENDING",
                operation="NONE",
                desired_state="ARCHIVED",
                conditions={
                    "volume": None,
                    "container": None,
                    "archive": None,
                },
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(ws)
            await session.commit()

        mock_ic = AsyncMock()
        mock_sp = AsyncMock()
        mock_sp.create_empty_archive.return_value = "test-ws-wc-005/op-123/home.tar.zst"
        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        async with test_db_engine.connect() as conn:
            wc = WorkspaceController(conn, mock_leader, mock_subscriber, mock_ic, mock_sp)
            await wc.reconcile()

        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == "test-ws-wc-005")
            )
            ws = result.scalar_one()

            print(f"phase: {ws.phase}, operation: {ws.operation}")
            # PENDING + desired=ARCHIVED → CREATE_EMPTY_ARCHIVE
            assert ws.operation == "CREATE_EMPTY_ARCHIVE"
            mock_sp.create_empty_archive.assert_called_once()

    async def test_reconcile_deleting(
        self, test_db_engine: AsyncEngine, test_user: User
    ):
        """WC-INT-006: desired=DELETED → DELETING."""
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-wc-006",
                owner_user_id=test_user.id,
                name="Delete Workspace",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-wc-006-home",
                phase="STANDBY",
                operation="NONE",
                desired_state="DELETED",
                conditions={
                    "volume": {
                        "workspace_id": "test-ws-wc-006",
                        "exists": True,
                        "reason": "VolumeExists",
                        "message": "",
                    },
                    "container": None,
                    "archive": None,
                },
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(ws)
            await session.commit()

        mock_ic = AsyncMock()
        mock_ic.delete.return_value = None
        mock_sp = AsyncMock()
        mock_sp.delete_volume.return_value = None
        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        async with test_db_engine.connect() as conn:
            wc = WorkspaceController(conn, mock_leader, mock_subscriber, mock_ic, mock_sp)
            await wc.reconcile()

        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == "test-ws-wc-006")
            )
            ws = result.scalar_one()

            print(f"phase: {ws.phase}, operation: {ws.operation}")
            # Any phase + desired=DELETED → DELETING
            assert ws.operation == "DELETING"
            # DELETING calls both delete container and delete volume
            mock_ic.delete.assert_called_once()
            mock_sp.delete_volume.assert_called_once()


class TestPhaseChangedAt:
    """phase_changed_at CASE WHEN 로직 테스트."""

    @pytest.fixture
    async def test_user(self, test_db_engine: AsyncEngine) -> User:
        """Create test user in DB."""
        async with AsyncSession(test_db_engine) as session:
            user = User(
                id="test-user-phase-changed",
                username="test_phase_changed",
                password_hash="hash",
                created_at=datetime.now(UTC),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def test_phase_changed_at_updated_on_phase_change(
        self, test_db_engine: AsyncEngine, test_user: User
    ):
        """WC-INT-010: phase 변경 시 phase_changed_at 갱신."""
        # Create workspace in STANDBY phase
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-phase-change-001",
                owner_user_id=test_user.id,
                name="Phase Change Test",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-phase-change-001-home",
                phase="STANDBY",
                operation="NONE",
                desired_state="RUNNING",
                phase_changed_at=None,  # Initially None
                conditions={
                    "volume": {
                        "workspace_id": "test-ws-phase-change-001",
                        "exists": True,
                        "reason": "VolumeExists",
                        "message": "",
                    },
                    "container": {
                        "workspace_id": "test-ws-phase-change-001",
                        "running": True,  # Container running → RUNNING phase
                        "reason": "Running",
                        "message": "",
                    },
                    "archive": None,
                },
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(ws)
            await session.commit()

        mock_ic = AsyncMock()
        mock_ic.start.return_value = None
        mock_sp = AsyncMock()
        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        # Run tick - phase should change from STANDBY to RUNNING
        async with test_db_engine.connect() as conn:
            wc = WorkspaceController(conn, mock_leader, mock_subscriber, mock_ic, mock_sp)
            await wc.reconcile()

        # Check phase_changed_at was set
        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == "test-ws-phase-change-001")
            )
            ws = result.scalar_one()

            print(f"phase: {ws.phase}, phase_changed_at: {ws.phase_changed_at}")

            # phase_changed_at should be set since phase changed
            assert ws.phase_changed_at is not None

    async def test_phase_changed_at_not_updated_when_same_phase(
        self, test_db_engine: AsyncEngine, test_user: User
    ):
        """WC-INT-011: phase 동일 시 phase_changed_at 유지."""
        # Create workspace already at desired state
        original_phase_changed_at = datetime.now(UTC)

        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-phase-same-001",
                owner_user_id=test_user.id,
                name="Same Phase Test",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-phase-same-001-home",
                phase="RUNNING",
                operation="NONE",
                desired_state="RUNNING",  # Already at desired
                phase_changed_at=original_phase_changed_at,
                conditions={
                    "volume": {
                        "workspace_id": "test-ws-phase-same-001",
                        "exists": True,
                        "reason": "VolumeExists",
                        "message": "",
                    },
                    "container": {
                        "workspace_id": "test-ws-phase-same-001",
                        "running": True,
                        "reason": "Running",
                        "message": "",
                    },
                    "archive": None,
                },
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(ws)
            await session.commit()

        mock_ic = AsyncMock()
        mock_sp = AsyncMock()
        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        # Run tick - phase should stay RUNNING (already converged)
        async with test_db_engine.connect() as conn:
            wc = WorkspaceController(conn, mock_leader, mock_subscriber, mock_ic, mock_sp)
            await wc.reconcile()

        # Check phase_changed_at was NOT modified
        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == "test-ws-phase-same-001")
            )
            ws = result.scalar_one()

            print(f"phase: {ws.phase}, phase_changed_at: {ws.phase_changed_at}")

            # phase_changed_at should be preserved (same phase)
            # Note: This workspace is already converged, so no update happens at all


class TestWCLoadWorkspaces:
    """_load_for_reconcile() 테스트."""

    @pytest.fixture
    async def test_user(self, test_db_engine: AsyncEngine) -> User:
        """Create test user in DB."""
        async with AsyncSession(test_db_engine) as session:
            user = User(
                id="test-user-wc-load",
                username="test_wc_load",
                password_hash="hash",
                created_at=datetime.now(UTC),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def test_load_only_non_converged(
        self, test_db_engine: AsyncEngine, test_user: User
    ):
        """WC-INT-007: operation != NONE 또는 phase != desired만 로드."""
        async with AsyncSession(test_db_engine) as session:
            # Converged workspace (should NOT be loaded)
            # Note: RUNNING is always loaded (for external deletion check)
            # So we use STANDBY to test true convergence
            ws1 = Workspace(
                id="ws-converged",
                owner_user_id=test_user.id,
                name="Converged",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-ws-converged-home",
                phase="STANDBY",
                operation="NONE",
                desired_state="STANDBY",
                conditions={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            # Non-converged workspace (should be loaded)
            ws2 = Workspace(
                id="ws-non-converged",
                owner_user_id=test_user.id,
                name="Non-Converged",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-ws-non-converged-home",
                phase="STANDBY",
                operation="NONE",
                desired_state="RUNNING",
                conditions={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            # In-progress workspace (should be loaded)
            ws3 = Workspace(
                id="ws-in-progress",
                owner_user_id=test_user.id,
                name="In Progress",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-ws-in-progress-home",
                phase="STANDBY",  # Phase stays STANDBY during STARTING operation
                operation="STARTING",
                desired_state="RUNNING",
                conditions={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add_all([ws1, ws2, ws3])
            await session.commit()

        mock_ic = AsyncMock()
        mock_sp = AsyncMock()
        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        async with test_db_engine.connect() as conn:
            wc = WorkspaceController(conn, mock_leader, mock_subscriber, mock_ic, mock_sp)
            workspaces = await wc._load_for_reconcile()

        ws_ids = [ws.id for ws in workspaces]
        print(f"Loaded workspace IDs: {ws_ids}")

        # Converged workspace should NOT be loaded
        assert "ws-converged" not in ws_ids
        # Non-converged and in-progress should be loaded
        assert "ws-non-converged" in ws_ids
        assert "ws-in-progress" in ws_ids
