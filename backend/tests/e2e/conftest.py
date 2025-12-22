"""E2E test fixtures for code-hub.

Extends the main conftest.py with E2E-specific fixtures for testing
real code-server containers and full stack integration.
"""

import asyncio
import logging

import docker
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from app.api.v1.dependencies import (
    get_instance_controller,
    get_storage_provider,
    get_workspace_service,
)
from app.core.config import get_settings
from app.core.security import hash_password
from app.db import User
from app.db.session import get_async_session
from app.main import app
from app.services.instance.local_docker import LocalDockerInstanceController
from app.services.storage.local_dir import LocalDirStorageProvider
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)

# E2E-specific container prefix to avoid conflicts with unit tests
E2E_CONTAINER_PREFIX = "codehub-e2e-"
E2E_NETWORK_NAME = "codehub-e2e-net"


@pytest.fixture(autouse=True)
def setup_e2e_env(setup_test_env, monkeypatch):
    """Set up E2E-specific environment variables.

    This fixture depends on setup_test_env from the main conftest.py
    to ensure directory creation happens first, then overrides
    E2E-specific settings (container prefix, network name).
    """
    # Override with E2E-specific container prefix and network
    monkeypatch.setenv("CODEHUB_WORKSPACE__CONTAINER_PREFIX", E2E_CONTAINER_PREFIX)
    monkeypatch.setenv("CODEHUB_WORKSPACE__NETWORK_NAME", E2E_NETWORK_NAME)
    # Shorter healthcheck timeout for faster tests
    monkeypatch.setenv("CODEHUB_WORKSPACE__HEALTHCHECK__TIMEOUT", "60s")

    # Clear settings cache to pick up new env vars
    get_settings.cache_clear()

    # Clear dependency caches for E2E-specific overrides
    get_instance_controller.cache_clear()
    get_storage_provider.cache_clear()
    get_workspace_service.cache_clear()

    yield

    # Clear caches after test
    get_settings.cache_clear()
    get_instance_controller.cache_clear()
    get_storage_provider.cache_clear()
    get_workspace_service.cache_clear()


@pytest.fixture(scope="session")
def docker_client():
    """Get Docker client for E2E tests."""
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
    """Clean up E2E Docker containers after all tests complete."""
    yield

    # Remove containers with E2E prefix
    containers = docker_client.containers.list(all=True)
    for container in containers:
        if container.name.startswith(E2E_CONTAINER_PREFIX):
            logger.info("Cleaning up E2E container: %s", container.name)
            try:
                container.remove(force=True)
            except Exception as e:
                logger.warning("Failed to remove %s: %s", container.name, e)

    # Remove E2E network if exists
    try:
        network = docker_client.networks.get(E2E_NETWORK_NAME)
        logger.info("Cleaning up E2E network: %s", E2E_NETWORK_NAME)
        network.remove()
    except docker.errors.NotFound:
        pass
    except Exception as e:
        logger.warning("Failed to remove network %s: %s", E2E_NETWORK_NAME, e)


def _create_e2e_instance_controller():
    """Create instance controller with expose_ports for E2E tests."""
    settings = get_settings()
    return LocalDockerInstanceController(
        container_prefix=settings.workspace.container_prefix,
        network_name=settings.workspace.network_name,
        expose_ports=True,  # Enable host port binding for E2E tests
    )


def _create_e2e_storage_provider():
    """Create storage provider for E2E tests."""
    settings = get_settings()
    return LocalDirStorageProvider(
        control_plane_base_dir=settings.home_store.control_plane_base_dir,
        workspace_base_dir=settings.home_store.workspace_base_dir,  # type: ignore
    )


def _create_e2e_workspace_service():
    """Create workspace service with E2E-configured dependencies."""
    return WorkspaceService(
        storage=_create_e2e_storage_provider(),
        instance=_create_e2e_instance_controller(),
    )


@pytest_asyncio.fixture
async def second_user(db_session: AsyncSession) -> User:
    """Create a second test user for ownership tests."""
    user = User(
        username="user2",
        password_hash=hash_password("user2pass"),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def e2e_client(db_engine, redis_client, test_user) -> AsyncClient:
    """Create an authenticated E2E test client with expose_ports enabled."""

    async def override_get_async_session():
        async_session = sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as session:
            yield session

    # Override dependencies with E2E-configured versions
    app.dependency_overrides[get_async_session] = override_get_async_session
    app.dependency_overrides[get_instance_controller] = _create_e2e_instance_controller
    app.dependency_overrides[get_storage_provider] = _create_e2e_storage_provider
    app.dependency_overrides[get_workspace_service] = _create_e2e_workspace_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Authenticate to get session cookie
        response = await client.post(
            "/api/v1/login",
            json={"username": "admin", "password": "admin"},
        )
        assert response.status_code == 200, f"Login failed: {response.text}"

        # Clear any existing cookies and set fresh session
        client.cookies.clear()
        session_cookie = response.cookies.get("session")
        if session_cookie:
            client.cookies.set("session", session_cookie)

        yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def second_user_client(db_engine, redis_client, second_user) -> AsyncClient:
    """Create an authenticated client for the second user."""

    async def override_get_async_session():
        async_session = sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as session:
            yield session

    # Override dependencies with E2E-configured versions
    app.dependency_overrides[get_async_session] = override_get_async_session
    app.dependency_overrides[get_instance_controller] = _create_e2e_instance_controller
    app.dependency_overrides[get_storage_provider] = _create_e2e_storage_provider
    app.dependency_overrides[get_workspace_service] = _create_e2e_workspace_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Authenticate as second user
        response = await client.post(
            "/api/v1/login",
            json={"username": "user2", "password": "user2pass"},
        )
        assert response.status_code == 200, f"Login failed: {response.text}"

        # Clear any existing cookies and set fresh session
        client.cookies.clear()
        session_cookie = response.cookies.get("session")
        if session_cookie:
            client.cookies.set("session", session_cookie)

        yield client

    app.dependency_overrides.clear()


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

    # Cleanup: just delete workspace (skip stop to avoid background task issues)
    # The session-level cleanup_e2e_containers will handle Docker cleanup
    try:
        await e2e_client.delete(f"/api/v1/workspaces/{workspace_id}")
    except Exception as e:
        logger.warning("Cleanup failed for workspace %s: %s", workspace_id, e)
