"""Coordinator infrastructure - leader election, base class."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from enum import StrEnum

import psycopg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection as SAConnection

from codehub.infra.redis import NotifySubscriber, WakeTarget

logger = logging.getLogger(__name__)

# Type alias for both connection types
ConnType = SAConnection | psycopg.AsyncConnection


class CoordinatorType(StrEnum):
    """Coordinator types for leader election."""

    OBSERVER = "observer"
    WC = "wc"
    GC = "gc"
    TTL = "ttl"


class LeaderElection:
    """Leader election using PostgreSQL session-level advisory lock.

    Supports both SQLAlchemy and psycopg3 connections.
    """

    def __init__(self, conn: ConnType, lock_key: str) -> None:
        """Initialize leader election.

        Args:
            conn: SQLAlchemy AsyncConnection or psycopg3 AsyncConnection.
            lock_key: Unique key for the advisory lock (e.g., coordinator type).
        """
        self._conn = conn
        self._lock_key = str(lock_key)  # Convert enum to str if needed
        self._is_leader = False
        self._was_leader = False

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    async def try_acquire(self) -> bool:
        """Try to acquire leadership (non-blocking). Logs only on state change."""
        query = f"SELECT pg_try_advisory_lock(hashtext('{self._lock_key}'))"

        if isinstance(self._conn, psycopg.AsyncConnection):
            # psycopg3 path (async fetchone)
            result = await self._conn.execute(query)
            row = await result.fetchone()
        else:
            # SQLAlchemy path (sync fetchone)
            result = await self._conn.execute(text(query))
            row = result.fetchone()

        self._is_leader = row[0] if row else False

        if self._is_leader and not self._was_leader:
            logger.info("Acquired leadership (lock=%s)", self._lock_key)
        elif not self._is_leader and self._was_leader:
            logger.warning("Lost leadership (lock=%s)", self._lock_key)

        self._was_leader = self._is_leader
        return self._is_leader

    async def release(self) -> None:
        """Release leadership lock."""
        if not self._is_leader:
            return

        query = f"SELECT pg_advisory_unlock(hashtext('{self._lock_key}'))"

        if isinstance(self._conn, psycopg.AsyncConnection):
            await self._conn.execute(query)
        else:
            await self._conn.execute(text(query))

        self._is_leader = False
        logger.info("Released leadership (lock=%s)", self._lock_key)


class CoordinatorBase(ABC):
    """Base class for Coordinators with leader election and polling.

    ## DB Connection Strategy (ADR-012)

    Coordinator uses the same connection for both Advisory Lock and DB transactions.
    This ensures atomic failure: if connection drops, both lock and transaction fail together.

    In tick(), use self._conn directly (NOT AsyncSession):

        async def tick(self) -> None:
            # Use connection directly for queries
            result = await self._conn.execute(select(Workspace))

            # Commit at connection level after writes
            await self._conn.execute(update_stmt)
            await self._conn.commit()

    WARNING: Do NOT use AsyncSession(bind=self._conn)!
    - AsyncSession.commit() only commits at session level
    - Connection stays in "idle in transaction" state
    - This causes lock conflicts between Coordinators (Observer ↔ WC)

    DO NOT use get_session() in Coordinator - it gets connection from pool,
    which may differ from the Advisory Lock connection, causing Zombie Lock risk.
    """

    IDLE_INTERVAL: float = 15.0
    ACTIVE_INTERVAL: float = 1.0
    MIN_INTERVAL: float = 1.0
    LEADER_RETRY_INTERVAL: float = 5.0
    VERIFY_INTERVAL: float = 60.0
    ACTIVE_DURATION: float = 30.0

    COORDINATOR_TYPE: CoordinatorType
    WAKE_TARGET: WakeTarget | None = None  # Set to receive wake messages

    def __init__(
        self,
        conn: SAConnection,
        leader: LeaderElection,
        notify: NotifySubscriber,
    ) -> None:
        self._conn = conn  # Shared with Advisory Lock - see ADR-012
        self._leader = leader
        self._notify = notify
        self._running = False
        self._subscribed = False
        self._active_until = time.time() + self.ACTIVE_DURATION
        self._last_verify = 0.0
        self._last_tick = 0.0

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def is_active(self) -> bool:
        return time.time() < self._active_until

    def accelerate(self) -> None:
        self._active_until = time.time() + self.ACTIVE_DURATION
        logger.info("[%s] Accelerating for %.0fs", self.name, self.ACTIVE_DURATION)

    def _get_interval(self) -> float:
        return self.ACTIVE_INTERVAL if self.is_active else self.IDLE_INTERVAL

    async def _safe_rollback(self) -> None:
        """Rollback transaction, logging any errors."""
        try:
            await self._conn.rollback()
        except Exception as e:
            logger.warning("[%s] Rollback failed: %s", self.name, e)

    @abstractmethod
    async def tick(self) -> None:
        """Execute one reconciliation cycle."""
        pass

    async def run(self) -> None:
        """Main coordinator loop."""
        self._running = True
        logger.info("[%s] Starting coordinator", self.name)

        try:
            while self._running:
                if not await self._ensure_leadership():
                    continue
                await self._ensure_subscribed()
                logger.info("[%s] run() loop: before _throttle()", self.name)
                await self._throttle()
                logger.info("[%s] run() loop: before _execute_tick()", self.name)
                if not await self._execute_tick():
                    break
                await self._wait_for_notify(self._get_interval())
        finally:
            await self._cleanup()

    async def _ensure_leadership(self) -> bool:
        """Verify/acquire leadership. Returns False if not leader."""
        now = time.time()
        if now - self._last_verify <= self.VERIFY_INTERVAL and self._leader.is_leader:
            return True

        try:
            acquired = await self._leader.try_acquire()
        except Exception as e:
            logger.warning("[%s] Error acquiring leadership: %s", self.name, e)
            await self._safe_rollback()
            acquired = False

        if not acquired:
            await self._release_subscription()
            await asyncio.sleep(self.LEADER_RETRY_INTERVAL)
            return False

        self._last_verify = now
        return True

    async def _ensure_subscribed(self) -> None:
        """Subscribe to wake stream if not already subscribed."""
        if not self._subscribed and self.WAKE_TARGET:
            await self._notify.subscribe(self.WAKE_TARGET)
            self._subscribed = True

    async def _throttle(self) -> None:
        """Ensure minimum interval between ticks."""
        elapsed = time.time() - self._last_tick
        if elapsed < self.MIN_INTERVAL:
            await asyncio.sleep(self.MIN_INTERVAL - elapsed)

    async def _execute_tick(self) -> bool:
        """Execute tick. Returns False if cancelled."""
        logger.info("[%s] _execute_tick() entering tick()", self.name)
        try:
            await self.tick()
            self._last_tick = time.time()
            return True
        except asyncio.CancelledError:
            return False
        except Exception as e:
            logger.exception("[%s] Error in tick: %s", self.name, e)
            await self._safe_rollback()
            self._last_tick = time.time()
            return True

    async def _wait_for_notify(self, interval: float) -> None:
        """Wait for interval or until notification received."""
        if not self.WAKE_TARGET:
            await asyncio.sleep(interval)
            return

        try:
            msg = await self._notify.get_message(timeout=interval)
            if msg:
                self.accelerate()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[%s] Error checking notify: %s", self.name, e)
            await asyncio.sleep(interval)

    async def _release_subscription(self) -> None:
        """Release notify subscription."""
        if self._subscribed and self.WAKE_TARGET:
            try:
                await self._notify.unsubscribe()
            except Exception as e:
                logger.warning("[%s] Error unsubscribing: %s", self.name, e)
            self._subscribed = False

    async def _cleanup(self) -> None:
        """Cleanup on shutdown."""
        logger.info("[%s] Cleaning up", self.name)
        await self._release_subscription()
        try:
            await self._leader.release()
        except Exception as e:
            logger.warning("[%s] Error releasing leadership: %s", self.name, e)
