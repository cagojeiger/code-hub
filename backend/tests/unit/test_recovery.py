"""Unit tests for startup recovery module.

Tests cover all recovery matrix scenarios:
- PROVISIONING -> RUNNING (healthy)
- PROVISIONING -> ERROR (not healthy)
- STOPPING -> STOPPED (not running) + deprovision
- STOPPING -> RUNNING (still running)
- DELETING -> DELETED (not exists) + deprovision
- DELETING -> ERROR (exists)
- Mixed scenarios (multiple states)
- Empty case (no workspaces to recover)
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.db import User, Workspace, WorkspaceStatus, init_db
from app.db.session import close_db
from app.services.instance.interface import InstanceStatus
from app.services.recovery import startup_recovery


@pytest_asyncio.fixture
async def db_engine():
    """Create an in-memory database for testing."""
    engine = await init_db("sqlite+aiosqlite:///:memory:", echo=False)
    yield engine
    await close_db()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Create a database session for testing."""
    async_session = sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    """Create a test user."""
    user = User(
        username="testuser",
        password_hash=hash_password("test"),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def create_workspace(
    user_id: str,
    status: WorkspaceStatus,
    home_ctx: str | None = None,
) -> Workspace:
    """Helper to create a workspace with given status."""
    return Workspace(
        owner_user_id=user_id,
        name=f"ws-{status.value}",
        image_ref="test:latest",
        home_store_key=f"users/{user_id}/workspaces/test/home",
        status=status,
        home_ctx=home_ctx,
    )


class TestStartupRecoveryProvisioning:
    """Tests for PROVISIONING state recovery."""

    @pytest.mark.asyncio
    async def test_provisioning_to_running_when_healthy(
        self, db_session: AsyncSession, test_user: User
    ):
        """PROVISIONING -> RUNNING when container is running and healthy."""
        ws = create_workspace(test_user.id, WorkspaceStatus.PROVISIONING)
        db_session.add(ws)
        await db_session.commit()
        await db_session.refresh(ws)

        mock_instance = AsyncMock()
        mock_instance.get_status.return_value = InstanceStatus(
            exists=True, running=True, healthy=True, port=8080
        )
        mock_storage = AsyncMock()

        count = await startup_recovery(db_session, mock_instance, mock_storage)

        assert count == 1
        await db_session.refresh(ws)
        assert ws.status == WorkspaceStatus.RUNNING
        mock_storage.deprovision.assert_not_called()

    @pytest.mark.asyncio
    async def test_provisioning_to_error_when_not_running(
        self, db_session: AsyncSession, test_user: User
    ):
        """PROVISIONING -> ERROR when container is not running."""
        ws = create_workspace(test_user.id, WorkspaceStatus.PROVISIONING)
        db_session.add(ws)
        await db_session.commit()
        await db_session.refresh(ws)

        mock_instance = AsyncMock()
        mock_instance.get_status.return_value = InstanceStatus(
            exists=True, running=False, healthy=False
        )
        mock_storage = AsyncMock()

        count = await startup_recovery(db_session, mock_instance, mock_storage)

        assert count == 1
        await db_session.refresh(ws)
        assert ws.status == WorkspaceStatus.ERROR

    @pytest.mark.asyncio
    async def test_provisioning_to_error_when_not_healthy(
        self, db_session: AsyncSession, test_user: User
    ):
        """PROVISIONING -> ERROR when container is running but not healthy."""
        ws = create_workspace(test_user.id, WorkspaceStatus.PROVISIONING)
        db_session.add(ws)
        await db_session.commit()
        await db_session.refresh(ws)

        mock_instance = AsyncMock()
        mock_instance.get_status.return_value = InstanceStatus(
            exists=True, running=True, healthy=False
        )
        mock_storage = AsyncMock()

        count = await startup_recovery(db_session, mock_instance, mock_storage)

        assert count == 1
        await db_session.refresh(ws)
        assert ws.status == WorkspaceStatus.ERROR


class TestStartupRecoveryStopping:
    """Tests for STOPPING state recovery."""

    @pytest.mark.asyncio
    async def test_stopping_to_stopped_when_not_running(
        self, db_session: AsyncSession, test_user: User
    ):
        """STOPPING -> STOPPED when container is not running."""
        ws = create_workspace(
            test_user.id, WorkspaceStatus.STOPPING, home_ctx="/path/to/home"
        )
        db_session.add(ws)
        await db_session.commit()
        await db_session.refresh(ws)

        mock_instance = AsyncMock()
        mock_instance.get_status.return_value = InstanceStatus(
            exists=True, running=False, healthy=False
        )
        mock_storage = AsyncMock()

        count = await startup_recovery(db_session, mock_instance, mock_storage)

        assert count == 1
        await db_session.refresh(ws)
        assert ws.status == WorkspaceStatus.STOPPED
        assert ws.home_ctx is None
        mock_storage.deprovision.assert_called_once_with("/path/to/home")

    @pytest.mark.asyncio
    async def test_stopping_to_stopped_no_home_ctx(
        self, db_session: AsyncSession, test_user: User
    ):
        """STOPPING -> STOPPED when home_ctx is None (no deprovision call)."""
        ws = create_workspace(test_user.id, WorkspaceStatus.STOPPING, home_ctx=None)
        db_session.add(ws)
        await db_session.commit()
        await db_session.refresh(ws)

        mock_instance = AsyncMock()
        mock_instance.get_status.return_value = InstanceStatus(
            exists=False, running=False, healthy=False
        )
        mock_storage = AsyncMock()

        count = await startup_recovery(db_session, mock_instance, mock_storage)

        assert count == 1
        await db_session.refresh(ws)
        assert ws.status == WorkspaceStatus.STOPPED
        mock_storage.deprovision.assert_not_called()

    @pytest.mark.asyncio
    async def test_stopping_to_running_when_still_running(
        self, db_session: AsyncSession, test_user: User
    ):
        """STOPPING -> RUNNING when container is still running."""
        ws = create_workspace(
            test_user.id, WorkspaceStatus.STOPPING, home_ctx="/path/to/home"
        )
        db_session.add(ws)
        await db_session.commit()
        await db_session.refresh(ws)

        mock_instance = AsyncMock()
        mock_instance.get_status.return_value = InstanceStatus(
            exists=True, running=True, healthy=True, port=8080
        )
        mock_storage = AsyncMock()

        count = await startup_recovery(db_session, mock_instance, mock_storage)

        assert count == 1
        await db_session.refresh(ws)
        assert ws.status == WorkspaceStatus.RUNNING
        # home_ctx should NOT be cleared when reverting to RUNNING
        assert ws.home_ctx == "/path/to/home"
        mock_storage.deprovision.assert_not_called()


class TestStartupRecoveryDeleting:
    """Tests for DELETING state recovery."""

    @pytest.mark.asyncio
    async def test_deleting_to_deleted_when_not_exists(
        self, db_session: AsyncSession, test_user: User
    ):
        """DELETING -> DELETED when container does not exist."""
        ws = create_workspace(
            test_user.id, WorkspaceStatus.DELETING, home_ctx="/path/to/home"
        )
        db_session.add(ws)
        await db_session.commit()
        await db_session.refresh(ws)

        mock_instance = AsyncMock()
        mock_instance.get_status.return_value = InstanceStatus(
            exists=False, running=False, healthy=False
        )
        mock_storage = AsyncMock()

        count = await startup_recovery(db_session, mock_instance, mock_storage)

        assert count == 1
        await db_session.refresh(ws)
        assert ws.status == WorkspaceStatus.DELETED
        assert ws.home_ctx is None
        assert ws.deleted_at is not None
        mock_storage.deprovision.assert_called_once_with("/path/to/home")

    @pytest.mark.asyncio
    async def test_deleting_to_deleted_no_home_ctx(
        self, db_session: AsyncSession, test_user: User
    ):
        """DELETING -> DELETED when home_ctx is None."""
        ws = create_workspace(test_user.id, WorkspaceStatus.DELETING, home_ctx=None)
        db_session.add(ws)
        await db_session.commit()
        await db_session.refresh(ws)

        mock_instance = AsyncMock()
        mock_instance.get_status.return_value = InstanceStatus(
            exists=False, running=False, healthy=False
        )
        mock_storage = AsyncMock()

        count = await startup_recovery(db_session, mock_instance, mock_storage)

        assert count == 1
        await db_session.refresh(ws)
        assert ws.status == WorkspaceStatus.DELETED
        assert ws.deleted_at is not None
        mock_storage.deprovision.assert_not_called()

    @pytest.mark.asyncio
    async def test_deleting_to_error_when_exists(
        self, db_session: AsyncSession, test_user: User
    ):
        """DELETING -> ERROR when container still exists."""
        ws = create_workspace(
            test_user.id, WorkspaceStatus.DELETING, home_ctx="/path/to/home"
        )
        db_session.add(ws)
        await db_session.commit()
        await db_session.refresh(ws)

        mock_instance = AsyncMock()
        mock_instance.get_status.return_value = InstanceStatus(
            exists=True, running=False, healthy=False
        )
        mock_storage = AsyncMock()

        count = await startup_recovery(db_session, mock_instance, mock_storage)

        assert count == 1
        await db_session.refresh(ws)
        assert ws.status == WorkspaceStatus.ERROR
        # home_ctx should NOT be cleared on error
        assert ws.home_ctx == "/path/to/home"
        mock_storage.deprovision.assert_not_called()


class TestStartupRecoveryMixed:
    """Tests for mixed scenarios."""

    @pytest.mark.asyncio
    async def test_multiple_workspaces_in_different_states(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test recovery with multiple workspaces in different transitional states."""
        ws_provisioning = create_workspace(test_user.id, WorkspaceStatus.PROVISIONING)
        ws_provisioning.name = "ws-prov"
        ws_stopping = create_workspace(
            test_user.id, WorkspaceStatus.STOPPING, home_ctx="/stop/home"
        )
        ws_stopping.name = "ws-stop"
        ws_deleting = create_workspace(
            test_user.id, WorkspaceStatus.DELETING, home_ctx="/del/home"
        )
        ws_deleting.name = "ws-del"

        db_session.add_all([ws_provisioning, ws_stopping, ws_deleting])
        await db_session.commit()

        mock_instance = AsyncMock()

        # Set up different responses for each workspace
        async def get_status(workspace_id: str) -> InstanceStatus:
            if workspace_id == ws_provisioning.id:
                return InstanceStatus(
                    exists=True, running=True, healthy=True, port=8080
                )
            elif workspace_id == ws_stopping.id:
                return InstanceStatus(exists=True, running=False, healthy=False)
            else:  # ws_deleting
                return InstanceStatus(exists=False, running=False, healthy=False)

        mock_instance.get_status.side_effect = get_status
        mock_storage = AsyncMock()

        count = await startup_recovery(db_session, mock_instance, mock_storage)

        assert count == 3

        await db_session.refresh(ws_provisioning)
        await db_session.refresh(ws_stopping)
        await db_session.refresh(ws_deleting)

        assert ws_provisioning.status == WorkspaceStatus.RUNNING
        assert ws_stopping.status == WorkspaceStatus.STOPPED
        assert ws_stopping.home_ctx is None
        assert ws_deleting.status == WorkspaceStatus.DELETED
        assert ws_deleting.home_ctx is None
        assert ws_deleting.deleted_at is not None

        # Check deprovision calls
        assert mock_storage.deprovision.call_count == 2

    @pytest.mark.asyncio
    async def test_no_transitional_workspaces(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test recovery when no workspaces are in transitional states."""
        ws_running = create_workspace(test_user.id, WorkspaceStatus.RUNNING)
        ws_stopped = create_workspace(test_user.id, WorkspaceStatus.STOPPED)
        ws_stopped.name = "ws-stopped"

        db_session.add_all([ws_running, ws_stopped])
        await db_session.commit()

        mock_instance = AsyncMock()
        mock_storage = AsyncMock()

        count = await startup_recovery(db_session, mock_instance, mock_storage)

        assert count == 0
        mock_instance.get_status.assert_not_called()


class TestStartupRecoveryErrorHandling:
    """Tests for error handling during recovery."""

    @pytest.mark.asyncio
    async def test_continues_on_single_workspace_error(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test that recovery continues even if one workspace fails."""
        ws1 = create_workspace(test_user.id, WorkspaceStatus.PROVISIONING)
        ws1.name = "ws-1"
        ws2 = create_workspace(test_user.id, WorkspaceStatus.STOPPING)
        ws2.name = "ws-2"

        db_session.add_all([ws1, ws2])
        await db_session.commit()

        mock_instance = AsyncMock()

        # First call raises error, second returns valid status
        async def get_status(workspace_id: str) -> InstanceStatus:
            if workspace_id == ws1.id:
                raise RuntimeError("Instance controller error")
            return InstanceStatus(exists=False, running=False, healthy=False)

        mock_instance.get_status.side_effect = get_status
        mock_storage = AsyncMock()

        count = await startup_recovery(db_session, mock_instance, mock_storage)

        # Only ws2 should be recovered (ws1 failed)
        assert count == 1

        await db_session.refresh(ws1)
        await db_session.refresh(ws2)

        # ws1 should remain in PROVISIONING (recovery failed)
        assert ws1.status == WorkspaceStatus.PROVISIONING
        # ws2 should be recovered to STOPPED
        assert ws2.status == WorkspaceStatus.STOPPED

    @pytest.mark.asyncio
    async def test_continues_on_deprovision_error(
        self, db_session: AsyncSession, test_user: User
    ):
        """Test that state update succeeds even if deprovision fails."""
        ws = create_workspace(
            test_user.id, WorkspaceStatus.STOPPING, home_ctx="/path/to/home"
        )
        db_session.add(ws)
        await db_session.commit()
        await db_session.refresh(ws)

        mock_instance = AsyncMock()
        mock_instance.get_status.return_value = InstanceStatus(
            exists=False, running=False, healthy=False
        )
        mock_storage = AsyncMock()
        mock_storage.deprovision.side_effect = RuntimeError("Storage error")

        count = await startup_recovery(db_session, mock_instance, mock_storage)

        assert count == 1
        await db_session.refresh(ws)
        # State should still be updated even if deprovision failed
        assert ws.status == WorkspaceStatus.STOPPED
        assert ws.home_ctx is None
