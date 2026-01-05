"""Integration tests for SQLAlchemyLeaderElection with real PostgreSQL."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codehub.infra.pg_leader import SQLAlchemyLeaderElection, _compute_lock_id


# Override autouse fixtures from conftest.py (not needed for SQLAlchemyLeaderElection tests)
@pytest.fixture(autouse=True)
def setup_storage():
    """Override the autouse storage fixture - SQLAlchemyLeaderElection doesn't need S3."""
    yield


@pytest.fixture(autouse=True)
def reset_docker_client():
    """Override the autouse docker fixture - SQLAlchemyLeaderElection doesn't need Docker."""
    yield


class TestExclusiveLock:
    """Two connections compete for the same lock."""

    @pytest.mark.asyncio
    async def test_exclusive_lock_between_connections(
        self, test_db_engine: AsyncEngine
    ) -> None:
        """Only one connection can hold the lock at a time."""
        lock_key = "test_exclusive"

        async with test_db_engine.connect() as conn1:
            async with test_db_engine.connect() as conn2:
                leader1 = SQLAlchemyLeaderElection(conn1, lock_key)
                leader2 = SQLAlchemyLeaderElection(conn2, lock_key)

                # conn1 acquires first
                result1 = await leader1.try_acquire()
                assert result1 is True
                assert leader1.is_leader is True

                # conn2 fails to acquire (lock already held)
                result2 = await leader2.try_acquire()
                assert result2 is False
                assert leader2.is_leader is False

                # conn1 releases
                await leader1.release()
                assert leader1.is_leader is False

                # Now conn2 can acquire
                result2 = await leader2.try_acquire()
                assert result2 is True
                assert leader2.is_leader is True

    @pytest.mark.asyncio
    async def test_different_lock_keys_independent(
        self, test_db_engine: AsyncEngine
    ) -> None:
        """Different lock keys don't block each other."""
        async with test_db_engine.connect() as conn1:
            async with test_db_engine.connect() as conn2:
                leader1 = SQLAlchemyLeaderElection(conn1, "lock_a")
                leader2 = SQLAlchemyLeaderElection(conn2, "lock_b")

                # Both can acquire their own locks
                result1 = await leader1.try_acquire()
                result2 = await leader2.try_acquire()

                assert result1 is True
                assert result2 is True
                assert leader1.is_leader is True
                assert leader2.is_leader is True


class TestReentrantLockNoAccumulation:
    """P0: Calling try_acquire multiple times doesn't accumulate locks."""

    @pytest.mark.asyncio
    async def test_multiple_acquire_single_release(
        self, test_db_engine: AsyncEngine
    ) -> None:
        """Multiple try_acquire calls, single release is enough."""
        lock_key = "test_reentrant"

        async with test_db_engine.connect() as conn1:
            async with test_db_engine.connect() as conn2:
                leader1 = SQLAlchemyLeaderElection(conn1, lock_key)
                leader2 = SQLAlchemyLeaderElection(conn2, lock_key)

                # Acquire multiple times (simulates periodic verification)
                for _ in range(5):
                    result = await leader1.try_acquire()
                    assert result is True

                # Single release
                await leader1.release()

                # conn2 should be able to acquire immediately
                # If locks accumulated, conn1 would still hold it
                result2 = await leader2.try_acquire()
                assert result2 is True, (
                    "Lock accumulated! Multiple try_acquire created multiple locks."
                )

    @pytest.mark.asyncio
    async def test_lock_count_query(self, test_db_engine: AsyncEngine) -> None:
        """Verify pg_locks shows exactly 1 lock after multiple acquires."""
        lock_key = "test_count"
        lock_id = _compute_lock_id(lock_key)
        objid = lock_id & 0xFFFFFFFF
        classid = (lock_id >> 32) & 0xFFFFFFFF

        async with test_db_engine.connect() as conn:
            leader = SQLAlchemyLeaderElection(conn, lock_key)

            # Acquire multiple times
            for _ in range(5):
                await leader.try_acquire()

            # Count locks in pg_locks
            result = await conn.execute(
                text("""
                    SELECT COUNT(*) FROM pg_locks
                    WHERE locktype = 'advisory'
                      AND objid = :objid
                      AND classid = :classid
                      AND pid = pg_backend_pid()
                """),
                {"objid": objid, "classid": classid},
            )
            row = result.fetchone()
            count = row[0] if row else 0

            # Should be exactly 1, not 5
            assert count == 1, f"Expected 1 lock, found {count} (lock accumulation bug)"


class TestVerifyHolding:
    """P6: verify_holding() detects lock loss."""

    @pytest.mark.asyncio
    async def test_verify_holding_returns_true(
        self, test_db_engine: AsyncEngine
    ) -> None:
        """verify_holding returns True when lock is held."""
        async with test_db_engine.connect() as conn:
            leader = SQLAlchemyLeaderElection(conn, "test_verify")

            await leader.try_acquire()
            assert leader.is_leader is True

            result = await leader.verify_holding()
            assert result is True
            assert leader.is_leader is True

    @pytest.mark.asyncio
    async def test_verify_holding_detects_lost_lock(
        self, test_db_engine: AsyncEngine
    ) -> None:
        """verify_holding detects when lock is lost externally."""
        lock_key = "test_verify_lost"
        lock_id = _compute_lock_id(lock_key)

        async with test_db_engine.connect() as conn:
            leader = SQLAlchemyLeaderElection(conn, lock_key)

            await leader.try_acquire()
            assert leader.is_leader is True

            # Force unlock via direct SQL (simulates connection issue)
            await conn.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": lock_id},
            )

            # verify_holding should detect the loss
            result = await leader.verify_holding()
            assert result is False
            assert leader.is_leader is False

    @pytest.mark.asyncio
    async def test_connection_close_releases_lock(
        self, test_db_engine: AsyncEngine
    ) -> None:
        """Session-level advisory lock is released when connection closes."""
        lock_key = "test_conn_close"

        # First connection acquires lock
        async with test_db_engine.connect() as conn1:
            leader1 = SQLAlchemyLeaderElection(conn1, lock_key)
            await leader1.try_acquire()
            assert leader1.is_leader is True

        # conn1 closed, lock should be released

        # Second connection should be able to acquire
        async with test_db_engine.connect() as conn2:
            leader2 = SQLAlchemyLeaderElection(conn2, lock_key)
            result = await leader2.try_acquire()
            assert result is True, "Lock not released after connection close"


class TestLockIdConsistency:
    """Lock ID is consistent across connections."""

    @pytest.mark.asyncio
    async def test_same_lock_id_blocks(self, test_db_engine: AsyncEngine) -> None:
        """Same lock_key produces same lock_id, causing blocking."""
        lock_key = "coordinator:wc"

        async with test_db_engine.connect() as conn1:
            async with test_db_engine.connect() as conn2:
                leader1 = SQLAlchemyLeaderElection(conn1, lock_key)
                leader2 = SQLAlchemyLeaderElection(conn2, lock_key)

                # Same lock_id
                assert leader1.lock_id == leader2.lock_id

                await leader1.try_acquire()
                result2 = await leader2.try_acquire()

                assert result2 is False
