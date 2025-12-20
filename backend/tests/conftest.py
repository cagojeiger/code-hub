"""Shared test fixtures for code-hub tests."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.security import hash_password
from app.db import User, init_db
from app.db.session import close_db, get_async_session
from app.main import app


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    """Set up test environment variables."""
    test_homes_dir = tmp_path / "homes"
    test_homes_dir.mkdir()

    monkeypatch.setenv("CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR", str(test_homes_dir))
    monkeypatch.setenv(
        "CODEHUB_HOME_STORE__CONTROL_PLANE_BASE_DIR", str(test_homes_dir)
    )

    # Clear settings cache to pick up new env vars
    get_settings.cache_clear()

    yield

    # Clear cache after test
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def db_engine():
    """Create an in-memory database for testing."""
    engine = await init_db("sqlite+aiosqlite:///:memory:", echo=False)
    yield engine
    await close_db()


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
async def async_client(db_engine, test_user) -> AsyncClient:
    """Create an async test client with database initialized and authenticated."""

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
async def unauthenticated_client(db_engine, test_user) -> AsyncClient:
    """Create an async test client without authentication.

    Note: test_user is required to ensure user exists in DB for auth tests.
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
