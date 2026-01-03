"""WorkspaceController - 워크스페이스 상태 수렴."""

from codehub.control.coordinator.base import Channel, CoordinatorBase, CoordinatorType


class WorkspaceController(CoordinatorBase):
    """워크스페이스 상태 수렴 컨트롤러."""

    COORDINATOR_TYPE = CoordinatorType.WC
    CHANNELS = [Channel.WC_WAKE]

    IDLE_INTERVAL = 30.0
    ACTIVE_INTERVAL = 2.0

    async def tick(self) -> None:
        pass  # TODO: Observe → Judge → Control → Persist
