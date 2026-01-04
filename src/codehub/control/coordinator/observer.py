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
        """Observe all resources and persist conditions to DB.

        DB-based iteration: 모든 워크스페이스를 업데이트 (리소스 유무 무관)
        - 리소스가 있는 워크스페이스: 관측된 conditions
        - 리소스가 없는 워크스페이스: empty conditions (container/volume/archive = None)
        """
        logger.debug("[%s] tick() started", self.name)
        now = datetime.now(UTC)

        # 1. Load ALL workspaces from DB first (DB-based iteration)
        ws_ids = await self._load_workspace_ids()
        if not ws_ids:
            logger.debug("[%s] No workspaces in DB, skipping", self.name)
            return

        # 2. Bulk observe resources (리소스가 있는 것만 반환)
        observed = await self._observer.observe_all()
        logger.debug(
            "[%s] DB workspaces=%d, observed=%d",
            self.name, len(ws_ids), len(observed)
        )

        # 3. Build updates for ALL workspaces (DB-based)
        empty_conditions = {"container": None, "volume": None, "archive": None}
        updates_to_apply: list[tuple[str, dict, datetime]] = []

        for ws_id in ws_ids:
            if ws_id in observed:
                updates_to_apply.append((ws_id, observed[ws_id], now))
            else:
                # 리소스 없는 워크스페이스 → empty conditions
                updates_to_apply.append((ws_id, empty_conditions, now))

        # 4. Orphan warning (DB에 없는데 리소스는 있는 경우 - GC 대상)
        orphan_ws_ids = set(observed.keys()) - ws_ids
        for ws_id in orphan_ws_ids:
            logger.warning("[%s] Orphan ws_id=%s (not in DB, GC target)", self.name, ws_id)

        # 5. Bulk update conditions
        if updates_to_apply:
            count = await self._bulk_update_conditions(updates_to_apply)
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
