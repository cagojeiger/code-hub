"""EventListener - PG NOTIFY to Redis (CDC).

Reference: docs/architecture_v2/event-listener.md

Architecture:
- notify_conn (psycopg3): LISTEN/NOTIFY only (no other queries!)
- sa_conn (SQLAlchemy): Advisory Lock + SELECT queries

Uses asyncio.Queue to decouple NOTIFY receiving from processing.
This prevents psycopg3's notifies() generator from being blocked by queries.

Listens to 2 PostgreSQL NOTIFY channels:
- ws_sse: UI-visible field changes -> query DB -> publish full data
- ws_wake: desired_state changes -> PUBLISH to wake channels

Note: Requires leader election - only 1 EventListener should write to prevent duplicates.
"""

import asyncio
import json
import logging
from datetime import datetime

import psycopg
import redis.asyncio as redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection as SAConnection
from sqlalchemy.ext.asyncio import create_async_engine

from codehub.app.config import get_settings
from codehub.app.metrics.collector import (
    EVENT_ERRORS_TOTAL,
    EVENT_LISTENER_IS_LEADER,
    EVENT_NOTIFY_RECEIVED_TOTAL,
    EVENT_QUEUE_SIZE,
    EVENT_SSE_PUBLISHED_TOTAL,
    EVENT_WAKE_PUBLISHED_TOTAL,
)
from codehub.core.logging_schema import LogEvent
from codehub.infra.pg_leader import SQLAlchemyLeaderElection
from codehub.infra.redis_pubsub import ChannelPublisher

logger = logging.getLogger(__name__)

_settings = get_settings()
_channel_config = _settings.redis_channel

# SQL query to fetch workspace data for SSE (SQLAlchemy :param style)
_FETCH_WORKSPACE_SQL = """
    SELECT id, owner_user_id, name, description, memo, image_ref,
           phase, operation, desired_state, archive_key,
           error_reason, error_count, created_at, updated_at,
           last_access_at, phase_changed_at, deleted_at
    FROM workspaces
    WHERE id = :workspace_id
"""


class EventListener:
    """PG NOTIFY -> Redis PUB/SUB transformer.

    Runs in FastAPI lifespan as a background task.
    Uses psycopg3's notifies() for OS-level wait (real-time notification).
    Uses SQLAlchemy for leader election and queries (same pattern as other Coordinators).
    """

    # PG NOTIFY channels
    CHANNEL_SSE = "ws_sse"
    CHANNEL_WAKE = "ws_wake"

    # Advisory lock key (consistent across all instances)
    LOCK_KEY = "event_listener"

    # Interval for leader acquisition retry
    LEADER_WAIT_INTERVAL_SEC = 5

    def __init__(
        self,
        database_url: str,
        redis_client: redis.Redis,
        publisher: ChannelPublisher | None = None,
    ) -> None:
        """Initialize EventListener.

        Args:
            database_url: PostgreSQL connection URL.
            redis_client: Redis client (fallback for creating publisher).
            publisher: ChannelPublisher for PUB/SUB operations.
                      If None, creates one from redis_client.
        """
        self._database_url = database_url
        self._publisher = publisher or ChannelPublisher(redis_client)
        self._running = False
        self._log_prefix = self.__class__.__name__

        # Dual connections
        self._notify_conn: psycopg.AsyncConnection | None = None  # LISTEN only
        self._sa_conn: SAConnection | None = None  # Advisory Lock + SELECT
        self._engine = None

        # Event queue for decoupling NOTIFY receiving from processing
        self._event_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

    async def run(self) -> None:
        """Start listening to PG NOTIFY channels.

        This method blocks until stop() is called or cancelled.
        Uses psycopg3's notifies() for LISTEN/NOTIFY only.
        Uses SQLAlchemy for leader election and queries.
        """
        self._running = True

        # 1. psycopg3 connection (LISTEN only - NO other queries!)
        self._notify_conn = await psycopg.AsyncConnection.connect(
            self._database_url,
            autocommit=True,
        )
        logger.info(
            "Connected to PostgreSQL (psycopg3 for LISTEN)",
            extra={"event": LogEvent.DB_CONNECTED},
        )

        # 2. SQLAlchemy connection (Advisory Lock + SELECT)
        # Convert postgresql:// to postgresql+asyncpg:// for SQLAlchemy async
        sa_url = self._database_url.replace("postgresql://", "postgresql+asyncpg://")
        self._engine = create_async_engine(sa_url)
        self._sa_conn = await self._engine.connect()
        logger.info(
            "Connected to PostgreSQL (SQLAlchemy for queries)",
            extra={"event": LogEvent.DB_CONNECTED},
        )

        # 3. Use SQLAlchemyLeaderElection (same as other Coordinators)
        leader = SQLAlchemyLeaderElection(self._sa_conn, self.LOCK_KEY)

        try:
            # Wait for leadership
            while self._running and not await leader.try_acquire():
                await asyncio.sleep(self.LEADER_WAIT_INTERVAL_SEC)

            if not self._running:
                return

            EVENT_LISTENER_IS_LEADER.set(1)
            logger.info(
                "Acquired leadership",
                extra={"event": LogEvent.LEADERSHIP_ACQUIRED},
            )

            # Register LISTEN channels (psycopg3 only!)
            await self._notify_conn.execute(f"LISTEN {self.CHANNEL_SSE}")
            await self._notify_conn.execute(f"LISTEN {self.CHANNEL_WAKE}")
            logger.info(
                "Listening to channels",
                extra={
                    "event": LogEvent.REDIS_SUBSCRIBED,
                    "channels": [self.CHANNEL_SSE, self.CHANNEL_WAKE],
                },
            )

            # Start worker task
            worker_task = asyncio.create_task(self._worker_loop())

            try:
                # notifies loop - ONLY put to queue (no SELECT, no blocking!)
                async for notify in self._notify_conn.notifies():
                    if not self._running:
                        break
                    logger.info(
                        "NOTIFY received",
                        extra={
                            "event": LogEvent.NOTIFY_RECEIVED,
                            "channel": notify.channel,
                        },
                    )
                    await self._event_queue.put((notify.channel, notify.payload or ""))
            finally:
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

        except asyncio.CancelledError:
            logger.info("Cancelled, cleaning up", extra={"event": LogEvent.APP_STOPPED})
        except Exception as e:
            logger.exception("Error: %s", e)
        finally:
            await self._close_connections()

    async def _worker_loop(self) -> None:
        """Queue consumer worker - SELECT queries and Redis publish."""
        while True:
            try:
                # Update queue size metric before processing
                EVENT_QUEUE_SIZE.set(self._event_queue.qsize())

                channel, payload = await self._event_queue.get()
                await self._dispatch(channel, payload)
                self._event_queue.task_done()

                # Update queue size after processing
                EVENT_QUEUE_SIZE.set(self._event_queue.qsize())
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Worker error: %s", e)

    async def _close_connections(self) -> None:
        """Close all connections."""
        EVENT_LISTENER_IS_LEADER.set(0)
        if self._notify_conn:
            await self._notify_conn.close()
            self._notify_conn = None
        if self._sa_conn:
            await self._sa_conn.close()
            self._sa_conn = None
        if self._engine:
            await self._engine.dispose()
            self._engine = None
        logger.info("Stopped", extra={"event": LogEvent.APP_STOPPED})

    async def _dispatch(self, channel: str, payload: str) -> None:
        """Dispatch event to appropriate handler."""
        # Record NOTIFY received metric
        EVENT_NOTIFY_RECEIVED_TOTAL.labels(channel=channel).inc()

        try:
            if channel == self.CHANNEL_SSE:
                await self._handle_sse(payload)
            elif channel == self.CHANNEL_WAKE:
                await self._handle_wake()
        except Exception as e:
            logger.exception("Error handling %s: %s", channel, e)

    def stop(self) -> None:
        """Stop the event listener."""
        self._running = False

    async def _handle_sse(self, payload: str) -> None:
        """Handle SSE event - query DB and publish full workspace data.

        Payload: {"id": "...", "owner_user_id": "..."}
        Queries DB for full workspace data, publishes to: {sse_prefix}:{owner_user_id}
        Frontend uses deleted_at field to determine if workspace was deleted.
        """
        try:
            data = json.loads(payload)
            workspace_id = data.get("id")
            user_id = data.get("owner_user_id")
            if not workspace_id or not user_id:
                logger.warning(
                    "SSE payload missing id or owner_user_id",
                    extra={"event": LogEvent.SSE_RECEIVED, "payload": payload},
                )
                return

            # Query DB for full workspace data (using SQLAlchemy connection)
            workspace_data = await self._fetch_workspace(workspace_id)
            if workspace_data is None:
                # Hard deleted - skip (no notification needed)
                logger.debug("SSE workspace not found (hard deleted): %s", workspace_id)
                return

            # Publish full workspace data (including deleted_at for soft deletes)
            channel = f"{_channel_config.sse_prefix}:{user_id}"
            await self._publisher.publish(channel, json.dumps(workspace_data))
            EVENT_SSE_PUBLISHED_TOTAL.inc()
            logger.info(
                "SSE published",
                extra={
                    "event": LogEvent.SSE_PUBLISHED,
                    "user_id": user_id,
                    "ws_id": workspace_id,
                    "deleted": workspace_data.get("deleted_at") is not None,
                },
            )
        except json.JSONDecodeError as e:
            EVENT_ERRORS_TOTAL.labels(operation="sse").inc()
            logger.warning(
                "Invalid SSE payload",
                extra={"event": LogEvent.SSE_RECEIVED, "payload": payload, "error": str(e)},
            )
        except Exception as e:
            EVENT_ERRORS_TOTAL.labels(operation="sse").inc()
            logger.exception("SSE error: %s", e)

    async def _fetch_workspace(self, workspace_id: str) -> dict | None:
        """Fetch workspace data from DB using SQLAlchemy connection.

        Returns:
            Workspace data as dict, or None if not found.
        """
        if self._sa_conn is None:
            logger.error(
                "DB connection not available",
                extra={"event": LogEvent.DB_ERROR},
            )
            return None

        result = await self._sa_conn.execute(
            text(_FETCH_WORKSPACE_SQL),
            {"workspace_id": workspace_id},
        )
        row = result.fetchone()

        if row is None:
            return None

        # Convert SQLAlchemy Row to dict with proper JSON serialization
        columns = [
            "id", "owner_user_id", "name", "description", "memo", "image_ref",
            "phase", "operation", "desired_state", "archive_key",
            "error_reason", "error_count", "created_at", "updated_at",
            "last_access_at", "phase_changed_at", "deleted_at",
        ]
        data = {}
        for i, col in enumerate(columns):
            value = row[i]
            # Convert datetime to ISO format string for JSON serialization
            if isinstance(value, datetime):
                data[col] = value.isoformat()
            else:
                data[col] = value
        return data

    async def _handle_wake(self) -> None:
        """Handle wake event - PUBLISH to wake channels.

        Publishes to both Observer and WC channels in parallel (1 RTT).
        """
        try:
            observer_channel = f"{_channel_config.wake_prefix}:observer"
            wc_channel = f"{_channel_config.wake_prefix}:wc"
            observer_count, wc_count = await asyncio.gather(
                self._publisher.publish(observer_channel),
                self._publisher.publish(wc_channel),
            )
            EVENT_WAKE_PUBLISHED_TOTAL.labels(target="observer").inc()
            EVENT_WAKE_PUBLISHED_TOTAL.labels(target="wc").inc()
            logger.info(
                "Wake published",
                extra={
                    "event": LogEvent.WAKE_PUBLISHED,
                    "observer_count": observer_count,
                    "wc_count": wc_count,
                },
            )
        except Exception as e:
            EVENT_ERRORS_TOTAL.labels(operation="wake").inc()
            logger.exception("Wake error: %s", e)
