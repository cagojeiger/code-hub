"""Leader election implementations using PostgreSQL advisory lock."""

import asyncio
import hashlib
import logging

import psycopg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection as SAConnection

from codehub.core.interfaces.leader import LeaderElection

logger = logging.getLogger(__name__)


def _compute_lock_id(lock_key: str) -> int:
    """Compute 64-bit lock ID from string key.

    Uses SHA-256 truncated to 63 bits for PostgreSQL signed bigint range.
    PostgreSQL bigint: -9223372036854775808 to 9223372036854775807
    We use only positive values (0 to 2^63-1) for simplicity.
    """
    h = hashlib.sha256(lock_key.encode()).digest()
    return int.from_bytes(h[:8], "big") & 0x7FFFFFFFFFFFFFFF


class SQLAlchemyLeaderElection(LeaderElection):
    """Leader election using SQLAlchemy AsyncConnection.

    Used by Coordinators (Observer, WC, GC, TTL).
    """

    DEFAULT_TIMEOUT: float = 5.0

    def __init__(self, conn: SAConnection, lock_key: str) -> None:
        """Initialize leader election.

        Args:
            conn: SQLAlchemy AsyncConnection.
            lock_key: Unique key for the advisory lock (e.g., coordinator type).
        """
        self._conn = conn
        self._lock_key = str(lock_key)
        self._lock_id = _compute_lock_id(self._lock_key)
        self._is_leader = False
        self._was_leader = False
        self._lock = asyncio.Lock()  # Future-proofing for multi-task scenarios

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    @property
    def lock_id(self) -> int:
        return self._lock_id

    async def try_acquire(self, timeout: float | None = None) -> bool:
        """Try to acquire leadership (non-blocking)."""
        # Skip DB call if already leader (prevents re-entrant lock accumulation)
        if self._is_leader:
            return True

        async with self._lock:
            # Double-check after acquiring lock
            if self._is_leader:
                return True

            timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT

            try:
                async with asyncio.timeout(timeout):
                    result = await self._conn.execute(
                        text("SELECT pg_try_advisory_lock(:lock_id)"),
                        {"lock_id": self._lock_id},
                    )
                    row = result.fetchone()
                    acquired = row[0] if row else False
            except TimeoutError:
                logger.warning("Leadership acquire timeout (lock=%s)", self._lock_key)
                acquired = False
            except Exception as e:
                logger.warning("Leadership acquire error (lock=%s): %s", self._lock_key, e)
                acquired = False

            self._is_leader = acquired

            if self._is_leader and not self._was_leader:
                logger.info("Acquired leadership (lock=%s, id=%d)", self._lock_key, self._lock_id)
            elif not self._is_leader and self._was_leader:
                logger.warning("Lost leadership (lock=%s)", self._lock_key)

            self._was_leader = self._is_leader
            return self._is_leader

    async def release(self, timeout: float | None = None) -> None:
        """Release leadership lock."""
        if not self._is_leader:
            return

        async with self._lock:
            if not self._is_leader:
                return

            timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT

            try:
                async with asyncio.timeout(timeout):
                    result = await self._conn.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": self._lock_id},
                    )
                    row = result.fetchone()
                    released = row[0] if row else False

                    if not released:
                        logger.warning(
                            "Lock was not held during release (lock=%s)", self._lock_key
                        )
            except TimeoutError:
                logger.warning("Leadership release timeout (lock=%s)", self._lock_key)
            except Exception as e:
                logger.warning("Leadership release error (lock=%s): %s", self._lock_key, e)

            self._is_leader = False
            logger.info("Released leadership (lock=%s)", self._lock_key)

    async def verify_holding(self, timeout: float | None = None) -> bool:
        """Verify that we still hold the advisory lock by querying pg_locks."""
        if not self._is_leader:
            return False

        async with self._lock:
            if not self._is_leader:
                return False

            timeout = timeout if timeout is not None else 2.0

            try:
                async with asyncio.timeout(timeout):
                    # Reassemble classid/objid to bigint in PostgreSQL to avoid asyncpg oid encoding issues
                    # Reference: https://www.postgresql.org/docs/17/view-pg-locks.html
                    result = await self._conn.execute(
                        text("""
                            SELECT EXISTS(
                                SELECT 1 FROM pg_locks
                                WHERE locktype = 'advisory'
                                  AND (classid::bigint << 32) | (objid::bigint & x'FFFFFFFF'::bigint) = :lock_id
                                  AND objsubid = 1
                                  AND pid = pg_backend_pid()
                                  AND granted = true
                            )
                        """),
                        {"lock_id": self._lock_id},
                    )
                    row = result.fetchone()
                    holding = row[0] if row else False
            except TimeoutError:
                logger.warning("Leadership verify timeout (lock=%s)", self._lock_key)
                self._is_leader = False
                return False
            except Exception as e:
                logger.warning("Leadership verify error (lock=%s): %s", self._lock_key, e)
                self._is_leader = False
                return False

            if not holding:
                logger.warning("Leadership lost (lock=%s) - detected via pg_locks", self._lock_key)
                self._is_leader = False
                self._was_leader = False

            return holding


class PsycopgLeaderElection(LeaderElection):
    """Leader election using psycopg3 AsyncConnection.

    Used by EventListener (requires psycopg3 for LISTEN/NOTIFY).
    """

    DEFAULT_TIMEOUT: float = 5.0

    def __init__(self, conn: psycopg.AsyncConnection, lock_key: str) -> None:
        """Initialize leader election.

        Args:
            conn: psycopg3 AsyncConnection.
            lock_key: Unique key for the advisory lock.
        """
        self._conn = conn
        self._lock_key = str(lock_key)
        self._lock_id = _compute_lock_id(self._lock_key)
        self._is_leader = False
        self._was_leader = False
        self._lock = asyncio.Lock()  # Future-proofing for multi-task scenarios

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    @property
    def lock_id(self) -> int:
        return self._lock_id

    async def try_acquire(self, timeout: float | None = None) -> bool:
        """Try to acquire leadership (non-blocking)."""
        if self._is_leader:
            return True

        async with self._lock:
            # Double-check after acquiring lock
            if self._is_leader:
                return True

            timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT

            try:
                async with asyncio.timeout(timeout):
                    result = await self._conn.execute(
                        "SELECT pg_try_advisory_lock(%s)", (self._lock_id,)
                    )
                    row = await result.fetchone()
                    acquired = row[0] if row else False
            except TimeoutError:
                logger.warning("Leadership acquire timeout (lock=%s)", self._lock_key)
                acquired = False
            except Exception as e:
                logger.warning("Leadership acquire error (lock=%s): %s", self._lock_key, e)
                acquired = False

            self._is_leader = acquired

            if self._is_leader and not self._was_leader:
                logger.info("Acquired leadership (lock=%s, id=%d)", self._lock_key, self._lock_id)
            elif not self._is_leader and self._was_leader:
                logger.warning("Lost leadership (lock=%s)", self._lock_key)

            self._was_leader = self._is_leader
            return self._is_leader

    async def release(self, timeout: float | None = None) -> None:
        """Release leadership lock."""
        if not self._is_leader:
            return

        async with self._lock:
            if not self._is_leader:
                return

            timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT

            try:
                async with asyncio.timeout(timeout):
                    result = await self._conn.execute(
                        "SELECT pg_advisory_unlock(%s)", (self._lock_id,)
                    )
                    row = await result.fetchone()
                    released = row[0] if row else False

                    if not released:
                        logger.warning(
                            "Lock was not held during release (lock=%s)", self._lock_key
                        )
            except TimeoutError:
                logger.warning("Leadership release timeout (lock=%s)", self._lock_key)
            except Exception as e:
                logger.warning("Leadership release error (lock=%s): %s", self._lock_key, e)

            self._is_leader = False
            logger.info("Released leadership (lock=%s)", self._lock_key)

    async def verify_holding(self, timeout: float | None = None) -> bool:
        """Verify that we still hold the advisory lock by querying pg_locks."""
        if not self._is_leader:
            return False

        async with self._lock:
            if not self._is_leader:
                return False

            timeout = timeout if timeout is not None else 2.0

            try:
                async with asyncio.timeout(timeout):
                    # Reassemble classid/objid to bigint in PostgreSQL to avoid psycopg oid encoding issues
                    # Reference: https://www.postgresql.org/docs/17/view-pg-locks.html
                    result = await self._conn.execute(
                        """
                        SELECT EXISTS(
                            SELECT 1 FROM pg_locks
                            WHERE locktype = 'advisory'
                              AND (classid::bigint << 32) | (objid::bigint & x'FFFFFFFF'::bigint) = %s
                              AND objsubid = 1
                              AND pid = pg_backend_pid()
                              AND granted = true
                        )
                        """,
                        (self._lock_id,),
                    )
                    row = await result.fetchone()
                    holding = row[0] if row else False
            except TimeoutError:
                logger.warning("Leadership verify timeout (lock=%s)", self._lock_key)
                self._is_leader = False
                return False
            except Exception as e:
                logger.warning("Leadership verify error (lock=%s): %s", self._lock_key, e)
                self._is_leader = False
                return False

            if not holding:
                logger.warning("Leadership lost (lock=%s) - detected via pg_locks", self._lock_key)
                self._is_leader = False
                self._was_leader = False

            return holding
