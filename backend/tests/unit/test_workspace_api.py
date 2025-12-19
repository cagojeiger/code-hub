"""Unit tests for Workspace CRUD API.

Tests cover:
- GET /api/v1/workspaces - List workspaces
- POST /api/v1/workspaces - Create workspace
- GET /api/v1/workspaces/{id} - Get workspace detail
- PATCH /api/v1/workspaces/{id} - Update workspace
- DELETE /api/v1/workspaces/{id} - Delete workspace
"""

import pytest
from httpx import AsyncClient


class TestListWorkspaces:
    """Tests for GET /api/v1/workspaces."""

    @pytest.mark.asyncio
    async def test_list_empty(self, async_client: AsyncClient):
        """Test listing workspaces when none exist."""
        response = await async_client.get("/api/v1/workspaces")
        assert response.status_code == 200
        assert response.json() == []

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
        workspaces = response.json()
        assert len(workspaces) == 2
        names = {ws["name"] for ws in workspaces}
        assert names == {"workspace-1", "workspace-2"}


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
        assert "url" in data
        assert data["url"].endswith(f"/w/{data['id']}/")
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
        workspaces = response.json()
        assert len(workspaces) == 1
        assert workspaces[0]["id"] == ws1_id


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
    async def test_workspace_url_format(self, async_client: AsyncClient):
        """Test that workspace URL follows spec format."""
        response = await async_client.post(
            "/api/v1/workspaces",
            json={"name": "url-test"},
        )
        data = response.json()
        # URL should be {public_base_url}/w/{id}/
        assert data["url"] == f"http://localhost:8080/w/{data['id']}/"

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
