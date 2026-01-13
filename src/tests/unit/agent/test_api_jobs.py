"""Unit tests for Jobs API endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from codehub_agent.api.dependencies import get_runtime, reset_runtime
from codehub_agent.main import app
from codehub_agent.runtimes.docker.job import JobResult


@pytest.fixture
def mock_runtime() -> MagicMock:
    """Create mock runtime."""
    runtime = MagicMock()
    runtime.jobs = MagicMock()
    runtime.jobs.run_archive = AsyncMock(
        return_value=JobResult(exit_code=0, logs="Archive completed")
    )
    runtime.jobs.run_restore = AsyncMock(
        return_value=JobResult(exit_code=0, logs="Restore completed")
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


class TestJobsAPI:
    """Tests for Jobs API endpoints."""

    def test_run_archive_success(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test run archive job success."""
        response = client.post(
            "/api/v1/jobs/archive",
            json={"workspace_id": "ws1", "op_id": "op123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0
        assert data["logs"] == "Archive completed"

    def test_run_archive_failure(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test run archive job with nonzero exit."""
        mock_runtime.jobs.run_archive.return_value = JobResult(
            exit_code=1, logs="Error occurred"
        )

        response = client.post(
            "/api/v1/jobs/archive",
            json={"workspace_id": "ws1", "op_id": "op123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 1
        assert data["logs"] == "Error occurred"

    def test_run_archive_error(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test run archive job handles exception."""
        mock_runtime.jobs.run_archive.side_effect = Exception("Job failed")

        response = client.post(
            "/api/v1/jobs/archive",
            json={"workspace_id": "ws1", "op_id": "op123"},
        )

        assert response.status_code == 500

    def test_run_restore_success(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test run restore job success."""
        response = client.post(
            "/api/v1/jobs/restore",
            json={"workspace_id": "ws1", "op_id": "op123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 0
        assert data["logs"] == "Restore completed"

    def test_run_restore_failure(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test run restore job with nonzero exit."""
        mock_runtime.jobs.run_restore.return_value = JobResult(
            exit_code=1, logs="Restore failed"
        )

        response = client.post(
            "/api/v1/jobs/restore",
            json={"workspace_id": "ws1", "op_id": "op123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["exit_code"] == 1

    def test_run_restore_error(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test run restore job handles exception."""
        mock_runtime.jobs.run_restore.side_effect = Exception("Restore error")

        response = client.post(
            "/api/v1/jobs/restore",
            json={"workspace_id": "ws1", "op_id": "op123"},
        )

        assert response.status_code == 500
