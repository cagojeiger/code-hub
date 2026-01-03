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
from codehub.core.interfaces.instance import ContainerInfo, InstanceController
from codehub.core.interfaces.storage import ArchiveInfo, StorageProvider, VolumeInfo
from codehub.core.models import Workspace

logger = logging.getLogger(__name__)


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

    async def observe_all(self) -> dict[str, dict[str, dict | None]]:
        """Observe all resources and return conditions by workspace_id.

        Returns:
            {workspace_id: {"container": {...}, "volume": {...}, "archive": {...}}, ...}
        """
        # 1. Bulk API calls (3회)
        containers = await self._ic.list_all(self._prefix)
        volumes = await self._sp.list_volumes(self._prefix)
        archives = await self._sp.list_archives(self._prefix)

        # 2. Index by workspace_id
        container_map: dict[str, ContainerInfo] = {c.workspace_id: c for c in containers}
        volume_map: dict[str, VolumeInfo] = {v.workspace_id: v for v in volumes}
        archive_map: dict[str, ArchiveInfo] = {a.workspace_id: a for a in archives}

        # 3. Collect all workspace_ids
        all_ws_ids = set(container_map) | set(volume_map) | set(archive_map)

        # 4. Build conditions for each workspace (Pydantic model_dump 직접 사용)
        result: dict[str, dict[str, dict | None]] = {}
        for ws_id in all_ws_ids:
            container = container_map.get(ws_id)
            volume = volume_map.get(ws_id)
            archive = archive_map.get(ws_id)

            result[ws_id] = {
                "container": container.model_dump() if container else None,
                "volume": volume.model_dump() if volume else None,
                "archive": archive.model_dump() if archive else None,
            }

        return result


class ObserverCoordinator(CoordinatorBase):
    """Observer Coordinator - 리소스 관측 → conditions DB 저장.

    Single Writer: conditions, observed_at 소유
    """

    COORDINATOR_TYPE = CoordinatorType.OBSERVER
    CHANNELS = [Channel.OB_WAKE]

    IDLE_INTERVAL = 15.0
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

                rows.append({
                    "id": ws_id,
                    "conditions": conditions,  # 이미 dict, 변환 불필요
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
