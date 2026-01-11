"""Scheduler - TTL + GC 오케스트레이터.

Background tasks:
- TTL: RUNNING → STANDBY → ARCHIVED 전환 (매 60초)
- GC: 고아 archive/container/volume 정리 (매 4시간)

장애 시 사용자 영향: 낮음 (운영 불편)
→ 같은 coordinator에서 실행해도 무방
"""

import time

from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.app.config import get_settings
from codehub.control.coordinator.base import (
    ChannelSubscriber,
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
)
from codehub.control.coordinator.scheduler_gc import GCRunner
from codehub.control.coordinator.scheduler_ttl import TTLRunner
from codehub.core.interfaces import InstanceController, StorageProvider
from codehub.infra.redis_kv import ActivityStore
from codehub.infra.redis_pubsub import ChannelPublisher

# Module-level settings cache
_settings = get_settings()


class Scheduler(CoordinatorBase):
    """TTL + GC 오케스트레이터.

    tick()에서 시간 기반으로 각 작업 실행:
    - TTL: 매 ttl_interval (60초)
    - GC: 매 gc_interval (4시간)
    """

    COORDINATOR_TYPE = CoordinatorType.SCHEDULER
    WAKE_TARGET = None  # No wake signal needed

    # Use shortest interval as base (TTL 60s)
    IDLE_INTERVAL = _settings.coordinator.ttl_interval
    ACTIVE_INTERVAL = _settings.coordinator.ttl_interval

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        subscriber: ChannelSubscriber,
        activity_store: ActivityStore,
        publisher: ChannelPublisher,
        storage: StorageProvider,
        ic: InstanceController,
    ) -> None:
        super().__init__(conn, leader, subscriber)

        # Compose runners
        self._ttl = TTLRunner(conn, activity_store, publisher)
        self._gc = GCRunner(conn, storage, ic)

        # Interval tracking
        self._ttl_interval = _settings.coordinator.ttl_interval
        self._gc_interval = _settings.coordinator.gc_interval
        self._last_ttl: float = 0.0
        self._last_gc: float = 0.0

    async def tick(self) -> None:
        """Execute scheduled tasks based on elapsed time."""
        now = time.monotonic()

        # TTL check (every ttl_interval)
        if now - self._last_ttl >= self._ttl_interval:
            await self._ttl.run()
            self._last_ttl = now

        # GC cleanup (every gc_interval)
        if now - self._last_gc >= self._gc_interval:
            await self._gc.run()
            self._last_gc = now
