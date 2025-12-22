"""Unit tests for Workspace CRUD API.

Tests cover:
- GET /api/v1/workspaces - List workspaces
- POST /api/v1/workspaces - Create workspace
- GET /api/v1/workspaces/{id} - Get workspace detail
- PATCH /api/v1/workspaces/{id} - Update workspace
- DELETE /api/v1/workspaces/{id} - Delete workspace
- POST /api/v1/workspaces/{id}:start - Start workspace
- POST /api/v1/workspaces/{id}:stop - Stop workspace
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.api.v1.dependencies import get_instance_controller, get_storage_provider
from app.db import Workspace, WorkspaceStatus
from app.main import app
from app.services.instance.interface import InstanceStatus
from app.services.storage.interface import ProvisionResult


class TestListWorkspaces:
    """Tests for GET /api/v1/workspaces."""

    @pytest.mark.asyncio
    async def test_list_empty(self, async_client: AsyncClient):
        """Test listing workspaces when none exist."""
        response = await async_client.get("/api/v1/workspaces")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert "pagination" in data
        assert data["pagination"]["total"] == 0

    @pytest.mark.asyncio
    async def test_list_workspaces(self, async_client: AsyncClient):
        """Test listing workspaces after creating some."""
        # Create two workspaces
        await async_client.post(
            "/api/v1/workspaces",
            json={"name": "workspace-1"},
        )
        await async_client.post(
            "/api/v1/workspaces",
            json={"name": "workspace-2"},
        )

        response = await async_client.get("/api/v1/workspaces")
        assert response.status_code == 200
        data = response.json()
        workspaces = data["items"]
        assert len(workspaces) == 2
        names = {ws["name"] for ws in workspaces}
        assert names == {"workspace-1", "workspace-2"}
        assert data["pagination"]["total"] == 2


class TestCreateWorkspace:
    """Tests for POST /api/v1/workspaces."""

    @pytest.mark.asyncio
    async def test_create_workspace_minimal(self, async_client: AsyncClient):
        """Test creating workspace with minimal data."""
        response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "my-workspace"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "my-workspace"
        assert data["description"] is None
        assert data["memo"] is None
        assert data["status"] == "CREATED"
        assert "id" in data
        assert "path" in data
        assert data["path"] == f"/w/{data['id']}/"
        assert "created_at" in data
        assert "updated_at" in data

    @pytest.mark.asyncio
    async def test_create_workspace_full(self, async_client: AsyncClient):
        """Test creating workspace with all fields."""
        response = await async_client.post(
            "/api/v1/workspaces",
            json={
                "name": "full-workspace",
                "description": "A test workspace",
                "memo": "Some notes here",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "full-workspace"
        assert data["description"] == "A test workspace"
        assert data["memo"] == "Some notes here"

    @pytest.mark.asyncio
    async def test_create_workspace_empty_name_fails(self, async_client: AsyncClient):
        """Test that empty name fails validation."""
        response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": ""},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_workspace_missing_name_fails(self, async_client: AsyncClient):
        """Test that missing name fails validation."""
        response = await async_client.post(
            "/api/v1/workspaces",
            json={},
        )
        assert response.status_code == 422


class TestGetWorkspace:
    """Tests for GET /api/v1/workspaces/{id}."""

    @pytest.mark.asyncio
    async def test_get_workspace(self, async_client: AsyncClient):
        """Test getting a workspace by ID."""
        # Create workspace first
        create_response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "get-test", "description": "Test desc"},
        )
        workspace_id = create_response.json()["id"]

        # Get workspace
        response = await async_client.get(f"/api/v1/workspaces/{workspace_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == workspace_id
        assert data["name"] == "get-test"
        assert data["description"] == "Test desc"

    @pytest.mark.asyncio
    async def test_get_workspace_not_found(self, async_client: AsyncClient):
        """Test getting a non-existent workspace."""
        response = await async_client.get("/api/v1/workspaces/nonexistent")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"


class TestUpdateWorkspace:
    """Tests for PATCH /api/v1/workspaces/{id}."""

    @pytest.mark.asyncio
    async def test_update_workspace_name(self, async_client: AsyncClient):
        """Test updating workspace name."""
        # Create workspace
        create_response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "original-name"},
        )
        workspace_id = create_response.json()["id"]

        # Update name
        response = await async_client.patch(
            f"/api/v1/workspaces/{workspace_id}",
            json={"name": "new-name"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "new-name"

    @pytest.mark.asyncio
    async def test_update_workspace_description(self, async_client: AsyncClient):
        """Test updating workspace description."""
        # Create workspace
        create_response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "desc-test"},
        )
        workspace_id = create_response.json()["id"]

        # Update description
        response = await async_client.patch(
            f"/api/v1/workspaces/{workspace_id}",
            json={"description": "New description"},
        )
        assert response.status_code == 200
        assert response.json()["description"] == "New description"

    @pytest.mark.asyncio
    async def test_update_workspace_memo(self, async_client: AsyncClient):
        """Test updating workspace memo."""
        # Create workspace
        create_response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "memo-test"},
        )
        workspace_id = create_response.json()["id"]

        # Update memo
        response = await async_client.patch(
            f"/api/v1/workspaces/{workspace_id}",
            json={"memo": "New memo content"},
        )
        assert response.status_code == 200
        assert response.json()["memo"] == "New memo content"

    @pytest.mark.asyncio
    async def test_update_workspace_multiple_fields(self, async_client: AsyncClient):
        """Test updating multiple fields at once."""
        # Create workspace
        create_response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "multi-test"},
        )
        workspace_id = create_response.json()["id"]

        # Update multiple fields
        response = await async_client.patch(
            f"/api/v1/workspaces/{workspace_id}",
            json={
                "name": "updated-name",
                "description": "Updated desc",
                "memo": "Updated memo",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "updated-name"
        assert data["description"] == "Updated desc"
        assert data["memo"] == "Updated memo"

    @pytest.mark.asyncio
    async def test_update_workspace_empty_body(self, async_client: AsyncClient):
        """Test updating with empty body (no changes)."""
        # Create workspace
        create_response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "no-change-test"},
        )
        workspace_id = create_response.json()["id"]
        original_name = create_response.json()["name"]

        # Update with empty body
        response = await async_client.patch(
            f"/api/v1/workspaces/{workspace_id}",
            json={},
        )
        assert response.status_code == 200
        assert response.json()["name"] == original_name

    @pytest.mark.asyncio
    async def test_update_workspace_not_found(self, async_client: AsyncClient):
        """Test updating a non-existent workspace."""
        response = await async_client.patch(
            "/api/v1/workspaces/nonexistent",
            json={"name": "new-name"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"


class TestDeleteWorkspace:
    """Tests for DELETE /api/v1/workspaces/{id}."""

    @pytest.mark.asyncio
    async def test_delete_workspace_created(self, async_client: AsyncClient):
        """Test deleting a workspace in CREATED state."""
        # Create workspace
        create_response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "delete-test"},
        )
        workspace_id = create_response.json()["id"]

        # Delete workspace
        response = await async_client.delete(f"/api/v1/workspaces/{workspace_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_response = await async_client.get(f"/api/v1/workspaces/{workspace_id}")
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_workspace_not_found(self, async_client: AsyncClient):
        """Test deleting a non-existent workspace."""
        response = await async_client.delete("/api/v1/workspaces/nonexistent")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_deleted_workspace_not_in_list(self, async_client: AsyncClient):
        """Test that deleted workspaces don't appear in list."""
        # Create two workspaces
        create1 = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "keep-me"},
        )
        create2 = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "delete-me"},
        )
        ws1_id = create1.json()["id"]
        ws2_id = create2.json()["id"]

        # Delete one
        await async_client.delete(f"/api/v1/workspaces/{ws2_id}")

        # List should only show one
        response = await async_client.get("/api/v1/workspaces")
        data = response.json()
        workspaces = data["items"]
        assert len(workspaces) == 1
        assert workspaces[0]["id"] == ws1_id

    @pytest.fixture
    def mock_storage(self):
        """Create a mock storage provider."""
        storage = AsyncMock()
        storage.provision = AsyncMock(
            return_value=ProvisionResult(
                home_mount="/mock/path/home",
                home_ctx="/mock/path/home",
            )
        )
        storage.deprovision = AsyncMock()
        return storage

    @pytest.fixture
    def mock_instance(self):
        """Create a mock instance controller."""
        instance = AsyncMock()
        instance.start_workspace = AsyncMock()
        instance.stop_workspace = AsyncMock()
        instance.delete_workspace = AsyncMock()
        instance.get_status = AsyncMock(
            return_value=InstanceStatus(
                exists=True,
                running=True,
                healthy=True,
                port=8080,
            )
        )
        return instance

    @pytest.fixture
    def client_with_mocks(self, async_client, mock_storage, mock_instance):
        """Client with mocked storage and instance dependencies."""
        app.dependency_overrides[get_storage_provider] = lambda: mock_storage
        app.dependency_overrides[get_instance_controller] = lambda: mock_instance
        yield async_client
        # Cleanup is done in async_client fixture

    @pytest.mark.asyncio
    async def test_delete_workspace_from_stopped(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting workspace from STOPPED state."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "delete-stopped-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to STOPPED with home_ctx
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.STOPPED, home_ctx="/mock/path/home")
        )
        await db_session.commit()

        # Delete workspace
        response = await client_with_mocks.delete(
            f"/api/v1/workspaces/{workspace_id}"
        )
        assert response.status_code == 204

        # Verify it's gone
        get_response = await client_with_mocks.get(
            f"/api/v1/workspaces/{workspace_id}"
        )
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_workspace_from_error(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting workspace from ERROR state."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "delete-error-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to ERROR
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.ERROR)
        )
        await db_session.commit()

        # Delete workspace - should succeed
        response = await client_with_mocks.delete(
            f"/api/v1/workspaces/{workspace_id}"
        )
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_workspace_invalid_state_running(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting workspace in RUNNING state fails."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "delete-running-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to RUNNING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.RUNNING)
        )
        await db_session.commit()

        # Try to delete - should fail
        response = await client_with_mocks.delete(
            f"/api/v1/workspaces/{workspace_id}"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_delete_workspace_invalid_state_provisioning(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting workspace in PROVISIONING state fails."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "delete-provisioning-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to PROVISIONING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.PROVISIONING)
        )
        await db_session.commit()

        # Try to delete - should fail
        response = await client_with_mocks.delete(
            f"/api/v1/workspaces/{workspace_id}"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_delete_workspace_invalid_state_stopping(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting workspace in STOPPING state fails."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "delete-stopping-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to STOPPING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.STOPPING)
        )
        await db_session.commit()

        # Try to delete - should fail
        response = await client_with_mocks.delete(
            f"/api/v1/workspaces/{workspace_id}"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_delete_workspace_invalid_state_deleting(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test deleting workspace in DELETING state fails."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "delete-deleting-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to DELETING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.DELETING)
        )
        await db_session.commit()

        # Try to delete - should fail
        response = await client_with_mocks.delete(
            f"/api/v1/workspaces/{workspace_id}"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_delete_workspace_cas_prevents_double_delete(
        self, client_with_mocks: AsyncClient
    ):
        """Test that CAS prevents concurrent delete requests."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "cas-delete-test"},
        )
        workspace_id = create_response.json()["id"]

        # First delete succeeds
        response1 = await client_with_mocks.delete(
            f"/api/v1/workspaces/{workspace_id}"
        )
        assert response1.status_code == 204

        # Second delete fails (workspace is now DELETING/DELETED)
        response2 = await client_with_mocks.delete(
            f"/api/v1/workspaces/{workspace_id}"
        )
        assert response2.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_workspace_concurrent_requests(
        self, client_with_mocks: AsyncClient
    ):
        """Test concurrent delete requests - only one should succeed."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "concurrent-delete-test"},
        )
        workspace_id = create_response.json()["id"]

        # Send two concurrent delete requests
        responses = await asyncio.gather(
            client_with_mocks.delete(f"/api/v1/workspaces/{workspace_id}"),
            client_with_mocks.delete(f"/api/v1/workspaces/{workspace_id}"),
            return_exceptions=True,
        )

        # Count successes and failures
        successes = sum(
            1 for r in responses if hasattr(r, "status_code") and r.status_code == 204
        )
        failures = sum(
            1
            for r in responses
            if hasattr(r, "status_code") and r.status_code in (404, 409)
        )

        # Exactly one should succeed due to CAS
        assert successes == 1
        assert failures == 1


class TestWorkspaceIdempotency:
    """Tests for idempotency and edge cases."""

    @pytest.mark.asyncio
    async def test_workspace_id_is_ulid(self, async_client: AsyncClient):
        """Test that workspace IDs are ULIDs (26 chars)."""
        response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "ulid-test"},
        )
        workspace_id = response.json()["id"]
        assert len(workspace_id) == 26

    @pytest.mark.asyncio
    async def test_workspace_path_format(self, async_client: AsyncClient):
        """Test that workspace path follows spec format."""
        response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "path-test"},
        )
        data = response.json()
        # Path should be /w/{id}/
        assert data["path"] == f"/w/{data['id']}/"

    @pytest.mark.asyncio
    async def test_create_multiple_workspaces_unique_ids(
        self, async_client: AsyncClient
    ):
        """Test that each workspace gets a unique ID."""
        ids = set()
        for i in range(5):
            response = await async_client.post(
                "/api/v1/workspaces",
                json={"name": f"ws-{i}"},
            )
            ids.add(response.json()["id"])
        assert len(ids) == 5


class TestStartWorkspace:
    """Tests for POST /api/v1/workspaces/{id}:start."""

    @pytest.fixture
    def mock_storage(self):
        """Create a mock storage provider."""
        storage = AsyncMock()
        storage.provision = AsyncMock(
            return_value=ProvisionResult(
                home_mount="/mock/path/home",
                home_ctx="/mock/path/home",
            )
        )
        storage.deprovision = AsyncMock()
        return storage

    @pytest.fixture
    def mock_instance(self):
        """Create a mock instance controller."""
        instance = AsyncMock()
        instance.start_workspace = AsyncMock()
        instance.get_status = AsyncMock(
            return_value=InstanceStatus(
                exists=True,
                running=True,
                healthy=True,
                port=8080,
            )
        )
        return instance

    @pytest.fixture
    def client_with_mocks(self, async_client, mock_storage, mock_instance):
        """Client with mocked storage and instance dependencies."""
        app.dependency_overrides[get_storage_provider] = lambda: mock_storage
        app.dependency_overrides[get_instance_controller] = lambda: mock_instance
        yield async_client
        # Cleanup is done in async_client fixture

    @pytest.mark.asyncio
    async def test_start_workspace_from_created(
        self, client_with_mocks: AsyncClient
    ):
        """Test starting workspace from CREATED state."""
        # Create workspace (starts in CREATED state)
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "start-test"},
        )
        workspace_id = create_response.json()["id"]
        assert create_response.json()["status"] == "CREATED"

        # Start workspace
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:start"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == workspace_id
        assert data["status"] == "PROVISIONING"

    @pytest.mark.asyncio
    async def test_start_workspace_not_found(self, client_with_mocks: AsyncClient):
        """Test starting a non-existent workspace."""
        response = await client_with_mocks.post(
            "/api/v1/workspaces/nonexistent:start"
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_start_workspace_invalid_state_provisioning(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test starting workspace in PROVISIONING state fails."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "provisioning-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to PROVISIONING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.PROVISIONING)
        )
        await db_session.commit()

        # Try to start - should fail
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:start"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_start_workspace_invalid_state_running(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test starting workspace in RUNNING state fails."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "running-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to RUNNING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.RUNNING)
        )
        await db_session.commit()

        # Try to start - should fail
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:start"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_start_workspace_invalid_state_stopping(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test starting workspace in STOPPING state fails."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "stopping-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to STOPPING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.STOPPING)
        )
        await db_session.commit()

        # Try to start - should fail
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:start"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_start_workspace_invalid_state_deleting(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test starting workspace in DELETING state fails."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "deleting-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to DELETING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.DELETING)
        )
        await db_session.commit()

        # Try to start - should fail
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:start"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_start_workspace_from_stopped(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test starting workspace from STOPPED state."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "stopped-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to STOPPED
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.STOPPED)
        )
        await db_session.commit()

        # Start workspace - should succeed
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:start"
        )
        assert response.status_code == 200
        assert response.json()["status"] == "PROVISIONING"

    @pytest.mark.asyncio
    async def test_start_workspace_from_error(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test starting workspace from ERROR state (retry)."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "error-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to ERROR
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.ERROR)
        )
        await db_session.commit()

        # Start workspace - should succeed (retry from error)
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:start"
        )
        assert response.status_code == 200
        assert response.json()["status"] == "PROVISIONING"

    @pytest.mark.asyncio
    async def test_start_workspace_cas_prevents_double_start(
        self, client_with_mocks: AsyncClient
    ):
        """Test that CAS prevents concurrent start requests."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "cas-test"},
        )
        workspace_id = create_response.json()["id"]

        # First start succeeds
        response1 = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:start"
        )
        assert response1.status_code == 200
        assert response1.json()["status"] == "PROVISIONING"

        # Second start fails (workspace is now PROVISIONING)
        response2 = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:start"
        )
        assert response2.status_code == 409
        assert response2.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_start_workspace_concurrent_requests(
        self, client_with_mocks: AsyncClient
    ):
        """Test concurrent start requests - only one should succeed."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "concurrent-test"},
        )
        workspace_id = create_response.json()["id"]

        # Send two concurrent start requests
        responses = await asyncio.gather(
            client_with_mocks.post(f"/api/v1/workspaces/{workspace_id}:start"),
            client_with_mocks.post(f"/api/v1/workspaces/{workspace_id}:start"),
            return_exceptions=True,
        )

        # Count successes and failures
        successes = sum(
            1 for r in responses if hasattr(r, "status_code") and r.status_code == 200
        )
        failures = sum(
            1 for r in responses if hasattr(r, "status_code") and r.status_code == 409
        )

        # Exactly one should succeed due to CAS
        assert successes == 1
        assert failures == 1


class TestStopWorkspace:
    """Tests for POST /api/v1/workspaces/{id}:stop."""

    @pytest.fixture
    def mock_storage(self):
        """Create a mock storage provider."""
        storage = AsyncMock()
        storage.provision = AsyncMock(
            return_value=ProvisionResult(
                home_mount="/mock/path/home",
                home_ctx="/mock/path/home",
            )
        )
        storage.deprovision = AsyncMock()
        return storage

    @pytest.fixture
    def mock_instance(self):
        """Create a mock instance controller."""
        instance = AsyncMock()
        instance.start_workspace = AsyncMock()
        instance.stop_workspace = AsyncMock()
        instance.get_status = AsyncMock(
            return_value=InstanceStatus(
                exists=True,
                running=True,
                healthy=True,
                port=8080,
            )
        )
        return instance

    @pytest.fixture
    def client_with_mocks(self, async_client, mock_storage, mock_instance):
        """Client with mocked storage and instance dependencies."""
        app.dependency_overrides[get_storage_provider] = lambda: mock_storage
        app.dependency_overrides[get_instance_controller] = lambda: mock_instance
        yield async_client
        # Cleanup is done in async_client fixture

    @pytest.mark.asyncio
    async def test_stop_workspace_from_running(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test stopping workspace from RUNNING state."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "stop-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to RUNNING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.RUNNING)
        )
        await db_session.commit()

        # Stop workspace
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:stop"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == workspace_id
        assert data["status"] == "STOPPING"

    @pytest.mark.asyncio
    async def test_stop_workspace_not_found(self, client_with_mocks: AsyncClient):
        """Test stopping a non-existent workspace."""
        response = await client_with_mocks.post(
            "/api/v1/workspaces/nonexistent:stop"
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "WORKSPACE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_stop_workspace_invalid_state_created(
        self, client_with_mocks: AsyncClient
    ):
        """Test stopping workspace in CREATED state fails."""
        # Create workspace (starts in CREATED state)
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "created-stop-test"},
        )
        workspace_id = create_response.json()["id"]

        # Try to stop - should fail
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:stop"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_stop_workspace_invalid_state_provisioning(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test stopping workspace in PROVISIONING state fails."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "provisioning-stop-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to PROVISIONING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.PROVISIONING)
        )
        await db_session.commit()

        # Try to stop - should fail
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:stop"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_stop_workspace_invalid_state_stopping(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test stopping workspace in STOPPING state fails."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "stopping-stop-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to STOPPING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.STOPPING)
        )
        await db_session.commit()

        # Try to stop - should fail
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:stop"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_stop_workspace_invalid_state_stopped(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test stopping workspace in STOPPED state fails."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "stopped-stop-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to STOPPED
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.STOPPED)
        )
        await db_session.commit()

        # Try to stop - should fail
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:stop"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_stop_workspace_invalid_state_deleting(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test stopping workspace in DELETING state fails."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "deleting-stop-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to DELETING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.DELETING)
        )
        await db_session.commit()

        # Try to stop - should fail
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:stop"
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_stop_workspace_from_error(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test stopping workspace from ERROR state (retry/cleanup)."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "error-stop-test"},
        )
        workspace_id = create_response.json()["id"]

        # Manually set to ERROR
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.ERROR)
        )
        await db_session.commit()

        # Stop workspace - should succeed (cleanup from error)
        response = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:stop"
        )
        assert response.status_code == 200
        assert response.json()["status"] == "STOPPING"

    @pytest.mark.asyncio
    async def test_stop_workspace_cas_prevents_double_stop(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test that CAS prevents concurrent stop requests."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "cas-stop-test"},
        )
        workspace_id = create_response.json()["id"]

        # Set to RUNNING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.RUNNING)
        )
        await db_session.commit()

        # First stop succeeds
        response1 = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:stop"
        )
        assert response1.status_code == 200
        assert response1.json()["status"] == "STOPPING"

        # Second stop fails (workspace is now STOPPING)
        response2 = await client_with_mocks.post(
            f"/api/v1/workspaces/{workspace_id}:stop"
        )
        assert response2.status_code == 409
        assert response2.json()["error"]["code"] == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_stop_workspace_concurrent_requests(
        self, client_with_mocks: AsyncClient, db_session: AsyncSession
    ):
        """Test concurrent stop requests - only one should succeed."""
        # Create workspace
        create_response = await client_with_mocks.post(
            "/api/v1/workspaces",
            json={"name": "concurrent-stop-test"},
        )
        workspace_id = create_response.json()["id"]

        # Set to RUNNING
        await db_session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(status=WorkspaceStatus.RUNNING)
        )
        await db_session.commit()

        # Send two concurrent stop requests
        responses = await asyncio.gather(
            client_with_mocks.post(f"/api/v1/workspaces/{workspace_id}:stop"),
            client_with_mocks.post(f"/api/v1/workspaces/{workspace_id}:stop"),
            return_exceptions=True,
        )

        # Count successes and failures
        successes = sum(
            1 for r in responses if hasattr(r, "status_code") and r.status_code == 200
        )
        failures = sum(
            1 for r in responses if hasattr(r, "status_code") and r.status_code == 409
        )

        # Exactly one should succeed due to CAS
        assert successes == 1
        assert failures == 1
