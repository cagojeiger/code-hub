"""Tests for proxy status pages.

Reference: docs/architecture_v2/event-listener.md (Proxy Auto-Wake)
"""

import pytest

from codehub.app.proxy.pages import (
    archived_page,
    error_page,
    limit_exceeded_page,
    starting_page,
)
from codehub.core.models import Workspace


@pytest.fixture
def workspace():
    """Create a test workspace."""
    return Workspace(
        id="ws-123",
        owner_user_id="user-1",
        name="Test Workspace",
        description="A test workspace",
        image_ref="test:latest",
        instance_backend="local-docker",
        storage_backend="minio",
        home_store_key="codehub-ws-ws-123-home",
        phase="STANDBY",
        operation="NONE",
        desired_state="STOPPED",
    )


class TestStartingPage:
    """starting_page() tests."""

    def test_returns_302_redirect(self, workspace):
        """Returns 302 redirect to static HTML."""
        response = starting_page(workspace)
        assert response.status_code == 302

    def test_redirects_to_starting_html(self, workspace):
        """Redirects to /static/proxy/starting.html."""
        response = starting_page(workspace)
        assert "/static/proxy/starting.html" in response.headers["location"]

    def test_includes_workspace_id(self, workspace):
        """Includes workspace ID in URL parameters."""
        response = starting_page(workspace)
        assert "id=ws-123" in response.headers["location"]

    def test_includes_workspace_name(self, workspace):
        """Includes workspace name in URL parameters."""
        response = starting_page(workspace)
        assert "name=Test%20Workspace" in response.headers["location"]


class TestArchivedPage:
    """archived_page() tests."""

    def test_returns_302_redirect(self, workspace):
        """Returns 302 redirect to static HTML."""
        workspace.phase = "ARCHIVED"
        response = archived_page(workspace)
        assert response.status_code == 302

    def test_redirects_to_archived_html(self, workspace):
        """Redirects to /static/proxy/archived.html."""
        workspace.phase = "ARCHIVED"
        response = archived_page(workspace)
        assert "/static/proxy/archived.html" in response.headers["location"]

    def test_includes_workspace_name(self, workspace):
        """Includes workspace name in URL parameters."""
        workspace.phase = "ARCHIVED"
        response = archived_page(workspace)
        assert "name=Test%20Workspace" in response.headers["location"]


class TestLimitExceededPage:
    """limit_exceeded_page() tests."""

    def test_returns_302_redirect(self, workspace):
        """Returns 302 redirect to static HTML."""
        running = [workspace]
        response = limit_exceeded_page(running, max_running=2)
        assert response.status_code == 302

    def test_redirects_to_limit_html(self, workspace):
        """Redirects to /static/proxy/limit.html."""
        running = [workspace]
        response = limit_exceeded_page(running, max_running=2)
        assert "/static/proxy/limit.html" in response.headers["location"]

    def test_includes_max_running(self, workspace):
        """Includes max running limit in URL parameters."""
        running = [workspace]
        response = limit_exceeded_page(running, max_running=2)
        assert "max=2" in response.headers["location"]

    def test_includes_workspace_list(self, workspace):
        """Includes running workspaces in URL parameters."""
        running = [workspace]
        response = limit_exceeded_page(running, max_running=2)
        location = response.headers["location"]
        assert "workspaces=" in location
        assert "ws-123" in location

    def test_multiple_running_workspaces(self, workspace):
        """Includes multiple workspaces in URL parameters."""
        ws2 = Workspace(
            id="ws-456",
            owner_user_id="user-1",
            name="Second Workspace",
            description="Another workspace",
            image_ref="test:latest",
            instance_backend="local-docker",
            storage_backend="minio",
            home_store_key="codehub-ws-ws-456-home",
            phase="RUNNING",
            operation="NONE",
            desired_state="RUNNING",
        )
        running = [workspace, ws2]
        response = limit_exceeded_page(running, max_running=2)
        location = response.headers["location"]
        assert "ws-123" in location
        assert "ws-456" in location


class TestErrorPage:
    """error_page() tests."""

    def test_returns_302_redirect(self, workspace):
        """Returns 302 redirect to static HTML."""
        workspace.phase = "ERROR"
        response = error_page(workspace)
        assert response.status_code == 302

    def test_redirects_to_error_html(self, workspace):
        """Redirects to /static/proxy/error.html."""
        workspace.phase = "ERROR"
        response = error_page(workspace)
        assert "/static/proxy/error.html" in response.headers["location"]

    def test_includes_phase(self, workspace):
        """Includes phase in URL parameters."""
        workspace.phase = "PENDING"
        response = error_page(workspace)
        assert "phase=PENDING" in response.headers["location"]

    def test_includes_workspace_name(self, workspace):
        """Includes workspace name in URL parameters."""
        workspace.phase = "ERROR"
        response = error_page(workspace)
        assert "name=Test%20Workspace" in response.headers["location"]

    def test_includes_error_reason(self, workspace):
        """Includes error reason in URL parameters when present."""
        workspace.phase = "ERROR"
        workspace.error_reason = "Container crashed"
        response = error_page(workspace)
        assert "error=Container%20crashed" in response.headers["location"]

    def test_no_error_param_when_no_reason(self, workspace):
        """Does not include error param when no error reason."""
        workspace.phase = "ERROR"
        workspace.error_reason = None
        response = error_page(workspace)
        assert "error=" not in response.headers["location"]
