"""Coordinator infrastructure - leader election, notify, base class."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from enum import StrEnum

import redis.asyncio as redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)


class Channel(StrEnum):
    """Notify channel names."""

    WC_WAKE = "wc:wake"
    GC_WAKE = "gc:wake"


class CoordinatorType(StrEnum):
    """Coordinator types for leader election."""

    WC = "wc"
    GC = "gc"
    TTL = "ttl"


class NotifyPublisher:
    """Publishes notifications to wake up Coordinators."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    async def publish(self, channel: Channel, message: str = "wake") -> int:
        count = await self._client.publish(channel, message)
        logger.debug("Published notify to %s (receivers=%d)", channel, count)
        return count

    async def wake_wc(self) -> int:
        return await self.publish(Channel.WC_WAKE)

    async def wake_gc(self) -> int:
        return await self.publish(Channel.GC_WAKE)


class NotifySubscriber:
    """Subscribes to notify channels for Coordinator wakeup."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client
        self._pubsub: redis.client.PubSub | None = None

    async def subscribe(self, *channels: Channel) -> None:
        self._pubsub = self._client.pubsub()
        await self._pubsub.subscribe(*[str(c) for c in channels])
        logger.info("Subscribed to channels: %s", [str(c) for c in channels])

    async def unsubscribe(self) -> None:
        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.aclose()
            self._pubsub = None

    async def get_message(self, timeout: float = 0.0) -> str | None:
        if not self._pubsub:
            return None

        message = await self._pubsub.get_message(
            ignore_subscribe_messages=True, timeout=timeout
        )
        if message and message["type"] == "message":
            return message["channel"]
        return None


class LeaderElection:
    """Leader election using PostgreSQL session-level advisory lock."""

    def __init__(self, conn: AsyncConnection, coordinator_type: CoordinatorType) -> None:
        self._conn = conn
        self._coordinator_type = coordinator_type
        self._is_leader = False
        self._was_leader = False

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    async def try_acquire(self) -> bool:
        """Try to acquire leadership (non-blocking). Logs only on state change."""
        result = await self._conn.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:lock_name))"),
            {"lock_name": str(self._coordinator_type)},
        )
        row = result.fetchone()
        self._is_leader = row[0] if row else False

        if self._is_leader and not self._was_leader:
            logger.info("Acquired leadership (coordinator=%s)", self._coordinator_type)
        elif not self._is_leader and self._was_leader:
            logger.warning("Lost leadership (coordinator=%s)", self._coordinator_type)

        self._was_leader = self._is_leader
        return self._is_leader

    async def release(self) -> None:
        if self._is_leader:
            await self._conn.execute(
                text("SELECT pg_advisory_unlock(hashtext(:lock_name))"),
                {"lock_name": str(self._coordinator_type)},
            )
            self._is_leader = False
            logger.info("Released leadership (coordinator=%s)", self._coordinator_type)


class CoordinatorBase(ABC):
    """Base class for Coordinators with leader election and polling.

    ## DB Connection Strategy (ADR-012)

    Coordinator uses the same connection for both Advisory Lock and DB transactions.
    This ensures atomic failure: if connection drops, both lock and transaction fail together.

    In tick(), always use AsyncSession(bind=self._conn):

        async def tick(self) -> None:
            # IMPORTANT: Use self._conn to share connection with Advisory Lock
            # - bind=Engine -> gets new connection from pool (different from lock)
            # - bind=Connection -> uses exact same connection (shares with lock)
            # See: ADR-012, sqlalchemy/orm/session.py:1179-1188
            async with AsyncSession(bind=self._conn) as session:
                ...

    DO NOT use get_session() in Coordinator - it gets connection from pool,
    which may differ from the Advisory Lock connection, causing Zombie Lock risk.
    """

    IDLE_INTERVAL: float = 15.0
    ACTIVE_INTERVAL: float = 2.0
    MIN_INTERVAL: float = 1.0
    LEADER_RETRY_INTERVAL: float = 5.0
    VERIFY_INTERVAL: float = 60.0
    ACTIVE_DURATION: float = 30.0

    COORDINATOR_TYPE: CoordinatorType
    CHANNELS: list[Channel] = []

    def __init__(self, conn: AsyncConnection, redis_client: redis.Redis) -> None:
        self._conn = conn  # Shared with Advisory Lock - see ADR-012
        self._leader = LeaderElection(conn, self.COORDINATOR_TYPE)
        self._notify = NotifySubscriber(redis_client)
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
        logger.debug("[%s] Accelerating for %.0fs", self.name, self.ACTIVE_DURATION)

    def _get_interval(self) -> float:
        return self.ACTIVE_INTERVAL if self.is_active else self.IDLE_INTERVAL

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
                await self._throttle()
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
            acquired = False

        if not acquired:
            await self._release_subscription()
            await asyncio.sleep(self.LEADER_RETRY_INTERVAL)
            return False

        self._last_verify = now
        return True

    async def _ensure_subscribed(self) -> None:
        """Subscribe to notify channels if not already subscribed."""
        if not self._subscribed and self.CHANNELS:
            await self._notify.subscribe(*self.CHANNELS)
            self._subscribed = True

    async def _throttle(self) -> None:
        """Ensure minimum interval between ticks."""
        elapsed = time.time() - self._last_tick
        if elapsed < self.MIN_INTERVAL:
            await asyncio.sleep(self.MIN_INTERVAL - elapsed)

    async def _execute_tick(self) -> bool:
        """Execute tick. Returns False if cancelled."""
        try:
            await self.tick()
            self._last_tick = time.time()
            return True
        except asyncio.CancelledError:
            return False
        except Exception as e:
            logger.exception("[%s] Error in tick: %s", self.name, e)
            self._last_tick = time.time()
            return True

    async def _wait_for_notify(self, interval: float) -> None:
        """Wait for interval or until notification received."""
        if not self.CHANNELS:
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
        if self._subscribed and self.CHANNELS:
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
