"""Observer Coordinator - 리소스 관측 → conditions DB 저장.

Reference: docs/architecture_v2/wc-observer.md
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.app.config import get_settings
from codehub.control.coordinator.base import (
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
    NotifySubscriber,
    WakeTarget,
)
from codehub.core.interfaces.instance import ContainerInfo, InstanceController
from codehub.core.interfaces.storage import ArchiveInfo, StorageProvider, VolumeInfo
from codehub.core.models import Workspace

logger = logging.getLogger(__name__)

# Load settings once at module level
_settings = get_settings()


class BulkObserver:
    """Bulk observe all workspace resources.

    Performance: 3 API calls instead of N (where N = workspace count)
    """

    def __init__(
        self,
        instance_controller: InstanceController,
        storage_provider: StorageProvider,
    ) -> None:
        self._ic = instance_controller
        self._sp = storage_provider
        self._prefix = _settings.docker.resource_prefix
        self._log_prefix = self.__class__.__name__

    async def observe_all(self) -> dict[str, dict[str, dict | None]]:
        """Observe all resources and return conditions by workspace_id.

        Returns:
            {workspace_id: {"container": {...}, "volume": {...}, "archive": {...}}, ...}
        """
        # 1. Bulk API calls (3회)
        logger.debug("[%s] Starting observe_all with prefix=%s", self._log_prefix, self._prefix)
        containers = await self._ic.list_all(self._prefix)
        volumes = await self._sp.list_volumes(self._prefix)
        archives = await self._sp.list_archives(self._prefix)
        logger.debug(
            "[%s] API results: containers=%d, volumes=%d, archives=%d",
            self._log_prefix, len(containers), len(volumes), len(archives)
        )

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
    WAKE_TARGET = WakeTarget.OB

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        notify: NotifySubscriber,
        instance_controller: InstanceController,
        storage_provider: StorageProvider,
    ) -> None:
        super().__init__(conn, leader, notify)
        self._observer = BulkObserver(instance_controller, storage_provider)

    async def tick(self) -> None:
        """Observe all resources and persist conditions to DB."""
        logger.debug("[%s] tick() started", self.name)
        now = datetime.now(UTC)

        # 1. Bulk observe all resources
        observed = await self._observer.observe_all()
        logger.debug("[%s] Observed %d workspaces from infra", self.name, len(observed))

        if not observed:
            logger.debug("[%s] No observed resources, skipping DB update", self.name)
            return

        # 2. Load existing workspaces from DB
        ws_ids = await self._load_workspace_ids()
        logger.debug("[%s] DB workspace IDs: %s", self.name, ws_ids)
        logger.debug("[%s] Observed workspace IDs: %s", self.name, set(observed.keys()))

        # 3. Filter to existing workspaces only
        updates_to_apply: list[tuple[str, dict, datetime]] = []
        for ws_id, conditions in observed.items():
            if ws_id not in ws_ids:
                logger.warning("[%s] Skipping orphan ws_id=%s (not in DB)", self.name, ws_id)
                continue
            updates_to_apply.append((ws_id, conditions, now))

        logger.debug("[%s] Updates to apply: %d", self.name, len(updates_to_apply))

        # 4. Update conditions for each workspace
        if updates_to_apply:
            count = await self._bulk_update_conditions(updates_to_apply)
            # Commit at connection level
            await self._conn.commit()
            logger.info("[%s] Committed %d updates to DB", self.name, count)
        else:
            logger.debug("[%s] No updates to apply", self.name)

    # =================================================================
    # DB Operations (Observer-owned columns: conditions, observed_at)
    # =================================================================

    async def _load_workspace_ids(self) -> set[str]:
        """Load all non-deleted workspace IDs."""
        result = await self._conn.execute(
            select(Workspace.id).where(Workspace.deleted_at.is_(None))
        )
        return {str(row[0]) for row in result.fetchall()}

    async def _bulk_update_conditions(
        self,
        updates: list[tuple[str, dict, datetime]],
    ) -> int:
        """Bulk update conditions for multiple workspaces.

        Args:
            updates: List of (workspace_id, conditions, observed_at)

        Returns:
            Number of rows updated
        """
        count = 0
        for ws_id, conditions, observed_at in updates:
            stmt = (
                update(Workspace)
                .where(Workspace.id == ws_id)
                .values(conditions=conditions, observed_at=observed_at)
            )
            result = await self._conn.execute(stmt)
            count += result.rowcount
        return count
