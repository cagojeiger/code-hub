"""E2E tests for MVP Criteria 3: Home directory persistence.

Criteria 3: STOP -> START preserves Home directory contents.
"""

import pytest
from httpx import AsyncClient

from .conftest import E2E_CONTAINER_PREFIX, wait_for_status


@pytest.mark.e2e
class TestHomePersistence:
    """MVP Criteria 3: Home directory persists after STOP -> START."""

    @pytest.mark.asyncio
    async def test_file_persists_after_stop_start(
        self,
        e2e_client: AsyncClient,
        workspace_fixture: dict,
        docker_client,
        ensure_code_server_image,
    ):
        """Verify files created in /home/coder survive stop and restart.

        Steps:
        1. Start workspace, wait for RUNNING
        2. Create a test file via docker exec
        3. Stop workspace, wait for STOPPED
        4. Start workspace again, wait for RUNNING
        5. Verify the file still exists
        """
        workspace_id = workspace_fixture["id"]
        container_name = f"{E2E_CONTAINER_PREFIX}{workspace_id}"
        test_content = "e2e-persistence-test-content"
        test_file = "/home/coder/persistence-test.txt"

        # Step 1: Start workspace
        response = await e2e_client.post(f"/api/v1/workspaces/{workspace_id}:start")
        assert response.status_code == 200
        await wait_for_status(e2e_client, workspace_id, "RUNNING")

        # Step 2: Create test file via docker exec
        container = docker_client.containers.get(container_name)
        exit_code, output = container.exec_run(
            f"sh -c 'echo \"{test_content}\" > {test_file}'"
        )
        assert exit_code == 0, f"Failed to create test file: {output.decode()}"

        # Verify file was created
        exit_code, output = container.exec_run(f"cat {test_file}")
        assert exit_code == 0
        assert test_content in output.decode()

        # Step 3: Stop workspace
        response = await e2e_client.post(f"/api/v1/workspaces/{workspace_id}:stop")
        assert response.status_code == 200
        await wait_for_status(e2e_client, workspace_id, "STOPPED", timeout=30.0)

        # Step 4: Start workspace again
        response = await e2e_client.post(f"/api/v1/workspaces/{workspace_id}:start")
        assert response.status_code == 200
        await wait_for_status(e2e_client, workspace_id, "RUNNING")

        # Step 5: Verify file still exists
        container = docker_client.containers.get(container_name)
        exit_code, output = container.exec_run(f"cat {test_file}")
        assert exit_code == 0, f"Test file not found after restart: {output.decode()}"
        assert test_content in output.decode(), (
            f"File content mismatch: expected '{test_content}', "
            f"got '{output.decode()}'"
        )

        # Cleanup
        await e2e_client.post(f"/api/v1/workspaces/{workspace_id}:stop")
        await wait_for_status(e2e_client, workspace_id, "STOPPED", timeout=30.0)
        await e2e_client.delete(f"/api/v1/workspaces/{workspace_id}")

    @pytest.mark.asyncio
    async def test_directory_structure_preserved(
        self,
        e2e_client: AsyncClient,
        workspace_fixture: dict,
        docker_client,
        ensure_code_server_image,
    ):
        """Verify nested directory structure survives stop and restart."""
        workspace_id = workspace_fixture["id"]
        container_name = f"{E2E_CONTAINER_PREFIX}{workspace_id}"
        test_dir = "/home/coder/projects/myapp/src"
        test_file = f"{test_dir}/main.py"
        # Avoid shell quote escaping issues - use simple content without quotes
        test_content = "hello-world-content"

        # Start workspace
        response = await e2e_client.post(f"/api/v1/workspaces/{workspace_id}:start")
        assert response.status_code == 200
        await wait_for_status(e2e_client, workspace_id, "RUNNING")

        # Create nested directory and file
        container = docker_client.containers.get(container_name)
        exit_code, output = container.exec_run(f"mkdir -p {test_dir}")
        assert exit_code == 0, f"Failed to create directory: {output.decode()}"
        exit_code, output = container.exec_run(
            f"sh -c 'echo \"{test_content}\" > {test_file}'"
        )
        assert exit_code == 0, f"Failed to create file: {output.decode()}"

        # Stop workspace
        response = await e2e_client.post(f"/api/v1/workspaces/{workspace_id}:stop")
        assert response.status_code == 200
        await wait_for_status(e2e_client, workspace_id, "STOPPED", timeout=30.0)

        # Start workspace again
        response = await e2e_client.post(f"/api/v1/workspaces/{workspace_id}:start")
        assert response.status_code == 200
        await wait_for_status(e2e_client, workspace_id, "RUNNING")

        # Verify directory structure and file
        container = docker_client.containers.get(container_name)
        exit_code, output = container.exec_run(f"cat {test_file}")
        assert exit_code == 0, f"Nested file not found: {output.decode()}"
        assert test_content in output.decode()

        # Cleanup
        await e2e_client.post(f"/api/v1/workspaces/{workspace_id}:stop")
        await wait_for_status(e2e_client, workspace_id, "STOPPED", timeout=30.0)
        await e2e_client.delete(f"/api/v1/workspaces/{workspace_id}")
