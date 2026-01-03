"""ArchiveGC - Orphan archive 정리."""

from codehub.control.coordinator.base import Channel, CoordinatorBase, CoordinatorType


class ArchiveGC(CoordinatorBase):
    """Orphan archive 정리."""

    COORDINATOR_TYPE = CoordinatorType.GC
    CHANNELS = [Channel.GC_WAKE]

    IDLE_INTERVAL = 3600.0

    async def tick(self) -> None:
        pass  # TODO: orphan archive 스캔 → 삭제
