"""Unit tests for database module.

Tests cover:
- SQLite WAL mode initialization
- Model creation and relationships
- Test user seeding
- Password hashing and verification
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from app.db import (
    TEST_USER_PASSWORD,
    TEST_USER_USERNAME,
    Session,
    User,
    Workspace,
    WorkspaceStatus,
    hash_password,
    init_db,
    seed_test_user,
    verify_password,
)
from app.db.session import close_db, get_engine


@pytest_asyncio.fixture
async def db_engine():
    """Create an in-memory database for testing."""
    engine = await init_db("sqlite+aiosqlite:///:memory:", echo=False)
    yield engine
    await close_db()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Create a database session for testing."""
    async_session = sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session


class TestDatabaseInitialization:
    """Tests for database initialization."""

    @pytest.mark.asyncio
    async def test_init_db_creates_tables(self, db_engine):
        """Test that init_db creates all tables."""
        async with db_engine.connect() as conn:
            # Check users table exists
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
            )
            assert result.scalar() == "users"

            # Check sessions table exists
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
            )
            assert result.scalar() == "sessions"

            # Check workspaces table exists
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='workspaces'")
            )
            assert result.scalar() == "workspaces"

    @pytest.mark.asyncio
    async def test_get_engine_returns_engine(self, db_engine):
        """Test that get_engine returns the initialized engine."""
        engine = get_engine()
        assert engine is not None
        assert engine == db_engine


class TestWALMode:
    """Tests for SQLite WAL mode (file-based DB only)."""

    @pytest.mark.asyncio
    async def test_wal_mode_enabled_for_file_db(self, tmp_path):
        """Test that WAL mode is enabled for file-based databases."""
        db_path = tmp_path / "test.db"
        engine = await init_db(f"sqlite+aiosqlite:///{db_path}", echo=False)

        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            journal_mode = result.scalar()
            assert journal_mode == "wal"

        await close_db()


class TestUserModel:
    """Tests for User model."""

    @pytest.mark.asyncio
    async def test_create_user(self, db_session: AsyncSession):
        """Test creating a user."""
        user = User(
            username="testuser",
            password_hash=hash_password("testpassword"),
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        assert user.id is not None
        assert len(user.id) == 26  # ULID length
        assert user.username == "testuser"
        assert user.created_at is not None

    @pytest.mark.asyncio
    async def test_user_username_unique(self, db_session: AsyncSession):
        """Test that username must be unique."""
        user1 = User(username="sameuser", password_hash="hash1")
        db_session.add(user1)
        await db_session.commit()

        user2 = User(username="sameuser", password_hash="hash2")
        db_session.add(user2)

        with pytest.raises(Exception):  # IntegrityError
            await db_session.commit()


class TestSessionModel:
    """Tests for Session model."""

    @pytest.mark.asyncio
    async def test_create_session(self, db_session: AsyncSession):
        """Test creating a session."""
        # Create user first
        user = User(username="sessionuser", password_hash="hash")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Create session
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        session = Session(user_id=user.id, expires_at=expires_at)
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        assert session.id is not None
        assert session.user_id == user.id
        assert session.created_at is not None
        # SQLite doesn't preserve timezone, so compare without tzinfo
        assert session.expires_at.replace(tzinfo=None) == expires_at.replace(tzinfo=None)
        assert session.revoked_at is None

    @pytest.mark.asyncio
    async def test_session_user_relationship(self, db_session: AsyncSession):
        """Test session-user relationship."""
        user = User(username="reluser", password_hash="hash")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        session = Session(
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        db_session.add(session)
        await db_session.commit()
        await db_session.refresh(session)

        # Load relationship
        result = await db_session.execute(
            select(Session).where(Session.id == session.id)
        )
        loaded_session = result.scalar_one()
        await db_session.refresh(loaded_session, ["user"])

        assert loaded_session.user.id == user.id
        assert loaded_session.user.username == "reluser"


class TestWorkspaceModel:
    """Tests for Workspace model."""

    @pytest.mark.asyncio
    async def test_create_workspace(self, db_session: AsyncSession):
        """Test creating a workspace."""
        user = User(username="wsowner", password_hash="hash")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        workspace = Workspace(
            owner_user_id=user.id,
            name="my-workspace",
            description="Test workspace",
            image_ref="codercom/code-server:latest",
            home_store_key=f"users/{user.id}/workspaces/test/home",
        )
        db_session.add(workspace)
        await db_session.commit()
        await db_session.refresh(workspace)

        assert workspace.id is not None
        assert workspace.name == "my-workspace"
        assert workspace.status == WorkspaceStatus.CREATED
        assert workspace.instance_backend == "local-docker"
        assert workspace.storage_backend == "local-dir"
        assert workspace.home_ctx is None
        assert workspace.deleted_at is None

    @pytest.mark.asyncio
    async def test_workspace_status_enum(self, db_session: AsyncSession):
        """Test workspace status values."""
        user = User(username="statusowner", password_hash="hash")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        # Test all status values
        for status in WorkspaceStatus:
            workspace = Workspace(
                owner_user_id=user.id,
                name=f"ws-{status.value}",
                image_ref="test:latest",
                home_store_key=f"users/{user.id}/workspaces/{status.value}/home",
                status=status,
            )
            db_session.add(workspace)

        await db_session.commit()

        # Verify all were created
        result = await db_session.execute(select(Workspace))
        workspaces = result.scalars().all()
        assert len(workspaces) == len(WorkspaceStatus)

    @pytest.mark.asyncio
    async def test_workspace_owner_relationship(self, db_session: AsyncSession):
        """Test workspace-owner relationship."""
        user = User(username="ownerrel", password_hash="hash")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        workspace = Workspace(
            owner_user_id=user.id,
            name="rel-workspace",
            image_ref="test:latest",
            home_store_key="test/home",
        )
        db_session.add(workspace)
        await db_session.commit()
        await db_session.refresh(workspace)

        # Load relationship
        result = await db_session.execute(
            select(Workspace).where(Workspace.id == workspace.id)
        )
        loaded_ws = result.scalar_one()
        await db_session.refresh(loaded_ws, ["owner"])

        assert loaded_ws.owner.id == user.id
        assert loaded_ws.owner.username == "ownerrel"


class TestPasswordHashing:
    """Tests for password hashing utilities."""

    def test_hash_password(self):
        """Test password hashing."""
        password = "mypassword123"
        hashed = hash_password(password)

        assert hashed != password
        assert hashed.startswith("$argon2")

    def test_verify_password_correct(self):
        """Test verifying correct password."""
        password = "correctpassword"
        hashed = hash_password(password)

        assert verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        """Test verifying incorrect password."""
        password = "correctpassword"
        hashed = hash_password(password)

        assert verify_password("wrongpassword", hashed) is False

    def test_hash_password_different_each_time(self):
        """Test that hashing same password produces different hashes (salted)."""
        password = "samepassword"
        hash1 = hash_password(password)
        hash2 = hash_password(password)

        assert hash1 != hash2


class TestSeedTestUser:
    """Tests for test user seeding."""

    @pytest.mark.asyncio
    async def test_seed_test_user_creates_user(self, db_session: AsyncSession):
        """Test that seed_test_user creates the test user."""
        user = await seed_test_user(db_session)

        assert user is not None
        assert user.username == TEST_USER_USERNAME
        assert verify_password(TEST_USER_PASSWORD, user.password_hash)

    @pytest.mark.asyncio
    async def test_seed_test_user_idempotent(self, db_session: AsyncSession):
        """Test that seed_test_user is idempotent."""
        user1 = await seed_test_user(db_session)
        user2 = await seed_test_user(db_session)

        assert user1.id == user2.id

        # Verify only one user exists
        result = await db_session.execute(
            select(User).where(User.username == TEST_USER_USERNAME)
        )
        users = result.scalars().all()
        assert len(users) == 1
