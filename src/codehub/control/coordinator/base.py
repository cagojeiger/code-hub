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


class WakeTarget(StrEnum):
    """Wake target identifiers for Redis Streams."""

    OB = "ob"
    WC = "wc"
    GC = "gc"


# Legacy Channel names (kept for backwards compatibility)
class Channel(StrEnum):
    """Notify channel names (legacy, for backwards compatibility)."""

    OB_WAKE = "ob:wake"
    WC_WAKE = "wc:wake"
    GC_WAKE = "gc:wake"


class CoordinatorType(StrEnum):
    """Coordinator types for leader election."""

    OBSERVER = "observer"
    WC = "wc"
    GC = "gc"
    TTL = "ttl"


# Redis Streams constants
STREAM_WAKE = "stream:wake"
CONSUMER_GROUP = "coordinators"
STREAM_MAXLEN = 100


class NotifyPublisher:
    """Publishes notifications to wake up Coordinators via Redis Streams.

    Uses XADD to add wake messages to stream:wake.
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    async def publish(self, target: WakeTarget) -> str:
        """Publish wake message to stream.

        Returns the message ID.
        """
        msg_id = await self._client.xadd(
            STREAM_WAKE,
            {"target": str(target)},
            maxlen=STREAM_MAXLEN,
        )
        logger.debug("Published wake to %s (target=%s, id=%s)", STREAM_WAKE, target, msg_id)
        return msg_id

    async def wake_ob(self) -> str:
        return await self.publish(WakeTarget.OB)

    async def wake_wc(self) -> str:
        return await self.publish(WakeTarget.WC)

    async def wake_gc(self) -> str:
        return await self.publish(WakeTarget.GC)


class NotifySubscriber:
    """Subscribes to wake stream using Redis Streams XREADGROUP.

    Uses consumer groups for exactly-once message delivery.
    Each coordinator instance is a separate consumer in the group.
    """

    def __init__(self, client: redis.Redis, consumer_name: str) -> None:
        """Initialize subscriber.

        Args:
            client: Redis client.
            consumer_name: Unique consumer name (e.g., "observer-12345").
        """
        self._client = client
        self._consumer_name = consumer_name
        self._target: WakeTarget | None = None
        self._initialized = False

    async def subscribe(self, target: WakeTarget) -> None:
        """Subscribe to wake stream for a specific target.

        Creates consumer group if it doesn't exist.
        """
        self._target = target
        await self._ensure_group()
        logger.info(
            "Subscribed to %s (consumer=%s, target=%s)",
            STREAM_WAKE,
            self._consumer_name,
            target,
        )

    async def _ensure_group(self) -> None:
        """Create consumer group if it doesn't exist."""
        if self._initialized:
            return

        try:
            await self._client.xgroup_create(
                STREAM_WAKE,
                CONSUMER_GROUP,
                id="$",  # Start from new messages
                mkstream=True,  # Create stream if not exists
            )
            logger.info("Created consumer group %s on %s", CONSUMER_GROUP, STREAM_WAKE)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
            # Group already exists, that's fine

        self._initialized = True

    async def unsubscribe(self) -> None:
        """Unsubscribe (no-op for streams, kept for interface compatibility)."""
        self._target = None

    async def get_message(self, timeout: float = 0.0) -> str | None:
        """Read wake message from stream.

        Returns the target if a matching message was received, None otherwise.
        Automatically ACKs processed messages.
        """
        if not self._target:
            return None

        await self._ensure_group()

        try:
            # XREADGROUP: read new messages for this consumer
            messages = await self._client.xreadgroup(
                CONSUMER_GROUP,
                self._consumer_name,
                {STREAM_WAKE: ">"},  # Only new messages
                block=int(timeout * 1000) if timeout > 0 else None,
                count=10,  # Process up to 10 messages at once
            )

            if not messages:
                return None

            for stream, entries in messages:
                for msg_id, fields in entries:
                    # Get target from message
                    target_raw = fields.get(b"target") or fields.get("target")
                    if isinstance(target_raw, bytes):
                        target_raw = target_raw.decode()

                    # ACK the message
                    await self._client.xack(STREAM_WAKE, CONSUMER_GROUP, msg_id)

                    # Check if this message is for us
                    if target_raw == str(self._target):
                        logger.debug(
                            "Received wake (consumer=%s, target=%s)",
                            self._consumer_name,
                            target_raw,
                        )
                        return target_raw

            return None

        except Exception as e:
            logger.warning("Error reading from stream: %s", e)
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
        conn: AsyncConnection,
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
