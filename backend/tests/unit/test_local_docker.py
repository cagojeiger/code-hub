"""Unit tests for LocalDockerInstanceController.

Tests idempotency of container operations using mocked Docker client.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from docker.errors import NotFound

from app.services.instance import LocalDockerInstanceController
from app.services.instance.local_docker import (
    CODE_SERVER_PORT,
    CONTAINER_PREFIX,
    HOME_MOUNT_PATH,
    NETWORK_NAME,
)


def run_async(coro):
    """Helper to run async functions in sync tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def mock_docker_client():
    """Create a mock Docker client."""
    with patch("app.services.instance.local_docker.docker") as mock_docker:
        mock_client = MagicMock()
        mock_docker.DockerClient.return_value = mock_client
        mock_docker.from_env.return_value = mock_client
        yield mock_client


@pytest.fixture
def controller(mock_docker_client) -> LocalDockerInstanceController:
    """Create a LocalDockerInstanceController with mocked Docker client."""
    return LocalDockerInstanceController()


class TestBackendName:

    def test_returns_local_docker(
        self, controller: LocalDockerInstanceController
    ) -> None:
        assert controller.backend_name == "local-docker"


class TestContainerNaming:

    def test_container_name_format(
        self, controller: LocalDockerInstanceController
    ) -> None:
        workspace_id = "01HXYZ123"
        expected = f"{CONTAINER_PREFIX}{workspace_id}"
        assert controller._container_name(workspace_id) == expected

    def test_container_prefix(self) -> None:
        assert CONTAINER_PREFIX == "codehub-ws-"


class TestStartWorkspace:

    def test_creates_container_when_not_exists(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-create-test"
        image_ref = "codercom/code-server:latest"
        home_mount = "/host/path/to/home"

        # Container doesn't exist
        mock_docker_client.containers.get.side_effect = NotFound("not found")
        # Network exists
        mock_docker_client.networks.get.return_value = MagicMock()

        run_async(controller.start_workspace(workspace_id, image_ref, home_mount))

        mock_docker_client.containers.run.assert_called_once()
        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert call_kwargs["name"] == f"{CONTAINER_PREFIX}{workspace_id}"
        assert call_kwargs["detach"] is True
        assert call_kwargs["network"] == NETWORK_NAME

    def test_starts_existing_stopped_container(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-start-stopped"
        image_ref = "codercom/code-server:latest"
        home_mount = "/host/path/to/home"

        # Container exists but is stopped
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_docker_client.containers.get.return_value = mock_container

        run_async(controller.start_workspace(workspace_id, image_ref, home_mount))

        mock_container.start.assert_called_once()
        mock_docker_client.containers.run.assert_not_called()

    def test_idempotent_when_already_running(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-already-running"
        image_ref = "codercom/code-server:latest"
        home_mount = "/host/path/to/home"

        # Container exists and is running
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker_client.containers.get.return_value = mock_container

        run_async(controller.start_workspace(workspace_id, image_ref, home_mount))

        mock_container.start.assert_not_called()
        mock_docker_client.containers.run.assert_not_called()

    def test_creates_network_if_not_exists(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-network-create"
        image_ref = "codercom/code-server:latest"
        home_mount = "/host/path/to/home"

        # Container doesn't exist
        mock_docker_client.containers.get.side_effect = NotFound("not found")
        # Network doesn't exist
        mock_docker_client.networks.get.side_effect = NotFound("not found")

        run_async(controller.start_workspace(workspace_id, image_ref, home_mount))

        mock_docker_client.networks.create.assert_called_once_with(
            NETWORK_NAME, driver="bridge"
        )

    def test_mounts_home_directory(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-mount-test"
        image_ref = "codercom/code-server:latest"
        home_mount = "/host/users/alice/home"

        mock_docker_client.containers.get.side_effect = NotFound("not found")
        mock_docker_client.networks.get.return_value = MagicMock()

        run_async(controller.start_workspace(workspace_id, image_ref, home_mount))

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert call_kwargs["volumes"] == {
            home_mount: {"bind": HOME_MOUNT_PATH, "mode": "rw"}
        }

    def test_uses_internal_network_without_port_binding(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        """Verify container uses Docker network without host port binding.

        M5 proxy connects via internal Docker network, not exposed ports.
        """
        workspace_id = "ws-no-port"
        image_ref = "codercom/code-server:latest"
        home_mount = "/host/path/to/home"

        mock_docker_client.containers.get.side_effect = NotFound("not found")
        mock_docker_client.networks.get.return_value = MagicMock()

        run_async(controller.start_workspace(workspace_id, image_ref, home_mount))

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        # No port binding - proxy connects via internal network
        assert "ports" not in call_kwargs
        assert call_kwargs["network"] == "codehub-net"


class TestStopWorkspace:

    def test_stops_running_container(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-stop-running"

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker_client.containers.get.return_value = mock_container

        run_async(controller.stop_workspace(workspace_id))

        mock_container.stop.assert_called_once()

    def test_idempotent_when_already_stopped(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-already-stopped"

        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_docker_client.containers.get.return_value = mock_container

        run_async(controller.stop_workspace(workspace_id))

        mock_container.stop.assert_not_called()

    def test_idempotent_when_not_exists(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-not-exists"

        mock_docker_client.containers.get.side_effect = NotFound("not found")

        # Should not raise
        run_async(controller.stop_workspace(workspace_id))


class TestDeleteWorkspace:

    def test_removes_container(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-delete"

        mock_container = MagicMock()
        mock_docker_client.containers.get.return_value = mock_container

        run_async(controller.delete_workspace(workspace_id))

        mock_container.remove.assert_called_once_with(force=True)

    def test_idempotent_when_not_exists(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-delete-not-exists"

        mock_docker_client.containers.get.side_effect = NotFound("not found")

        # Should not raise
        run_async(controller.delete_workspace(workspace_id))


class TestResolveUpstream:

    def test_returns_container_name_and_internal_port(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        """Verify upstream uses container name for Docker network."""
        workspace_id = "ws-upstream"

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker_client.containers.get.return_value = mock_container

        result = run_async(controller.resolve_upstream(workspace_id))

        # Uses container name as host (for internal network)
        assert result.host == f"{CONTAINER_PREFIX}{workspace_id}"
        # Uses internal port (not host-mapped port)
        assert result.port == CODE_SERVER_PORT

    def test_raises_when_container_not_found(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-upstream-not-found"

        mock_docker_client.containers.get.side_effect = NotFound("not found")

        with pytest.raises(ValueError, match="Container not found"):
            run_async(controller.resolve_upstream(workspace_id))

    def test_raises_when_container_not_running(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-stopped"

        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_docker_client.containers.get.return_value = mock_container

        with pytest.raises(ValueError, match=r"Container not running"):
            run_async(controller.resolve_upstream(workspace_id))


class TestGetStatus:

    def test_container_not_exists(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-status-not-exists"

        mock_docker_client.containers.get.side_effect = NotFound("not found")

        status = run_async(controller.get_status(workspace_id))

        assert status.exists is False
        assert status.running is False
        assert status.healthy is False
        assert status.port is None

    def test_container_stopped(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-status-stopped"

        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.ports = {}
        mock_docker_client.containers.get.return_value = mock_container

        status = run_async(controller.get_status(workspace_id))

        assert status.exists is True
        assert status.running is False
        assert status.healthy is False
        assert status.port is None

    def test_container_running(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-status-running"

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker_client.containers.get.return_value = mock_container

        status = run_async(controller.get_status(workspace_id))

        assert status.exists is True
        assert status.running is True
        assert status.healthy is True
        # Port is None since we use internal Docker network, not host port binding
        assert status.port is None


class TestIdempotencyFullCycle:
    """Test full idempotency cycle: start -> stop -> delete."""

    def test_full_cycle(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        workspace_id = "ws-full-cycle"
        image_ref = "codercom/code-server:latest"
        home_mount = "/host/path/to/home"

        # 1. Start (create) - container doesn't exist
        mock_docker_client.containers.get.side_effect = NotFound("not found")
        mock_docker_client.networks.get.return_value = MagicMock()

        run_async(controller.start_workspace(workspace_id, image_ref, home_mount))
        mock_docker_client.containers.run.assert_called_once()

        # 2. Start again (idempotent) - container exists and running
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_docker_client.containers.get.side_effect = None
        mock_docker_client.containers.get.return_value = mock_container

        run_async(controller.start_workspace(workspace_id, image_ref, home_mount))
        # Should not create again
        assert mock_docker_client.containers.run.call_count == 1
        mock_container.start.assert_not_called()

        # 3. Stop - container running
        run_async(controller.stop_workspace(workspace_id))
        mock_container.stop.assert_called_once()

        # 4. Stop again (idempotent) - container stopped
        mock_container.status = "exited"
        mock_container.stop.reset_mock()

        run_async(controller.stop_workspace(workspace_id))
        mock_container.stop.assert_not_called()

        # 5. Delete - container exists
        run_async(controller.delete_workspace(workspace_id))
        mock_container.remove.assert_called_once_with(force=True)

        # 6. Delete again (idempotent) - container doesn't exist
        mock_docker_client.containers.get.side_effect = NotFound("not found")
        mock_container.remove.reset_mock()

        run_async(controller.delete_workspace(workspace_id))
        mock_container.remove.assert_not_called()

    def test_start_after_stop(
        self, controller: LocalDockerInstanceController, mock_docker_client
    ) -> None:
        """Test that stopped container can be started again."""
        workspace_id = "ws-restart"
        image_ref = "codercom/code-server:latest"
        home_mount = "/host/path/to/home"

        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_docker_client.containers.get.return_value = mock_container

        run_async(controller.start_workspace(workspace_id, image_ref, home_mount))

        mock_container.start.assert_called_once()
        mock_docker_client.containers.run.assert_not_called()
