"""TTLManager - 비활성 워크스페이스 desired_state 강등."""

from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.control.coordinator.base import (
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
    NotifySubscriber,
)


class TTLManager(CoordinatorBase):
    """비활성 워크스페이스 desired_state 강등."""

    COORDINATOR_TYPE = CoordinatorType.TTL

    IDLE_INTERVAL = 60.0

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        notify: NotifySubscriber,
    ) -> None:
        super().__init__(conn, leader, notify)

    async def tick(self) -> None:
        pass  # TODO: 비활성 감지 → desired_state 변경
