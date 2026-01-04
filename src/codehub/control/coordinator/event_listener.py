"""EventListener - PG NOTIFY to Redis (CDC).

Reference: docs/architecture_v2/event-listener.md

Uses psycopg3 AsyncConnection for native LISTEN/NOTIFY support.
No keep-alive needed - notifies() generator handles idle connections.

Listens to 3 PostgreSQL NOTIFY channels:
- ws_sse: phase/operation changes -> SSEStreamPublisher.publish_update()
- ws_wake: desired_state changes -> PUBLISH ob:wake, wc:wake (PUB/SUB)
- ws_deleted: soft deletes -> SSEStreamPublisher.publish_deleted()

Note: Requires leader election - only 1 EventListener should write to prevent duplicates.
Uses PostgreSQL Advisory Lock for leader election.
"""

import asyncio
import json
import logging

import psycopg
import redis.asyncio as redis

from codehub.control.coordinator.base import LeaderElection
from codehub.infra.redis import NotifyPublisher, SSEStreamPublisher

logger = logging.getLogger(__name__)


class EventListener:
    """PG NOTIFY -> Redis Streams transformer.

    Runs in FastAPI lifespan as a background task.
    Uses psycopg3's notifies() generator for reliable notification delivery.
    Uses PostgreSQL Advisory Lock for leader election (only 1 instance writes).
    """

    # PG NOTIFY channels
    CHANNEL_SSE = "ws_sse"
    CHANNEL_WAKE = "ws_wake"
    CHANNEL_DELETED = "ws_deleted"

    # Advisory lock key (consistent across all instances)
    LOCK_KEY = "event_listener"

    # Interval for leader acquisition retry
    LEADER_WAIT_INTERVAL_SEC = 5

    def __init__(
        self,
        database_url: str,
        redis_client: redis.Redis,
        sse_publisher: SSEStreamPublisher | None = None,
        wake_publisher: NotifyPublisher | None = None,
    ) -> None:
        """Initialize EventListener.

        Args:
            database_url: PostgreSQL connection URL (psycopg format).
            redis_client: Redis client (fallback for creating publishers).
            sse_publisher: SSEStreamPublisher for Streams operations.
                          If None, creates one from redis_client.
            wake_publisher: NotifyPublisher for wake notifications.
                           If None, creates one from redis_client.
        """
        self._database_url = database_url
        self._sse = sse_publisher or SSEStreamPublisher(redis_client)
        self._wake = wake_publisher or NotifyPublisher(redis_client)
        self._running = False
        self._log_prefix = self.__class__.__name__

    async def run(self) -> None:
        """Start listening to PG NOTIFY channels.

        This method blocks until stop() is called or cancelled.
        Uses psycopg3's notifies() generator for native notification support.
        Uses unified LeaderElection for leader election.
        """
        self._running = True

        # psycopg3 async connection (autocommit required for NOTIFY)
        aconn = await psycopg.AsyncConnection.connect(
            self._database_url,
            autocommit=True,
        )
        logger.info("[%s] Connected to PostgreSQL", self._log_prefix)

        # Use unified LeaderElection (supports both SQLAlchemy and psycopg)
        leader = LeaderElection(aconn, self.LOCK_KEY)

        try:
            # Wait for leadership
            while self._running and not await leader.try_acquire():
                await asyncio.sleep(self.LEADER_WAIT_INTERVAL_SEC)

            if not self._running:
                return

            # Subscribe to channels using SQL LISTEN (leader only)
            await aconn.execute(f"LISTEN {self.CHANNEL_SSE}")
            await aconn.execute(f"LISTEN {self.CHANNEL_WAKE}")
            await aconn.execute(f"LISTEN {self.CHANNEL_DELETED}")
            logger.info(
                "[%s] Leader: Listening to %s, %s, %s",
                self._log_prefix,
                self.CHANNEL_SSE,
                self.CHANNEL_WAKE,
                self.CHANNEL_DELETED,
            )

            # Generator-based notification loop (no keep-alive needed)
            async for notify in aconn.notifies():
                if not self._running:
                    break
                await self._dispatch(notify.channel, notify.payload)

        except asyncio.CancelledError:
            logger.info("[%s] Cancelled, cleaning up", self._log_prefix)
        except Exception as e:
            logger.exception("[%s] Error: %s", self._log_prefix, e)
        finally:
            await aconn.close()
            logger.info("[%s] Stopped", self._log_prefix)

    async def _dispatch(self, channel: str, payload: str) -> None:
        """Dispatch event to appropriate handler."""
        try:
            if channel == self.CHANNEL_SSE:
                await self._handle_sse(payload)
            elif channel == self.CHANNEL_WAKE:
                await self._handle_wake()
            elif channel == self.CHANNEL_DELETED:
                await self._handle_deleted(payload)
        except Exception as e:
            logger.exception("[%s] Error handling %s: %s", self._log_prefix, channel, e)

    def stop(self) -> None:
        """Stop the event listener."""
        self._running = False

    async def _handle_sse(self, payload: str) -> None:
        """Handle SSE event - publish to user-specific stream.

        Payload: {"id": "...", "owner_user_id": "..."}
        Publishes to: events:{owner_user_id}
        """
        try:
            data = json.loads(payload)
            user_id = data.get("owner_user_id")
            if not user_id:
                logger.warning(
                    "[%s] SSE payload missing owner_user_id: %s",
                    self._log_prefix,
                    payload,
                )
                return

            await self._sse.publish_update(user_id, payload)
            logger.debug("[%s] SSE -> publish_update user=%s", self._log_prefix, user_id)
        except json.JSONDecodeError as e:
            logger.warning(
                "[%s] Invalid SSE payload: %s (%s)", self._log_prefix, payload, e
            )
        except Exception as e:
            logger.exception("[%s] SSE error: %s", self._log_prefix, e)

    async def _handle_wake(self) -> None:
        """Handle wake event - PUBLISH to wake channels.

        Uses NotifyPublisher.wake_ob_wc() for parallel execution (1 RTT).
        """
        try:
            await self._wake.wake_ob_wc()
            logger.debug("[%s] Wake -> wake_ob_wc()", self._log_prefix)
        except Exception as e:
            logger.exception("[%s] Wake error: %s", self._log_prefix, e)

    async def _handle_deleted(self, payload: str) -> None:
        """Handle delete event - publish deleted event to user stream.

        Payload: {"id": "...", "owner_user_id": "..."}
        Publishes to: events:{owner_user_id} with deleted=true
        """
        try:
            data = json.loads(payload)
            user_id = data.get("owner_user_id")
            workspace_id = data.get("id")
            if not user_id or not workspace_id:
                logger.warning(
                    "[%s] Deleted payload missing owner_user_id or id: %s",
                    self._log_prefix,
                    payload,
                )
                return

            await self._sse.publish_deleted(user_id, workspace_id)
            logger.debug("[%s] Deleted -> publish_deleted user=%s ws=%s", self._log_prefix, user_id, workspace_id)
        except json.JSONDecodeError as e:
            logger.warning(
                "[%s] Invalid deleted payload: %s (%s)", self._log_prefix, payload, e
            )
        except Exception as e:
            logger.exception("[%s] Deleted error: %s", self._log_prefix, e)
