"""Scheduler TTL integration tests.

Tests SQL syntax and actual database operations.
Unit tests mock DB, so SQL syntax errors are not caught.
Integration tests with real PostgreSQL validate actual SQL execution.

Reference: docs/architecture_v2/ttl-manager.md
"""

import pytest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from codehub.control.coordinator.scheduler import Scheduler
from codehub.core.interfaces import InstanceController, StorageProvider
from codehub.core.models import Workspace, User


class TestSchedulerSync:
    """Scheduler._sync_to_db() integration test with real DB.

    Validates SQL syntax:
    - unnest(CAST(:ids AS text[]), CAST(:timestamps AS timestamptz[]))
    - Bulk UPDATE using PostgreSQL array functions
    """

    @pytest.fixture
    def mock_storage(self) -> MagicMock:
        """Mock StorageProvider."""
        storage = MagicMock(spec=StorageProvider)
        storage.list_all_archive_keys = AsyncMock(return_value=set())
        storage.list_volumes = AsyncMock(return_value=[])
        return storage

    @pytest.fixture
    def mock_ic(self) -> MagicMock:
        """Mock InstanceController."""
        ic = MagicMock(spec=InstanceController)
        ic.list_all = AsyncMock(return_value=[])
        return ic

    @pytest.fixture
    async def test_user(self, test_db_engine: AsyncEngine) -> User:
        """Create test user in DB."""
        async with AsyncSession(test_db_engine) as session:
            user = User(
                id="test-user-ttl-001",
                username="test_ttl",
                password_hash="hash",
                created_at=datetime.now(UTC),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    @pytest.fixture
    async def test_workspace(
        self, test_db_engine: AsyncEngine, test_user: User
    ) -> Workspace:
        """Create test workspace in DB."""
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-ttl-001",
                owner_user_id=test_user.id,
                name="TTL Test Workspace",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-ttl-001-home",
                phase="RUNNING",
                operation="NONE",
                desired_state="RUNNING",
                conditions={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                last_access_at=datetime.now(UTC) - timedelta(hours=1),  # 1시간 전
            )
            session.add(ws)
            await session.commit()
            await session.refresh(ws)
            return ws

    async def test_sync_to_db_sql_syntax(
        self, test_db_engine: AsyncEngine, test_workspace: Workspace, test_redis,
        mock_storage: MagicMock, mock_ic: MagicMock,
    ):
        """TTL-INT-001: _sync_to_db() SQL 문법 검증.

        이 테스트가 검증하는 SQL:
        UPDATE workspaces AS w
        SET last_access_at = v.ts
        FROM unnest(CAST(:ids AS text[]), CAST(:timestamps AS timestamptz[])) AS v(id, ts)
        WHERE w.id = v.id

        Note: 이전에 ::type 문법 사용 시 SQLAlchemy :param과 충돌하여 문법 오류 발생.
        CAST() 문법으로 수정하여 해결.
        """
        ws_id = test_workspace.id
        now_ts = datetime.now(UTC).timestamp()

        # Arrange: Redis에 activity 저장
        await test_redis.set(f"last_access:{ws_id}", str(now_ts))

        # Mock dependencies
        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()
        mock_activity = AsyncMock()
        mock_activity.scan_all.return_value = {ws_id: now_ts}
        mock_activity.delete = AsyncMock()
        mock_publisher = AsyncMock()

        # Act: _sync_to_db() 실행 (실제 SQL 실행)
        async with test_db_engine.connect() as conn:
            scheduler = Scheduler(
                conn,
                mock_leader,
                mock_subscriber,
                mock_activity,
                mock_publisher,
                mock_storage,
                mock_ic,
            )
            synced = await scheduler._sync_to_db()
            await conn.commit()

        # Assert: SQL이 성공적으로 실행됨 (문법 오류 없음)
        assert synced == 1, "1개 workspace가 동기화되어야 함"

        # Assert: DB에 last_access_at이 업데이트됨
        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == ws_id)
            )
            ws = result.scalar_one()
            assert ws.last_access_at is not None

    async def test_sync_to_db_empty_activities(
        self, test_db_engine: AsyncEngine, test_workspace: Workspace,
        mock_storage: MagicMock, mock_ic: MagicMock,
    ):
        """TTL-INT-002: activity가 없을 때 SQL 실행 안 함."""
        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()
        mock_activity = AsyncMock()
        mock_activity.scan_all.return_value = {}  # Empty
        mock_publisher = AsyncMock()

        async with test_db_engine.connect() as conn:
            scheduler = Scheduler(
                conn,
                mock_leader,
                mock_subscriber,
                mock_activity,
                mock_publisher,
                mock_storage,
                mock_ic,
            )
            synced = await scheduler._sync_to_db()

        assert synced == 0

    async def test_sync_to_db_multiple_workspaces(
        self, test_db_engine: AsyncEngine, test_user: User,
        mock_storage: MagicMock, mock_ic: MagicMock,
    ):
        """TTL-INT-003: 여러 workspace 동시 동기화."""
        # Create 3 workspaces
        ws_ids = []
        async with AsyncSession(test_db_engine) as session:
            for i in range(3):
                ws = Workspace(
                    id=f"test-ws-ttl-multi-{i}",
                    owner_user_id=test_user.id,
                    name=f"TTL Multi Test {i}",
                    image_ref="test:latest",
                    instance_backend="docker",
                    storage_backend="s3",
                    home_store_key=f"codehub-ws-test-ws-ttl-multi-{i}-home",
                    phase="RUNNING",
                    operation="NONE",
                    desired_state="RUNNING",
                    conditions={},
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                session.add(ws)
                ws_ids.append(ws.id)
            await session.commit()

        # Prepare activities
        now_ts = datetime.now(UTC).timestamp()
        activities = {ws_id: now_ts + i for i, ws_id in enumerate(ws_ids)}

        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()
        mock_activity = AsyncMock()
        mock_activity.scan_all.return_value = activities
        mock_activity.delete = AsyncMock()
        mock_publisher = AsyncMock()

        # Act
        async with test_db_engine.connect() as conn:
            scheduler = Scheduler(
                conn,
                mock_leader,
                mock_subscriber,
                mock_activity,
                mock_publisher,
                mock_storage,
                mock_ic,
            )
            synced = await scheduler._sync_to_db()
            await conn.commit()

        # Assert
        assert synced == 3, "3개 workspace가 동기화되어야 함"


class TestSchedulerStandbyTTL:
    """Scheduler._check_standby_ttl() integration test.

    Validates SQL syntax:
    - make_interval(secs := :standby_ttl)
    - NOW() - last_access_at comparison
    """

    @pytest.fixture
    def mock_storage(self) -> MagicMock:
        """Mock StorageProvider."""
        storage = MagicMock(spec=StorageProvider)
        storage.list_all_archive_keys = AsyncMock(return_value=set())
        storage.list_volumes = AsyncMock(return_value=[])
        return storage

    @pytest.fixture
    def mock_ic(self) -> MagicMock:
        """Mock InstanceController."""
        ic = MagicMock(spec=InstanceController)
        ic.list_all = AsyncMock(return_value=[])
        return ic

    @pytest.fixture
    async def test_user(self, test_db_engine: AsyncEngine) -> User:
        """Create test user in DB."""
        async with AsyncSession(test_db_engine) as session:
            user = User(
                id="test-user-ttl-standby",
                username="test_ttl_standby",
                password_hash="hash",
                created_at=datetime.now(UTC),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    @pytest.fixture
    async def expired_workspace(
        self, test_db_engine: AsyncEngine, test_user: User
    ) -> Workspace:
        """Create RUNNING workspace with expired last_access_at."""
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-ttl-expired",
                owner_user_id=test_user.id,
                name="Expired Workspace",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-ttl-expired-home",
                phase="RUNNING",
                operation="NONE",
                desired_state="RUNNING",
                conditions={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                # TTL 기본값 300초보다 오래 전
                last_access_at=datetime.now(UTC) - timedelta(hours=1),
            )
            session.add(ws)
            await session.commit()
            await session.refresh(ws)
            return ws

    async def test_check_standby_ttl_sql_syntax(
        self, test_db_engine: AsyncEngine, expired_workspace: Workspace,
        mock_storage: MagicMock, mock_ic: MagicMock,
    ):
        """TTL-INT-004: _check_standby_ttl() SQL 문법 검증.

        이 테스트가 검증하는 SQL:
        UPDATE workspaces
        SET desired_state = :desired_state
        WHERE phase = :phase
          AND operation = :operation
          AND deleted_at IS NULL
          AND last_access_at IS NOT NULL
          AND NOW() - last_access_at > make_interval(secs := :standby_ttl)
        RETURNING id
        """
        ws_id = expired_workspace.id

        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()
        mock_activity = AsyncMock()
        mock_activity.scan_all.return_value = {}
        mock_publisher = AsyncMock()

        # Act
        async with test_db_engine.connect() as conn:
            scheduler = Scheduler(
                conn,
                mock_leader,
                mock_subscriber,
                mock_activity,
                mock_publisher,
                mock_storage,
                mock_ic,
            )
            # Override TTL to ensure test passes (1 second)
            scheduler._standby_ttl = 1
            expired = await scheduler._check_standby_ttl()
            await conn.commit()

        # Assert: SQL 성공 + workspace가 STANDBY로 전환
        assert expired == 1

        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == ws_id)
            )
            ws = result.scalar_one()
            assert ws.desired_state == "STANDBY"

    async def test_check_standby_ttl_ignores_non_running(
        self, test_db_engine: AsyncEngine, test_user: User,
        mock_storage: MagicMock, mock_ic: MagicMock,
    ):
        """TTL-INT-005: RUNNING이 아닌 workspace는 무시."""
        # Create STANDBY workspace with old last_access_at
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-ttl-standby-skip",
                owner_user_id=test_user.id,
                name="Standby Workspace",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-ttl-standby-skip-home",
                phase="STANDBY",  # Not RUNNING
                operation="NONE",
                desired_state="STANDBY",
                conditions={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                last_access_at=datetime.now(UTC) - timedelta(hours=1),
            )
            session.add(ws)
            await session.commit()

        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()
        mock_activity = AsyncMock()
        mock_activity.scan_all.return_value = {}
        mock_publisher = AsyncMock()

        async with test_db_engine.connect() as conn:
            scheduler = Scheduler(
                conn,
                mock_leader,
                mock_subscriber,
                mock_activity,
                mock_publisher,
                mock_storage,
                mock_ic,
            )
            scheduler._standby_ttl = 1
            expired = await scheduler._check_standby_ttl()

        assert expired == 0


class TestSchedulerArchiveTTL:
    """Scheduler._check_archive_ttl() integration test.

    Validates SQL syntax:
    - make_interval(secs := :archive_ttl)
    - NOW() - phase_changed_at comparison
    """

    @pytest.fixture
    def mock_storage(self) -> MagicMock:
        """Mock StorageProvider."""
        storage = MagicMock(spec=StorageProvider)
        storage.list_all_archive_keys = AsyncMock(return_value=set())
        storage.list_volumes = AsyncMock(return_value=[])
        return storage

    @pytest.fixture
    def mock_ic(self) -> MagicMock:
        """Mock InstanceController."""
        ic = MagicMock(spec=InstanceController)
        ic.list_all = AsyncMock(return_value=[])
        return ic

    @pytest.fixture
    async def test_user(self, test_db_engine: AsyncEngine) -> User:
        """Create test user in DB."""
        async with AsyncSession(test_db_engine) as session:
            user = User(
                id="test-user-ttl-archive",
                username="test_ttl_archive",
                password_hash="hash",
                created_at=datetime.now(UTC),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    @pytest.fixture
    async def standby_workspace(
        self, test_db_engine: AsyncEngine, test_user: User
    ) -> Workspace:
        """Create STANDBY workspace with expired phase_changed_at."""
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-ttl-archive-exp",
                owner_user_id=test_user.id,
                name="Archive Candidate",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-ttl-archive-exp-home",
                phase="STANDBY",
                operation="NONE",
                desired_state="STANDBY",
                conditions={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                # archive TTL 기본값 1800초보다 오래 전
                phase_changed_at=datetime.now(UTC) - timedelta(hours=1),
            )
            session.add(ws)
            await session.commit()
            await session.refresh(ws)
            return ws

    async def test_check_archive_ttl_sql_syntax(
        self, test_db_engine: AsyncEngine, standby_workspace: Workspace,
        mock_storage: MagicMock, mock_ic: MagicMock,
    ):
        """TTL-INT-006: _check_archive_ttl() SQL 문법 검증.

        이 테스트가 검증하는 SQL:
        UPDATE workspaces
        SET desired_state = :desired_state
        WHERE phase = :phase
          AND operation = :operation
          AND deleted_at IS NULL
          AND phase_changed_at IS NOT NULL
          AND NOW() - phase_changed_at > make_interval(secs := :archive_ttl)
        RETURNING id
        """
        ws_id = standby_workspace.id

        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()
        mock_activity = AsyncMock()
        mock_activity.scan_all.return_value = {}
        mock_publisher = AsyncMock()

        async with test_db_engine.connect() as conn:
            scheduler = Scheduler(
                conn,
                mock_leader,
                mock_subscriber,
                mock_activity,
                mock_publisher,
                mock_storage,
                mock_ic,
            )
            # Override TTL to ensure test passes (1 second)
            scheduler._archive_ttl = 1
            expired = await scheduler._check_archive_ttl()
            await conn.commit()

        # Assert: SQL 성공 + workspace가 ARCHIVED로 전환
        assert expired == 1

        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == ws_id)
            )
            ws = result.scalar_one()
            assert ws.desired_state == "ARCHIVED"

    async def test_check_archive_ttl_ignores_running(
        self, test_db_engine: AsyncEngine, test_user: User,
        mock_storage: MagicMock, mock_ic: MagicMock,
    ):
        """TTL-INT-007: STANDBY가 아닌 workspace는 무시."""
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-ttl-running-skip",
                owner_user_id=test_user.id,
                name="Running Workspace",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-ttl-running-skip-home",
                phase="RUNNING",  # Not STANDBY
                operation="NONE",
                desired_state="RUNNING",
                conditions={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                phase_changed_at=datetime.now(UTC) - timedelta(hours=1),
            )
            session.add(ws)
            await session.commit()

        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()
        mock_activity = AsyncMock()
        mock_activity.scan_all.return_value = {}
        mock_publisher = AsyncMock()

        async with test_db_engine.connect() as conn:
            scheduler = Scheduler(
                conn,
                mock_leader,
                mock_subscriber,
                mock_activity,
                mock_publisher,
                mock_storage,
                mock_ic,
            )
            scheduler._archive_ttl = 1
            expired = await scheduler._check_archive_ttl()

        assert expired == 0


class TestSchedulerRunTtl:
    """Scheduler._run_ttl() full integration test."""

    @pytest.fixture
    def mock_storage(self) -> MagicMock:
        """Mock StorageProvider."""
        storage = MagicMock(spec=StorageProvider)
        storage.list_all_archive_keys = AsyncMock(return_value=set())
        storage.list_volumes = AsyncMock(return_value=[])
        return storage

    @pytest.fixture
    def mock_ic(self) -> MagicMock:
        """Mock InstanceController."""
        ic = MagicMock(spec=InstanceController)
        ic.list_all = AsyncMock(return_value=[])
        return ic

    @pytest.fixture
    async def test_user(self, test_db_engine: AsyncEngine) -> User:
        """Create test user in DB."""
        async with AsyncSession(test_db_engine) as session:
            user = User(
                id="test-user-ttl-tick",
                username="test_ttl_tick",
                password_hash="hash",
                created_at=datetime.now(UTC),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def test_run_ttl_full_cycle(
        self, test_db_engine: AsyncEngine, test_user: User,
        mock_storage: MagicMock, mock_ic: MagicMock,
    ):
        """TTL-INT-008: _run_ttl() 전체 사이클 테스트.

        1. Redis activity 동기화
        2. standby_ttl 체크
        3. archive_ttl 체크
        4. wake 호출
        """
        # Create workspaces in different states
        async with AsyncSession(test_db_engine) as session:
            # RUNNING workspace with expired last_access_at
            ws_running = Workspace(
                id="test-ws-tick-running",
                owner_user_id=test_user.id,
                name="Running",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-tick-running-home",
                phase="RUNNING",
                operation="NONE",
                desired_state="RUNNING",
                conditions={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                last_access_at=datetime.now(UTC) - timedelta(hours=1),
            )
            session.add(ws_running)

            # STANDBY workspace with expired phase_changed_at
            ws_standby = Workspace(
                id="test-ws-tick-standby",
                owner_user_id=test_user.id,
                name="Standby",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-tick-standby-home",
                phase="STANDBY",
                operation="NONE",
                desired_state="STANDBY",
                conditions={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                phase_changed_at=datetime.now(UTC) - timedelta(hours=1),
            )
            session.add(ws_standby)
            await session.commit()

        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()
        mock_activity = AsyncMock()
        mock_activity.scan_all.return_value = {}
        mock_activity.delete = AsyncMock()
        mock_publisher = AsyncMock()

        # Act
        async with test_db_engine.connect() as conn:
            scheduler = Scheduler(
                conn,
                mock_leader,
                mock_subscriber,
                mock_activity,
                mock_publisher,
                mock_storage,
                mock_ic,
            )
            scheduler._standby_ttl = 1
            scheduler._archive_ttl = 1
            await scheduler._run_ttl()

        # Assert: publish called (since workspaces expired)
        mock_publisher.publish.assert_called_once()

        # Assert: DB states updated
        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == "test-ws-tick-running")
            )
            ws = result.scalar_one()
            assert ws.desired_state == "STANDBY"

            result = await session.execute(
                select(Workspace).where(Workspace.id == "test-ws-tick-standby")
            )
            ws = result.scalar_one()
            assert ws.desired_state == "ARCHIVED"
