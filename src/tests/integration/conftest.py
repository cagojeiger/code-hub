"""Integration test fixtures."""

import os
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from codehub.app.config import get_settings

# Test bucket name - separate from production
TEST_BUCKET = "codehub-archives-test"

# PostgreSQL server URL (without database name)
# Use POSTGRES_HOST env var to override hostname for local testing
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_SERVER = f"postgresql+asyncpg://codehub:codehub@{POSTGRES_HOST}:5432"

# Redis host (for local testing)
REDIS_HOST = os.getenv("REDIS_HOST", "redis")


@pytest.fixture(scope="function")
def test_db_name() -> str:
    """Unique test database name per test function."""
    return f"codehub_test_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture(scope="function")
async def test_db_engine(test_db_name: str) -> AsyncGenerator[AsyncEngine, None]:
    """Create temporary test database and engine.

    1. Connect to 'postgres' DB to create test DB
    2. Create engine for test DB
    3. Create tables
    4. Cleanup: Drop test database after tests
    """
    # Import models to register them with SQLModel.metadata
    from codehub.core.models import User, Workspace  # noqa: F401

    # 1. Connect to 'postgres' DB to create test DB
    admin_engine = create_async_engine(f"{POSTGRES_SERVER}/postgres", isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(text(f"CREATE DATABASE {test_db_name}"))
    await admin_engine.dispose()

    # 2. Create engine for test DB
    test_engine = create_async_engine(
        f"{POSTGRES_SERVER}/{test_db_name}",
        echo=False,
    )

    # 3. Create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield test_engine

    # 4. Cleanup: Drop test database
    await test_engine.dispose()
    admin_engine = create_async_engine(f"{POSTGRES_SERVER}/postgres", isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        # Terminate existing connections
        await conn.execute(text(f"""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = '{test_db_name}' AND pid <> pg_backend_pid()
        """))
        await conn.execute(text(f"DROP DATABASE IF EXISTS {test_db_name}"))
    await admin_engine.dispose()


@pytest.fixture(autouse=True)
async def reset_settings_cache():
    """Reset settings cache before each test."""
    get_settings.cache_clear()
    os.environ["S3_BUCKET"] = TEST_BUCKET
    yield
    get_settings.cache_clear()


# Test resource prefix - clearly identifies test resources
TEST_PREFIX = "test-int-"


@pytest.fixture
def test_prefix() -> str:
    """Unique prefix for test resources to avoid conflicts.

    Format: test-int-{uuid8}- (e.g., test-int-a1b2c3d4-)
    """
    return f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}-"


@pytest_asyncio.fixture
async def test_redis():
    """Redis client for integration tests.

    Uses the same Redis instance as the application.
    Set REDIS_HOST env var for local testing.
    """
    import redis.asyncio as redis

    client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
    yield client
    await client.aclose()
