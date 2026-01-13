"""Unit tests for Instance API endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from codehub_agent.api.dependencies import get_runtime, reset_runtime
from codehub_agent.main import app
from codehub_agent.runtimes.docker.instance import InstanceStatus, UpstreamInfo


@pytest.fixture
def mock_runtime() -> MagicMock:
    """Create mock runtime."""
    runtime = MagicMock()
    runtime.instances = MagicMock()
    runtime.instances.list_all = AsyncMock(return_value=[])
    runtime.instances.start = AsyncMock()
    runtime.instances.delete = AsyncMock()
    runtime.instances.get_status = AsyncMock(
        return_value=InstanceStatus(
            exists=True,
            running=True,
            healthy=True,
            reason="Running",
            message="Up 5 minutes",
        )
    )
    runtime.instances.get_upstream = AsyncMock(
        return_value=UpstreamInfo(hostname="codehub-ws1", port=8080)
    )
    return runtime


@pytest.fixture
def client(mock_runtime: MagicMock) -> TestClient:
    """Create test client with mocked runtime."""
    # Use FastAPI's dependency_overrides
    app.dependency_overrides[get_runtime] = lambda: mock_runtime

    with TestClient(app) as client:
        yield client

    # Cleanup
    app.dependency_overrides.clear()
    reset_runtime()


class TestInstanceAPI:
    """Tests for Instance API endpoints."""

    def test_list_instances_empty(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test list instances returns empty list."""
        response = client.get("/api/v1/instances")

        assert response.status_code == 200
        data = response.json()
        assert data["instances"] == []

    def test_list_instances_with_data(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test list instances returns instance data."""
        mock_runtime.instances.list_all.return_value = [
            {
                "workspace_id": "ws1",
                "running": True,
                "reason": "Running",
                "message": "Up 5 minutes",
            }
        ]

        response = client.get("/api/v1/instances")

        assert response.status_code == 200
        data = response.json()
        assert len(data["instances"]) == 1
        assert data["instances"][0]["workspace_id"] == "ws1"

    def test_start_instance(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test start instance."""
        response = client.post(
            "/api/v1/instances/ws1/start",
            json={"image_ref": "image:latest"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["workspace_id"] == "ws1"

    def test_start_instance_error(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test start instance handles error."""
        mock_runtime.instances.start.side_effect = Exception("Start failed")

        response = client.post(
            "/api/v1/instances/ws1/start",
            json={"image_ref": "image:latest"},
        )

        assert response.status_code == 500

    def test_delete_instance(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test delete instance."""
        response = client.delete("/api/v1/instances/ws1")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["workspace_id"] == "ws1"

    def test_delete_instance_error(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test delete instance handles error."""
        mock_runtime.instances.delete.side_effect = Exception("Delete failed")

        response = client.delete("/api/v1/instances/ws1")

        assert response.status_code == 500

    def test_get_instance_status(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test get instance status."""
        response = client.get("/api/v1/instances/ws1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is True
        assert data["running"] is True
        assert data["healthy"] is True
        assert data["reason"] == "Running"

    def test_get_instance_status_not_found(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test get instance status when not found."""
        mock_runtime.instances.get_status.return_value = InstanceStatus(
            exists=False,
            running=False,
            healthy=False,
            reason="NotFound",
            message="Container not found",
        )

        response = client.get("/api/v1/instances/ws1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False
        assert data["reason"] == "NotFound"

    def test_get_upstream(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test get upstream."""
        response = client.get("/api/v1/instances/ws1/upstream")

        assert response.status_code == 200
        data = response.json()
        assert data["hostname"] == "codehub-ws1"
        assert data["port"] == 8080
        assert data["url"] == "http://codehub-ws1:8080"
