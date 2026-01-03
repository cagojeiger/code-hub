"""Observer Coordinator - 리소스 관측 → conditions DB 저장.

Reference: docs/architecture_v2/wc-observer.md
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from codehub.control.coordinator.base import (
    Channel,
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
    NotifyPublisher,
    NotifySubscriber,
)
from codehub.core.domain.conditions import ConditionStatus
from codehub.core.interfaces.instance import InstanceController
from codehub.core.interfaces.storage import StorageProvider
from codehub.infra.models import Workspace

logger = logging.getLogger(__name__)

# Condition keys
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

    async def observe_all(self) -> dict[str, dict[str, ConditionStatus]]:
        """Observe all resources and return conditions by workspace_id.

        Returns:
            {workspace_id: {condition_key: ConditionStatus, ...}, ...}
        """
        now = datetime.now(UTC)
        result: dict[str, dict[str, ConditionStatus]] = {}

        # 1. Bulk API calls (3회)
        containers = await self._ic.list_all(self._prefix)
        volumes = await self._sp.list_volumes(self._prefix)
        archives = await self._sp.list_archives(self._prefix)

        # 2. Index by workspace_id
        container_map = {c.workspace_id: c for c in containers}
        volume_map = {v.workspace_id: v for v in volumes}
        archive_map = {a.workspace_id: a for a in archives}

        # 3. Collect all workspace_ids
        all_ws_ids = set(container_map) | set(volume_map) | set(archive_map)

        # 4. Build conditions for each workspace
        for ws_id in all_ws_ids:
            conditions: dict[str, ConditionStatus] = {}

            # container_ready
            container = container_map.get(ws_id)
            conditions[COND_CONTAINER_READY] = ConditionStatus(
                status="True" if container and container.running else "False",
                reason=container.reason if container else "ContainerNotFound",
                message=container.message if container else "No container",
                last_transition_time=now,
            )

            # volume_ready
            volume = volume_map.get(ws_id)
            conditions[COND_VOLUME_READY] = ConditionStatus(
                status="True" if volume and volume.exists else "False",
                reason=volume.reason if volume else "VolumeNotFound",
                message=volume.message if volume else "No volume",
                last_transition_time=now,
            )

            # archive_ready
            archive = archive_map.get(ws_id)
            conditions[COND_ARCHIVE_READY] = ConditionStatus(
                status="True" if archive and archive.exists else "False",
                reason=archive.reason if archive else "NoArchive",
                message=archive.message if archive else "No archive",
                last_transition_time=now,
            )

            result[ws_id] = conditions

        return result


class ObserverCoordinator(CoordinatorBase):
    """Observer Coordinator - 리소스 관측 → conditions DB 저장.

    Single Writer: conditions, observed_at 소유
    """

    COORDINATOR_TYPE = CoordinatorType.OBSERVER
    CHANNELS = [Channel.OB_WAKE]

    IDLE_INTERVAL = 10.0
    ACTIVE_INTERVAL = 2.0

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        notify: NotifySubscriber,
        instance_controller: InstanceController,
        storage_provider: StorageProvider,
        publisher: NotifyPublisher,
        prefix: str = "ws-",
    ) -> None:
        super().__init__(conn, leader, notify)
        self._observer = BulkObserver(instance_controller, storage_provider, prefix)
        self._publisher = publisher

    async def tick(self) -> None:
        """Observe all resources and persist conditions to DB."""
        now = datetime.now(UTC)

        # 1. Bulk observe all resources
        observed = await self._observer.observe_all()
        logger.debug("Observed %d workspaces from infra", len(observed))

        if not observed:
            return

        # 2. Load existing workspaces from DB
        async with AsyncSession(bind=self._conn) as session:
            result = await session.execute(
                select(Workspace.id).where(Workspace.deleted_at.is_(None))
            )
            ws_ids = {str(row[0]) for row in result.fetchall()}

            # 3. Build rows for bulk upsert (only if exists in DB)
            rows = []
            for ws_id, conditions in observed.items():
                if ws_id not in ws_ids:
                    continue

                # Convert ConditionStatus to dict for JSONB
                conditions_dict = {
                    k: v.to_dict() if hasattr(v, "to_dict") else v
                    for k, v in conditions.items()
                }
                rows.append({
                    "id": ws_id,
                    "conditions": conditions_dict,
                    "observed_at": now,
                })

            # 4. Bulk upsert (1 query)
            if rows:
                stmt = insert(Workspace).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "conditions": stmt.excluded.conditions,
                        "observed_at": stmt.excluded.observed_at,
                    },
                )
                await session.execute(stmt)
                await session.commit()
                logger.debug("Updated conditions for %d workspaces", len(rows))

                # 5. Wake WC to process updated conditions
                await self._publisher.wake_wc()
