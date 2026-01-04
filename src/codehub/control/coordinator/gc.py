"""ArchiveGC - Orphan archive 정리."""

from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.control.coordinator.base import (
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
    NotifySubscriber,
    WakeTarget,
)


class ArchiveGC(CoordinatorBase):
    """Orphan archive 정리."""

    COORDINATOR_TYPE = CoordinatorType.GC
    WAKE_TARGET = WakeTarget.GC

    IDLE_INTERVAL = 3600.0

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        notify: NotifySubscriber,
    ) -> None:
        super().__init__(conn, leader, notify)

    async def tick(self) -> None:
        pass  # TODO: orphan archive 스캔 → 삭제
