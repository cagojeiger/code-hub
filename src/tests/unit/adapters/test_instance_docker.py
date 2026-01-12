"""Unit tests for DockerInstanceController."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from codehub.adapters.instance.docker import DockerInstanceController


class TestDockerInstanceController:
    """DockerInstanceController 테스트."""

    @pytest.fixture
    def mock_containers(self) -> AsyncMock:
        """Mock ContainerAPI."""
        mock = AsyncMock()
        mock.inspect = AsyncMock(return_value=None)
        mock.create = AsyncMock()
        mock.start = AsyncMock()
        mock.stop = AsyncMock()
        mock.remove = AsyncMock()
        return mock

    @pytest.fixture
    def mock_images(self) -> AsyncMock:
        """Mock ImageAPI."""
        mock = AsyncMock()
        mock.ensure = AsyncMock()
        return mock

    @pytest.fixture
    def controller(
        self, mock_containers: AsyncMock, mock_images: AsyncMock
    ) -> DockerInstanceController:
        """DockerInstanceController with mocks."""
        with patch("codehub.adapters.instance.docker.get_settings") as mock_settings:
            mock_settings.return_value.runtime.resource_prefix = "test-"
            mock_settings.return_value.runtime.default_image = "code-server:latest"
            mock_settings.return_value.runtime.container_port = 8080
            mock_settings.return_value.docker.coder_uid = 1000
            mock_settings.return_value.docker.coder_gid = 1000
            mock_settings.return_value.docker.network_name = "codehub"
            mock_settings.return_value.docker.dns_servers = []
            mock_settings.return_value.docker.dns_options = []
            return DockerInstanceController(
                containers=mock_containers, images=mock_images
            )

    async def test_start_existing_container(
        self, controller: DockerInstanceController, mock_containers: AsyncMock
    ):
        """기존 컨테이너가 있으면 start만 호출."""
        mock_containers.inspect.return_value = {"Id": "abc123"}

        await controller.start("ws-1", "ubuntu:22.04")

        mock_containers.inspect.assert_called_once()
        mock_containers.start.assert_called_once()
        mock_containers.create.assert_not_called()

    async def test_start_404_recreates_container(
        self,
        controller: DockerInstanceController,
        mock_containers: AsyncMock,
        mock_images: AsyncMock,
    ):
        """Start 404 시 컨테이너 삭제 후 재생성."""
        # inspect는 컨테이너 존재 반환
        mock_containers.inspect.return_value = {"Id": "abc123"}

        # start는 404 발생
        response = MagicMock()
        response.status_code = 404
        mock_containers.start.side_effect = [
            httpx.HTTPStatusError(
                "Not Found", request=MagicMock(), response=response
            ),
            None,  # 두 번째 start는 성공
        ]

        await controller.start("ws-1", "ubuntu:22.04")

        # remove → create → start 순서로 호출
        mock_containers.remove.assert_called_once()
        mock_containers.create.assert_called_once()
        mock_images.ensure.assert_called_once()
        # start는 2번 호출 (첫 번째 실패, 두 번째 성공)
        assert mock_containers.start.call_count == 2

    async def test_start_non_404_error_propagates(
        self, controller: DockerInstanceController, mock_containers: AsyncMock
    ):
        """Start에서 404 외 에러는 전파."""
        mock_containers.inspect.return_value = {"Id": "abc123"}

        response = MagicMock()
        response.status_code = 500
        mock_containers.start.side_effect = httpx.HTTPStatusError(
            "Internal Error", request=MagicMock(), response=response
        )

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await controller.start("ws-1", "ubuntu:22.04")

        assert exc_info.value.response.status_code == 500

    async def test_start_new_container(
        self,
        controller: DockerInstanceController,
        mock_containers: AsyncMock,
        mock_images: AsyncMock,
    ):
        """새 컨테이너 생성 및 시작."""
        mock_containers.inspect.return_value = None  # 컨테이너 없음

        await controller.start("ws-1", "ubuntu:22.04")

        mock_images.ensure.assert_called_once_with("ubuntu:22.04")
        mock_containers.create.assert_called_once()
        mock_containers.start.assert_called_once()

    async def test_delete_calls_stop_and_remove(
        self, controller: DockerInstanceController, mock_containers: AsyncMock
    ):
        """Delete는 stop 후 remove 호출."""
        await controller.delete("ws-1")

        mock_containers.stop.assert_called_once()
        mock_containers.remove.assert_called_once()
