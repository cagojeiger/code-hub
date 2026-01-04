"""EventListener - PG NOTIFY to Redis Streams (CDC).

Reference: docs/architecture_v2/event-listener.md

Uses psycopg3 AsyncConnection for native LISTEN/NOTIFY support.
No keep-alive needed - notifies() generator handles idle connections.

Listens to 3 PostgreSQL NOTIFY channels:
- ws_sse: phase/operation changes -> XADD events:{user_id}
- ws_wake: desired_state changes -> XADD stream:wake
- ws_deleted: soft deletes -> XADD events:{user_id}

Note: Requires leader election - only 1 EventListener should XADD to prevent duplicates.
Uses PostgreSQL Advisory Lock for leader election.
"""

import asyncio
import json
import logging

import psycopg
import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Redis Streams constants
STREAM_MAXLEN = 1000  # Max messages per user stream
WAKE_STREAM_MAXLEN = 100  # Max wake messages


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

    # Redis Streams keys
    STREAM_WAKE = "stream:wake"

    def __init__(
        self,
        database_url: str,
        redis_client: redis.Redis,
    ) -> None:
        """Initialize EventListener.

        Args:
            database_url: PostgreSQL connection URL (psycopg format).
            redis_client: Redis client for XADD operations.
        """
        self._database_url = database_url
        self._redis = redis_client
        self._running = False
        self._log_prefix = self.__class__.__name__

    async def run(self) -> None:
        """Start listening to PG NOTIFY channels.

        This method blocks until stop() is called or cancelled.
        Uses psycopg3's notifies() generator for native notification support.
        Uses PostgreSQL Advisory Lock for leader election.
        """
        self._running = True

        # psycopg3 async connection (autocommit required for NOTIFY)
        aconn = await psycopg.AsyncConnection.connect(
            self._database_url,
            autocommit=True,
        )
        logger.info("[%s] Connected to PostgreSQL", self._log_prefix)

        try:
            # Leader election using PostgreSQL Advisory Lock
            is_leader = await self._try_acquire_lock(aconn)

            if not is_leader:
                logger.info("[%s] Not leader, waiting...", self._log_prefix)
                while self._running:
                    await asyncio.sleep(5)
                    if await self._try_acquire_lock(aconn):
                        logger.info("[%s] Became leader", self._log_prefix)
                        break
                else:
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

    async def _try_acquire_lock(self, aconn: psycopg.AsyncConnection) -> bool:
        """Try to acquire PostgreSQL Advisory Lock.

        Returns True if lock acquired (this instance is the leader).
        """
        result = await aconn.execute(
            f"SELECT pg_try_advisory_lock(hashtext('{self.LOCK_KEY}'))"
        )
        row = await result.fetchone()
        return row[0] if row else False

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
        """Handle SSE event - XADD to user-specific stream.

        Payload: {"id": "...", "owner_user_id": "..."}
        Adds to: events:{owner_user_id}
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

            stream_key = f"events:{user_id}"
            await self._redis.xadd(
                stream_key,
                {"data": payload},
                maxlen=STREAM_MAXLEN,
            )
            logger.debug("[%s] SSE -> XADD %s", self._log_prefix, stream_key)
        except json.JSONDecodeError as e:
            logger.warning(
                "[%s] Invalid SSE payload: %s (%s)", self._log_prefix, payload, e
            )
        except Exception as e:
            logger.exception("[%s] SSE error: %s", self._log_prefix, e)

    async def _handle_wake(self) -> None:
        """Handle wake event - XADD to wake stream.

        Adds to: stream:wake with target="ob" and target="wc"
        """
        try:
            await self._redis.xadd(
                self.STREAM_WAKE,
                {"target": "ob"},
                maxlen=WAKE_STREAM_MAXLEN,
            )
            await self._redis.xadd(
                self.STREAM_WAKE,
                {"target": "wc"},
                maxlen=WAKE_STREAM_MAXLEN,
            )
            logger.debug("[%s] Wake -> XADD %s", self._log_prefix, self.STREAM_WAKE)
        except Exception as e:
            logger.exception("[%s] Wake error: %s", self._log_prefix, e)

    async def _handle_deleted(self, payload: str) -> None:
        """Handle delete event - XADD to user stream with deleted flag.

        Payload: {"id": "...", "owner_user_id": "..."}
        Adds to: events:{owner_user_id} with deleted=true
        """
        try:
            data = json.loads(payload)
            user_id = data.get("owner_user_id")
            if not user_id:
                logger.warning(
                    "[%s] Deleted payload missing owner_user_id: %s",
                    self._log_prefix,
                    payload,
                )
                return

            # Add deleted flag
            data["deleted"] = True
            stream_key = f"events:{user_id}"
            await self._redis.xadd(
                stream_key,
                {"data": json.dumps(data)},
                maxlen=STREAM_MAXLEN,
            )
            logger.debug("[%s] Deleted -> XADD %s", self._log_prefix, stream_key)
        except json.JSONDecodeError as e:
            logger.warning(
                "[%s] Invalid deleted payload: %s (%s)", self._log_prefix, payload, e
            )
        except Exception as e:
            logger.exception("[%s] Deleted error: %s", self._log_prefix, e)
