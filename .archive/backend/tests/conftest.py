"""Shared test fixtures for code-hub tests."""

import logging

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.core.config import get_settings
from app.core.redis import close_redis, init_redis
from app.core.security import hash_password
from app.db import User, init_db
from app.db.session import close_db, get_async_session
from app.main import app

logger = logging.getLogger(__name__)

# Test-specific container prefix for easy cleanup
TEST_CONTAINER_PREFIX = "codehub-test-"
TEST_NETWORK_NAME = "codehub-test-net"


@pytest.fixture(scope="session", autouse=True)
def cleanup_docker_containers():
    """Clean up Docker containers with test prefix after all tests complete.

    This fixture runs once per test session and cleans up any containers
    that were created with the test prefix.
    """
    yield

    # Only cleanup if docker is available
    try:
        import docker

        client = docker.from_env()

        # Remove containers with test prefix
        containers = client.containers.list(all=True)
        for container in containers:
            if container.name.startswith(TEST_CONTAINER_PREFIX):
                logger.info("Cleaning up test container: %s", container.name)
                try:
                    container.remove(force=True)
                except Exception as e:
                    logger.warning("Failed to remove %s: %s", container.name, e)

        # Remove test network if exists
        try:
            network = client.networks.get(TEST_NETWORK_NAME)
            logger.info("Cleaning up test network: %s", TEST_NETWORK_NAME)
            network.remove()
        except docker.errors.NotFound:
            pass
        except Exception as e:
            logger.warning("Failed to remove network %s: %s", TEST_NETWORK_NAME, e)

    except ImportError:
        logger.debug("docker package not available, skipping cleanup")
    except Exception as e:
        logger.warning("Docker cleanup failed: %s", e)


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    """Set up test environment variables."""
    test_homes_dir = tmp_path / "homes"
    test_homes_dir.mkdir()

    monkeypatch.setenv("CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR", str(test_homes_dir))
    monkeypatch.setenv(
        "CODEHUB_HOME_STORE__CONTROL_PLANE_BASE_DIR", str(test_homes_dir)
    )
    # Set test-specific container prefix and network
    monkeypatch.setenv("CODEHUB_WORKSPACE__CONTAINER_PREFIX", TEST_CONTAINER_PREFIX)
    monkeypatch.setenv("CODEHUB_WORKSPACE__NETWORK_NAME", TEST_NETWORK_NAME)

    # Clear settings cache to pick up new env vars
    get_settings.cache_clear()

    yield

    # Clear cache after test
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def postgres_container():
    """Start PostgreSQL container for all tests."""
    with PostgresContainer("postgres:17") as postgres:
        yield postgres


@pytest.fixture(scope="session")
def redis_container():
    """Start Redis container for all tests."""
    with RedisContainer("redis:7") as redis:
        yield redis


@pytest_asyncio.fixture
async def db_engine(postgres_container):
    """Create database engine connected to test PostgreSQL."""
    from sqlmodel import SQLModel

    url = postgres_container.get_connection_url()
    async_url = url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    engine = await init_db(async_url, echo=False, create_tables=False)

    # Drop and recreate all tables for test isolation
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine
    await close_db()


def _get_redis_url(redis_container) -> str:
    """Build Redis URL from container."""
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}"


@pytest_asyncio.fixture
async def redis_client(redis_container, monkeypatch):
    """Initialize Redis connection for tests."""
    redis_url = _get_redis_url(redis_container)
    monkeypatch.setenv("CODEHUB_REDIS__URL", redis_url)
    get_settings.cache_clear()

    await init_redis()
    yield
    await close_redis()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Create a database session for testing."""
    async_session = sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    """Create a test user."""
    user = User(
        username="admin",
        password_hash=hash_password("admin"),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def async_client(db_engine, redis_client, test_user) -> AsyncClient:
    """Create an authenticated async test client with DB and Redis initialized."""

    async def override_get_async_session():
        async_session = sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_get_async_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Authenticate to get session cookie
        response = await client.post(
            "/api/v1/login",
            json={"username": "admin", "password": "admin"},
        )
        assert response.status_code == 200, f"Login failed: {response.text}"

        # Extract session cookie and set it for all subsequent requests
        session_cookie = response.cookies.get("session")
        if session_cookie:
            client.cookies.set("session", session_cookie)

        yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def unauthenticated_client(db_engine, redis_client, test_user) -> AsyncClient:
    """Create an async test client without authentication.

    Note: test_user is required to ensure user exists in DB for auth tests.
    Note: redis_client is required to ensure Redis is initialized.
    """

    async def override_get_async_session():
        async_session = sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_get_async_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
