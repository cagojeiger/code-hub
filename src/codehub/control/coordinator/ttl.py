"""TTLManager - 비활성 워크스페이스 desired_state 강등."""

from codehub.control.coordinator.base import CoordinatorBase, CoordinatorType


class TTLManager(CoordinatorBase):
    """비활성 워크스페이스 desired_state 강등."""

    COORDINATOR_TYPE = CoordinatorType.TTL

    IDLE_INTERVAL = 60.0

    async def tick(self) -> None:
        pass  # TODO: 비활성 감지 → desired_state 변경
