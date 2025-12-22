"""E2E test fixtures for code-hub.

E2E tests run against a real backend deployed via docker-compose.e2e.yml.
The backend runs in a container on the same Docker network as code-server containers.

Usage:
    docker compose -f docker-compose.e2e.yml up -d --build --wait
    E2E_BASE_URL=http://localhost:8080 uv run pytest tests/e2e -v
"""

import asyncio
import logging
import os

import docker
import pytest
import pytest_asyncio
from httpx import AsyncClient

logger = logging.getLogger(__name__)

# E2E backend URL (from docker-compose.e2e.yml)
E2E_BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8080")

# E2E-specific container prefix (must match docker-compose.e2e.yml)
E2E_CONTAINER_PREFIX = "codehub-e2e-"
E2E_NETWORK_NAME = "codehub-e2e-net"


@pytest.fixture(scope="session")
def docker_client():
    """Get Docker client for E2E tests (file operations via docker exec)."""
    return docker.from_env()


@pytest.fixture(scope="session")
def ensure_code_server_image(docker_client):
    """Ensure code-server image is available before tests."""
    image = "cagojeiger/code-server:4.107.0"
    try:
        docker_client.images.get(image)
        logger.info("Image already exists: %s", image)
    except docker.errors.ImageNotFound:
        logger.info("Pulling image: %s", image)
        docker_client.images.pull(image)
    return image


@pytest.fixture(scope="session", autouse=True)
def cleanup_e2e_containers(docker_client):
    """Clean up E2E workspace containers after all tests complete."""
    yield

    # Remove workspace containers with E2E prefix (not infra containers)
    try:
        containers = docker_client.containers.list(all=True)
        for container in containers:
            # Only remove workspace containers, not backend/postgres/redis
            if container.name.startswith(E2E_CONTAINER_PREFIX) and container.name not in [
                "codehub-e2e-backend",
                "codehub-e2e-postgres",
                "codehub-e2e-redis",
                "codehub-e2e-docker-proxy",
                "codehub-e2e-migrate",
            ]:
                logger.info("Cleaning up E2E workspace container: %s", container.name)
                try:
                    container.remove(force=True)
                except Exception as e:
                    logger.warning("Failed to remove %s: %s", container.name, e)
    except Exception as e:
        logger.warning("Error during cleanup: %s", e)


@pytest_asyncio.fixture
async def e2e_client() -> AsyncClient:
    """Create an authenticated E2E test client.

    Connects to real backend via HTTP (not ASGITransport).
    """
    async with AsyncClient(base_url=E2E_BASE_URL, timeout=30.0) as client:
        # Authenticate to get session cookie
        response = await client.post(
            "/api/v1/login",
            json={"username": "admin", "password": "admin"},
        )
        assert response.status_code == 200, f"Login failed: {response.text}"

        # Session cookie is automatically captured by httpx from response
        # No need to manually set it again

        yield client


@pytest.fixture(scope="session")
def second_user_created(docker_client):
    """Create a second test user in the database.

    Uses docker exec to insert the user directly into PostgreSQL via stdin.
    """
    import subprocess

    # Argon2 hash for "user2pass" - pre-computed
    # To generate: python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('user2pass'))"
    password_hash = "$argon2id$v=19$m=65536,t=3,p=4$CS0HQ7oHzYZ1eMA0x8qRQg$p9P3mhoxCBXXnJtzA7zuco5I5ERof2nLhSz1zObEIR0"

    user_id = "01TESTUSER2000000000000000"

    # Use stdin to avoid shell interpretation of $ in password hash
    sql = f"""
    INSERT INTO users (id, username, password_hash, created_at, failed_login_attempts)
    VALUES ('{user_id}', 'user2', '{password_hash}', NOW(), 0)
    ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash;
    """

    result = subprocess.run(
        [
            "docker", "exec", "-i", "codehub-e2e-postgres",
            "psql", "-U", "codehub", "-d", "codehub"
        ],
        input=sql,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("Failed to create second user: %s", result.stderr)
    else:
        logger.info("Second user created/updated: %s", result.stdout)

    return True


@pytest_asyncio.fixture
async def second_user_client(second_user_created) -> AsyncClient:
    """Create an authenticated client for the second user."""
    async with AsyncClient(base_url=E2E_BASE_URL, timeout=30.0) as client:
        # Authenticate as second user
        response = await client.post(
            "/api/v1/login",
            json={"username": "user2", "password": "user2pass"},
        )
        assert response.status_code == 200, f"Login failed: {response.text}"

        # Session cookie is automatically captured by httpx from response
        yield client


@pytest_asyncio.fixture
async def unauthenticated_client() -> AsyncClient:
    """Create an unauthenticated client for testing auth failures."""
    async with AsyncClient(base_url=E2E_BASE_URL, timeout=30.0) as client:
        yield client


async def wait_for_status(
    client: AsyncClient,
    workspace_id: str,
    target_status: str,
    timeout: float = 90.0,
    interval: float = 2.0,
) -> dict:
    """Poll workspace API until target status reached.

    Args:
        client: Authenticated HTTP client
        workspace_id: Workspace ID to poll
        target_status: Expected status to wait for
        timeout: Maximum wait time in seconds
        interval: Polling interval in seconds

    Returns:
        Workspace data when target status is reached

    Raises:
        TimeoutError: If status not reached within timeout
        RuntimeError: If workspace enters ERROR state unexpectedly
    """
    elapsed = 0.0
    while elapsed < timeout:
        response = await client.get(f"/api/v1/workspaces/{workspace_id}")
        if response.status_code == 200:
            data = response.json()
            current_status = data["status"]
            if current_status == target_status:
                return data
            if current_status == "ERROR" and target_status != "ERROR":
                raise RuntimeError(f"Workspace entered ERROR state: {data}")
        await asyncio.sleep(interval)
        elapsed += interval
    raise TimeoutError(
        f"Workspace {workspace_id} did not reach {target_status} in {timeout}s"
    )


@pytest_asyncio.fixture
async def workspace_fixture(e2e_client: AsyncClient) -> dict:
    """Create a workspace (not started)."""
    response = await e2e_client.post(
        "/api/v1/workspaces",
        json={"name": "e2e-test-workspace"},
    )
    assert response.status_code == 201
    return response.json()


@pytest_asyncio.fixture
async def running_workspace(
    e2e_client: AsyncClient,
    workspace_fixture: dict,
    ensure_code_server_image,
) -> dict:
    """Create and start a workspace, waiting for RUNNING status.

    This fixture creates a workspace, starts it, and waits for the
    code-server container to become healthy. Returns the workspace data.
    """
    workspace_id = workspace_fixture["id"]

    # Start the workspace
    response = await e2e_client.post(f"/api/v1/workspaces/{workspace_id}:start")
    assert response.status_code == 200
    assert response.json()["status"] == "PROVISIONING"

    # Wait for RUNNING status
    workspace_data = await wait_for_status(e2e_client, workspace_id, "RUNNING")

    yield workspace_data

    # Cleanup: delete workspace (container cleanup handled by session fixture)
    try:
        await e2e_client.post(f"/api/v1/workspaces/{workspace_id}:stop")
        await wait_for_status(e2e_client, workspace_id, "STOPPED", timeout=30.0)
        await e2e_client.delete(f"/api/v1/workspaces/{workspace_id}")
    except Exception as e:
        logger.warning("Cleanup failed for workspace %s: %s", workspace_id, e)
