"""Coordinator infrastructure - base class for coordinators.

Configuration via CoordinatorConfig (COORDINATOR_ env prefix).
"""

import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncConnection as SAConnection

from codehub.app.config import get_settings
from codehub.core.interfaces.leader import LeaderElection
from codehub.core.logging_schema import LogEvent
from codehub.infra.redis_pubsub import ChannelSubscriber

logger = logging.getLogger(__name__)

_settings = get_settings()
_coordinator_config = _settings.coordinator
_channel_config = _settings.redis_channel


class CoordinatorType(StrEnum):
    """Coordinator types for leader election."""

    OBSERVER = "observer"
    WC = "wc"
    GC = "gc"
    TTL = "ttl"


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

    IDLE_INTERVAL: float = _coordinator_config.idle_interval
    ACTIVE_INTERVAL: float = _coordinator_config.active_interval
    MIN_INTERVAL: float = _coordinator_config.min_interval
    LEADER_RETRY_INTERVAL: float = _coordinator_config.leader_retry_interval
    VERIFY_INTERVAL: float = _coordinator_config.verify_interval
    VERIFY_JITTER: float = _coordinator_config.verify_jitter
    ACTIVE_DURATION: float = _coordinator_config.active_duration

    COORDINATOR_TYPE: CoordinatorType
    WAKE_TARGET: str | None = None  # e.g., "ob", "wc", "gc" - receives wake from this target

    def __init__(
        self,
        conn: SAConnection,
        leader: LeaderElection,
        subscriber: ChannelSubscriber,
    ) -> None:
        self._conn = conn  # Shared with Advisory Lock - see ADR-012
        self._leader = leader
        self._subscriber = subscriber
        self._running = False
        self._subscribed = False
        self._active_until = time.time() + self.ACTIVE_DURATION
        self._last_verify = 0.0
        self._last_tick = 0.0
        # Leadership waiting state tracking (for LEADERSHIP_ACQUIRED log)
        self._waiting_since: float | None = None

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def is_active(self) -> bool:
        return time.time() < self._active_until

    def accelerate(self) -> None:
        self._active_until = time.time() + self.ACTIVE_DURATION
        logger.info(
            "Accelerating",
            extra={"event": LogEvent.STATE_CHANGED, "duration": self.ACTIVE_DURATION},
        )

    def _get_interval(self) -> float:
        return self.ACTIVE_INTERVAL if self.is_active else self.IDLE_INTERVAL

    def _jittered_verify_interval(self) -> float:
        """Return VERIFY_INTERVAL with ±VERIFY_JITTER random jitter.

        Jitter prevents Thundering Herd when multiple coordinators
        try to re-verify leadership at the same time.
        """
        jitter = 1.0 + random.uniform(-self.VERIFY_JITTER, self.VERIFY_JITTER)
        return self.VERIFY_INTERVAL * jitter

    async def _safe_rollback(self) -> None:
        """Rollback transaction, logging any errors."""
        try:
            await self._conn.rollback()
        except Exception as e:
            logger.warning(
                "Rollback failed",
                extra={"event": LogEvent.DB_ERROR, "error": str(e)},
            )

    @abstractmethod
    async def tick(self) -> None:
        """Execute one reconciliation cycle."""
        pass

    async def run(self) -> None:
        """Main coordinator loop."""
        self._running = True
        logger.info("Starting coordinator", extra={"event": LogEvent.APP_STARTED})

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
        # P5: Use jittered interval to prevent Thundering Herd
        if now - self._last_verify <= self._jittered_verify_interval() and self._leader.is_leader:
            # Reset waiting state when we have leadership
            self._waiting_since = None
            return True

        try:
            acquired = await self._leader.try_acquire()
        except Exception as e:
            logger.warning(
                "Error acquiring leadership",
                extra={"event": LogEvent.LEADERSHIP_LOST, "error": str(e)},
            )
            await self._safe_rollback()
            acquired = False

        if not acquired:
            await self._release_subscription()
            # Track waiting state for LEADERSHIP_ACQUIRED log
            if self._waiting_since is None:
                self._waiting_since = now
            await asyncio.sleep(self.LEADER_RETRY_INTERVAL)
            return False

        # Leadership acquired - reset waiting state and log
        if self._waiting_since is not None:
            wait_seconds = now - self._waiting_since
            logger.info(
                "Leadership acquired after waiting",
                extra={
                    "event": LogEvent.LEADERSHIP_ACQUIRED,
                    "wait_seconds": round(wait_seconds, 1),
                },
            )
        self._waiting_since = None
        self._last_verify = now
        return True

    async def _ensure_subscribed(self) -> None:
        """Subscribe to wake channel if not already subscribed."""
        if not self._subscribed and self.WAKE_TARGET:
            channel = f"{_channel_config.wake_prefix}:{self.WAKE_TARGET}"
            await self._subscriber.subscribe(channel)
            self._subscribed = True

    async def _throttle(self) -> None:
        """Ensure minimum interval between ticks."""
        elapsed = time.time() - self._last_tick
        if elapsed < self.MIN_INTERVAL:
            await asyncio.sleep(self.MIN_INTERVAL - elapsed)

    async def _execute_tick(self) -> bool:
        """Execute tick. Returns False if cancelled or leadership lost."""
        # P6: Verify leadership before tick to detect Split Brain early
        if not await self._leader.verify_holding():
            logger.warning(
                "Leadership lost before tick - skipping",
                extra={"event": LogEvent.LEADERSHIP_LOST},
            )
            await self._release_subscription()
            return True  # Continue loop to re-acquire leadership

        try:
            await self.tick()
            self._last_tick = time.time()
            return True
        except asyncio.CancelledError:
            return False
        except Exception as e:
            logger.exception("Error in tick: %s", e)
            await self._safe_rollback()
            self._last_tick = time.time()
            return True

    async def _wait_for_notify(self, interval: float) -> None:
        """Wait for interval or until notification received."""
        if not self.WAKE_TARGET:
            await asyncio.sleep(interval)
            return

        try:
            msg = await self._subscriber.get_message(timeout=interval)
            if msg is not None:  # Empty string "" is valid wake signal
                self.accelerate()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "Error checking notify",
                extra={"event": LogEvent.REDIS_CONNECTION_ERROR, "error": str(e)},
            )
            await asyncio.sleep(interval)

    async def _release_subscription(self) -> None:
        """Release notify subscription."""
        if self._subscribed and self.WAKE_TARGET:
            try:
                await self._subscriber.unsubscribe()
            except Exception as e:
                logger.warning(
                    "Error unsubscribing",
                    extra={"event": LogEvent.REDIS_CONNECTION_ERROR, "error": str(e)},
                )
            self._subscribed = False

    async def _cleanup(self) -> None:
        """Cleanup on shutdown."""
        logger.info("Cleaning up", extra={"event": LogEvent.APP_STOPPED})
        await self._release_subscription()
        try:
            await self._leader.release()
        except Exception as e:
            logger.warning(
                "Error releasing leadership",
                extra={"event": LogEvent.LEADERSHIP_LOST, "error": str(e)},
            )
