"""Bulk observer for workspace resources.

Reference: docs/architecture_v2/wc-observer.md
"""

from codehub.core.interfaces.instance import InstanceController
from codehub.core.interfaces.storage import StorageProvider

# Condition keys (JSONB에서 사용하는 실제 키)
COND_VOLUME_READY = "storage.volume_ready"
COND_ARCHIVE_READY = "storage.archive_ready"
COND_CONTAINER_READY = "infra.container_ready"


class BulkObserver:
    """Bulk observe all workspace resources.

    Performance: 3 API calls instead of N (where N = workspace count)
    """

    def __init__(
        self,
        instance_controller: InstanceController,
        storage_provider: StorageProvider,
        prefix: str = "ws-",
    ) -> None:
        self._ic = instance_controller
        self._sp = storage_provider
        self._prefix = prefix

    async def observe_all(self) -> dict[str, dict]:
        """Observe all resources and return conditions by workspace_id.

        Returns:
            {workspace_id: {condition_key: ConditionStatus, ...}, ...}
        """
        pass  # TODO: IC.list_all, SP.list_volumes, SP.list_archives
