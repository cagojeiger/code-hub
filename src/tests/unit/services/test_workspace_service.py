from datetime import UTC, datetime
from typing import Callable
from unittest.mock import MagicMock, patch

import pytest

from codehub.core.domain import DesiredState, Phase
from codehub.core.errors import (
    BadRequestError,
    ForbiddenError,
    RunningLimitExceededError,
    WorkspaceNotFoundError,
)
from codehub.core.models import Workspace
from codehub.services import workspace_service


class TestCreateWorkspace:
    async def test_create_basic_workspace(
        self,
        mock_db,
        mock_settings: MagicMock,
    ) -> None:
        fixed_now = datetime(2026, 1, 1, tzinfo=UTC)
        with (
            patch("codehub.services.workspace_service.uuid4", return_value="ws-123"),
            patch("codehub.services.workspace_service.datetime") as mock_datetime,
        ):
            mock_datetime.now.return_value = fixed_now

            workspace = await workspace_service.create_workspace(
                db=mock_db,
                user_id="user-1",
                name="My Workspace",
            )

        assert workspace.id == "ws-123"
        assert workspace.owner_user_id == "user-1"
        assert workspace.name == "My Workspace"
        assert workspace.image_ref == mock_settings.runtime.default_image
        assert workspace.home_store_key == "codehub-ws-ws-123-home"
        assert workspace.phase == Phase.PENDING.value
        assert workspace.desired_state == DesiredState.RUNNING.value
        assert workspace.created_at == fixed_now
        assert workspace.updated_at == fixed_now
        assert workspace.last_access_at == fixed_now
        mock_db.add.assert_called_once_with(workspace)
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once_with(workspace)

    async def test_create_uses_default_image_when_none(
        self,
        mock_db,
        mock_settings: MagicMock,
    ) -> None:
        mock_settings.runtime.default_image = "python:3.13"

        with patch("codehub.services.workspace_service.uuid4", return_value="ws-124"):
            workspace = await workspace_service.create_workspace(
                db=mock_db,
                user_id="user-1",
                name="Default Image Workspace",
                image_ref=None,
            )

        assert workspace.image_ref == "python:3.13"

    async def test_create_with_custom_image(self, mock_db) -> None:
        with patch("codehub.services.workspace_service.uuid4", return_value="ws-125"):
            workspace = await workspace_service.create_workspace(
                db=mock_db,
                user_id="user-1",
                name="Custom Image Workspace",
                image_ref="ghcr.io/custom/image:1.0",
            )

        assert workspace.image_ref == "ghcr.io/custom/image:1.0"

    async def test_create_from_archived_source(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        source = make_workspace(
            id="src-1",
            owner_user_id="user-1",
            phase=Phase.ARCHIVED.value,
            archive_key="archives/src-1/home.tar.zst",
        )
        source_result = MagicMock()
        source_result.scalar_one_or_none.return_value = source
        mock_db.execute.return_value = source_result

        with patch("codehub.services.workspace_service.uuid4", return_value="ws-126"):
            workspace = await workspace_service.create_workspace(
                db=mock_db,
                user_id="user-1",
                name="From Archive",
                source_workspace_id="src-1",
            )

        assert workspace.phase == Phase.ARCHIVED.value
        assert workspace.archive_key == "archives/src-1/home.tar.zst"

    async def test_create_from_source_not_archived_raises(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        source = make_workspace(
            id="src-2",
            owner_user_id="user-1",
            phase=Phase.RUNNING.value,
            archive_key="archives/src-2/home.tar.zst",
        )
        source_result = MagicMock()
        source_result.scalar_one_or_none.return_value = source
        mock_db.execute.return_value = source_result

        with pytest.raises(BadRequestError, match="Source workspace must be archived"):
            await workspace_service.create_workspace(
                db=mock_db,
                user_id="user-1",
                name="Invalid Source",
                source_workspace_id="src-2",
            )

        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()

    async def test_create_from_source_no_archive_key_raises(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        source = make_workspace(
            id="src-3",
            owner_user_id="user-1",
            phase=Phase.ARCHIVED.value,
            archive_key=None,
        )
        source_result = MagicMock()
        source_result.scalar_one_or_none.return_value = source
        mock_db.execute.return_value = source_result

        with pytest.raises(BadRequestError, match="Source workspace has no archive"):
            await workspace_service.create_workspace(
                db=mock_db,
                user_id="user-1",
                name="No Archive Source",
                source_workspace_id="src-3",
            )

        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_called()


class TestGetWorkspace:
    async def test_get_existing_workspace(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws = make_workspace(id="ws-get-1")
        result = MagicMock()
        result.scalar_one_or_none.return_value = ws
        mock_db.execute.return_value = result

        loaded = await workspace_service.get_workspace(mock_db, "ws-get-1")

        assert loaded is ws

    async def test_get_nonexistent_raises(self, mock_db) -> None:
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result

        with pytest.raises(WorkspaceNotFoundError):
            await workspace_service.get_workspace(mock_db, "missing")

    async def test_get_with_owner_verified(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws = make_workspace(id="ws-get-2", owner_user_id="user-1")
        result = MagicMock()
        result.scalar_one_or_none.return_value = ws
        mock_db.execute.return_value = result

        loaded = await workspace_service.get_workspace(mock_db, "ws-get-2", user_id="user-1")

        assert loaded is ws

    async def test_get_with_wrong_owner_raises(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws = make_workspace(id="ws-get-3", owner_user_id="other-user")
        result = MagicMock()
        result.scalar_one_or_none.return_value = ws
        mock_db.execute.return_value = result

        with pytest.raises(ForbiddenError):
            await workspace_service.get_workspace(mock_db, "ws-get-3", user_id="user-1")


class TestListWorkspaces:
    async def test_list_returns_user_workspaces(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws1 = make_workspace(id="ws-list-1", owner_user_id="user-1")
        ws2 = make_workspace(id="ws-list-2", owner_user_id="user-1")
        result = MagicMock()
        result.scalars.return_value.all.return_value = [ws1, ws2]
        mock_db.execute.return_value = result

        workspaces = await workspace_service.list_workspaces(mock_db, "user-1")

        assert workspaces == [ws1, ws2]

    async def test_list_excludes_deleted(self, mock_db) -> None:
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = result

        await workspace_service.list_workspaces(mock_db, "user-1")

        stmt = mock_db.execute.call_args.args[0]
        assert "deleted_at IS NULL" in str(stmt)

    async def test_list_pagination(self, mock_db) -> None:
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = result

        await workspace_service.list_workspaces(mock_db, "user-1", limit=10, offset=20)

        stmt = mock_db.execute.call_args.args[0]
        assert stmt._limit_clause.value == 10
        assert stmt._offset_clause.value == 20


class TestUpdateWorkspace:
    async def test_update_name_only(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        original_now = datetime(2025, 12, 1, tzinfo=UTC)
        fixed_now = datetime(2026, 1, 2, tzinfo=UTC)
        ws = make_workspace(
            id="ws-upd-1",
            name="Old Name",
            description="Old Desc",
            memo="Old Memo",
            updated_at=original_now,
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = ws
        mock_db.execute.return_value = result

        with patch("codehub.services.workspace_service.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            updated = await workspace_service.update_workspace(
                mock_db,
                workspace_id="ws-upd-1",
                user_id="user-1",
                name="New Name",
            )

        assert updated.name == "New Name"
        assert updated.description == "Old Desc"
        assert updated.memo == "Old Memo"
        assert updated.updated_at == fixed_now
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once_with(ws)

    async def test_update_multiple_fields(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws = make_workspace(id="ws-upd-2")
        result = MagicMock()
        result.scalar_one_or_none.return_value = ws
        mock_db.execute.return_value = result

        updated = await workspace_service.update_workspace(
            mock_db,
            workspace_id="ws-upd-2",
            user_id="user-1",
            name="Updated",
            description="Updated Description",
            memo="Updated Memo",
        )

        assert updated.name == "Updated"
        assert updated.description == "Updated Description"
        assert updated.memo == "Updated Memo"

    async def test_update_desired_state(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws = make_workspace(id="ws-upd-3", desired_state=DesiredState.STANDBY.value)
        result = MagicMock()
        result.scalar_one_or_none.return_value = ws
        mock_db.execute.return_value = result

        updated = await workspace_service.update_workspace(
            mock_db,
            workspace_id="ws-upd-3",
            user_id="user-1",
            desired_state=DesiredState.ARCHIVED,
        )

        assert updated.desired_state == DesiredState.ARCHIVED.value

    async def test_update_wrong_owner_raises(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws = make_workspace(id="ws-upd-4", owner_user_id="other-user")
        result = MagicMock()
        result.scalar_one_or_none.return_value = ws
        mock_db.execute.return_value = result

        with pytest.raises(ForbiddenError):
            await workspace_service.update_workspace(
                mock_db,
                workspace_id="ws-upd-4",
                user_id="user-1",
                name="Should Fail",
            )


class TestDeleteWorkspace:
    async def test_soft_delete_sets_deleted_at(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws = make_workspace(id="ws-del-1")
        result = MagicMock()
        result.scalar_one_or_none.return_value = ws
        mock_db.execute.return_value = result
        fixed_now = datetime(2026, 1, 3, tzinfo=UTC)

        with patch("codehub.services.workspace_service.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            await workspace_service.delete_workspace(
                mock_db,
                workspace_id="ws-del-1",
                user_id="user-1",
            )

        assert ws.deleted_at == fixed_now
        assert ws.desired_state == DesiredState.DELETED.value
        assert ws.updated_at == fixed_now
        mock_db.commit.assert_called_once()

    async def test_delete_wrong_owner_raises(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws = make_workspace(id="ws-del-2", owner_user_id="other-user")
        result = MagicMock()
        result.scalar_one_or_none.return_value = ws
        mock_db.execute.return_value = result

        with pytest.raises(ForbiddenError):
            await workspace_service.delete_workspace(
                mock_db,
                workspace_id="ws-del-2",
                user_id="user-1",
            )


class TestCountRunningWorkspaces:
    async def test_count_running_phase(self, mock_db) -> None:
        result = MagicMock()
        result.scalar.return_value = 2
        mock_db.execute.return_value = result

        count = await workspace_service.count_running_workspaces(mock_db, "user-1")

        assert count == 2

    async def test_count_running_desired_state(self, mock_db) -> None:
        result = MagicMock()
        result.scalar.return_value = 1
        mock_db.execute.return_value = result

        count = await workspace_service.count_running_workspaces(mock_db, "user-1")

        assert count == 1

    async def test_count_zero_when_none(self, mock_db) -> None:
        result = MagicMock()
        result.scalar.return_value = None
        mock_db.execute.return_value = result

        count = await workspace_service.count_running_workspaces(mock_db, "user-1")

        assert count == 0


class TestSetDesiredState:
    async def test_set_desired_state_success(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws = make_workspace(id="ws-state-1", desired_state=DesiredState.STANDBY.value)
        result = MagicMock()
        result.scalar_one_or_none.return_value = ws
        mock_db.execute.return_value = result
        fixed_now = datetime(2026, 1, 4, tzinfo=UTC)

        with patch("codehub.services.workspace_service.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            await workspace_service.set_desired_state(
                mock_db,
                workspace_id="ws-state-1",
                desired_state=DesiredState.RUNNING,
            )

        assert ws.desired_state == DesiredState.RUNNING.value
        assert ws.updated_at == fixed_now
        mock_db.commit.assert_called_once()

    async def test_set_desired_state_not_found_raises(self, mock_db) -> None:
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result

        with pytest.raises(WorkspaceNotFoundError):
            await workspace_service.set_desired_state(
                mock_db,
                workspace_id="missing",
                desired_state=DesiredState.RUNNING,
            )


class TestRequestStart:
    async def test_request_start_success(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws = make_workspace(id="ws-start-1", desired_state=DesiredState.STANDBY.value)
        get_result = MagicMock()
        get_result.scalar_one_or_none.return_value = ws
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        mock_db.execute.side_effect = [get_result, count_result]
        fixed_now = datetime(2026, 1, 5, tzinfo=UTC)

        with patch("codehub.services.workspace_service.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            updated = await workspace_service.request_start(
                mock_db,
                workspace_id="ws-start-1",
                user_id="user-1",
            )

        assert updated is ws
        assert updated.desired_state == DesiredState.RUNNING.value
        assert updated.updated_at == fixed_now
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once_with(ws)

    async def test_request_start_already_running_idempotent(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws = make_workspace(id="ws-start-2", desired_state=DesiredState.RUNNING.value)
        get_result = MagicMock()
        get_result.scalar_one_or_none.return_value = ws
        mock_db.execute.return_value = get_result

        loaded = await workspace_service.request_start(
            mock_db,
            workspace_id="ws-start-2",
            user_id="user-1",
        )

        assert loaded is ws
        mock_db.commit.assert_not_called()
        mock_db.refresh.assert_not_called()
        assert mock_db.execute.call_count == 1

    async def test_request_start_limit_exceeded_raises(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws = make_workspace(id="ws-start-3", desired_state=DesiredState.STANDBY.value)
        get_result = MagicMock()
        get_result.scalar_one_or_none.return_value = ws
        count_result = MagicMock()
        count_result.scalar.return_value = 3
        mock_db.execute.side_effect = [get_result, count_result]

        with pytest.raises(RunningLimitExceededError):
            await workspace_service.request_start(
                mock_db,
                workspace_id="ws-start-3",
                user_id="user-1",
            )

        mock_db.commit.assert_not_called()
        mock_db.refresh.assert_not_called()

    async def test_request_start_wrong_owner_raises(
        self,
        mock_db,
        make_workspace: Callable[..., Workspace],
    ) -> None:
        ws = make_workspace(id="ws-start-4", owner_user_id="other-user")
        get_result = MagicMock()
        get_result.scalar_one_or_none.return_value = ws
        mock_db.execute.return_value = get_result

        with pytest.raises(ForbiddenError):
            await workspace_service.request_start(
                mock_db,
                workspace_id="ws-start-4",
                user_id="user-1",
            )
