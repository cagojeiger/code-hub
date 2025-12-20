"""Workspace service for orchestrating workspace lifecycle operations.

Handles the business logic for CRUD operations and lifecycle management.
Coordinates between Storage Provider, Instance Controller, and DB.
"""

import asyncio
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.core.config import get_settings
from app.core.errors import InvalidStateError, WorkspaceNotFoundError
from app.db import Workspace, WorkspaceStatus, get_engine
from app.db.models import generate_ulid, utc_now
from app.services.instance.interface import InstanceController
from app.services.storage.interface import StorageProvider

logger = logging.getLogger(__name__)

# States that allow deletion
DELETABLE_STATES = {
    WorkspaceStatus.CREATED,
    WorkspaceStatus.STOPPED,
    WorkspaceStatus.ERROR,
}

# States that allow starting
STARTABLE_STATES = {
    WorkspaceStatus.CREATED,
    WorkspaceStatus.STOPPED,
    WorkspaceStatus.ERROR,
}

# States that allow stopping
STOPPABLE_STATES = {
    WorkspaceStatus.RUNNING,
    WorkspaceStatus.ERROR,
}


class WorkspaceService:
    """Service for workspace CRUD and lifecycle operations."""

    def __init__(
        self,
        storage: StorageProvider,
        instance: InstanceController,
    ) -> None:
        self._storage = storage
        self._instance = instance

    @staticmethod
    def _build_home_store_key(user_id: str, workspace_id: str) -> str:
        """Build home store key from user and workspace IDs."""
        return f"users/{user_id}/workspaces/{workspace_id}/home"

    async def list_workspaces(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> list[Workspace]:
        """List workspaces owned by user."""
        result = await session.execute(
            select(Workspace).where(
                col(Workspace.owner_user_id) == user_id,
                col(Workspace.deleted_at).is_(None),
            )
        )
        return list(result.scalars().all())

    async def create_workspace(
        self,
        session: AsyncSession,
        user_id: str,
        name: str,
        description: str | None = None,
        memo: str | None = None,
    ) -> Workspace:
        """Create a new workspace."""
        settings = get_settings()

        workspace_id = generate_ulid()
        home_store_key = self._build_home_store_key(user_id, workspace_id)

        workspace = Workspace(
            id=workspace_id,
            owner_user_id=user_id,
            name=name,
            description=description,
            memo=memo,
            status=WorkspaceStatus.CREATED,
            image_ref=settings.workspace.default_image,
            instance_backend="local-docker",
            storage_backend=settings.home_store.backend,
            home_store_key=home_store_key,
            home_ctx=None,
        )

        session.add(workspace)
        await session.commit()
        await session.refresh(workspace)

        return workspace

    async def get_workspace(
        self,
        session: AsyncSession,
        user_id: str,
        workspace_id: str,
    ) -> Workspace:
        """Get workspace by ID. Raises WorkspaceNotFoundError if not found."""
        result = await session.execute(
            select(Workspace).where(
                col(Workspace.id) == workspace_id,
                col(Workspace.owner_user_id) == user_id,
                col(Workspace.deleted_at).is_(None),
            )
        )
        workspace = result.scalar_one_or_none()
        if workspace is None:
            raise WorkspaceNotFoundError()
        return workspace

    async def update_workspace(
        self,
        session: AsyncSession,
        user_id: str,
        workspace_id: str,
        name: str | None = None,
        description: str | None = None,
        memo: str | None = None,
    ) -> Workspace:
        """Update workspace metadata."""
        workspace = await self.get_workspace(session, user_id, workspace_id)

        # Build update data from non-None values
        update_data: dict[str, str | None] = {}
        if name is not None:
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description
        if memo is not None:
            update_data["memo"] = memo

        if update_data:
            for key, value in update_data.items():
                setattr(workspace, key, value)
            workspace.updated_at = utc_now()
            await session.commit()
            await session.refresh(workspace)

        return workspace

    async def delete_workspace(
        self,
        session: AsyncSession,
        user_id: str,
        workspace_id: str,
    ) -> None:
        """Delete workspace (soft delete).

        Only allowed in CREATED, STOPPED, or ERROR state.
        Uses CAS pattern to prevent race conditions.
        """
        # First verify workspace exists and is owned by current user
        workspace = await self.get_workspace(session, user_id, workspace_id)

        if workspace.status not in DELETABLE_STATES:
            raise InvalidStateError(
                f"Cannot delete workspace in {workspace.status.value} state. "
                f"Allowed states: {', '.join(s.value for s in DELETABLE_STATES)}"
            )

        # CAS update: atomically change to DELETING
        now = utc_now()
        result = await session.execute(
            update(Workspace)
            .where(
                col(Workspace.id) == workspace_id,
                col(Workspace.status).in_([s.value for s in DELETABLE_STATES]),
            )
            .values(
                status=WorkspaceStatus.DELETING,
                updated_at=now,
            )
        )

        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise InvalidStateError("Workspace state changed during delete operation")

        # For M4 CRUD API, we do synchronous soft delete
        # Full delete flow (Instance Controller + Storage) is in DeleteWorkspace API task
        await session.execute(
            update(Workspace)
            .where(col(Workspace.id) == workspace_id)
            .values(
                status=WorkspaceStatus.DELETED,
                deleted_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    async def initiate_start(
        self,
        session: AsyncSession,
        user_id: str,
        workspace_id: str,
    ) -> Workspace:
        """Initiate workspace start (CAS transition to PROVISIONING).

        Only allowed in CREATED, STOPPED, or ERROR state.
        Returns workspace with PROVISIONING status.
        Caller should schedule background task for actual provisioning.
        """
        workspace = await self.get_workspace(session, user_id, workspace_id)

        if workspace.status not in STARTABLE_STATES:
            raise InvalidStateError(
                f"Cannot start workspace in {workspace.status.value} state. "
                f"Allowed states: {', '.join(s.value for s in STARTABLE_STATES)}"
            )

        # CAS update: atomically change to PROVISIONING
        result = await session.execute(
            update(Workspace)
            .where(
                col(Workspace.id) == workspace_id,
                col(Workspace.status).in_([s.value for s in STARTABLE_STATES]),
            )
            .values(
                status=WorkspaceStatus.PROVISIONING,
                updated_at=utc_now(),
            )
        )

        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise InvalidStateError("Workspace state changed during start operation")

        await session.commit()
        await session.refresh(workspace)

        return workspace

    async def initiate_stop(
        self,
        session: AsyncSession,
        user_id: str,
        workspace_id: str,
    ) -> Workspace:
        """Initiate workspace stop (CAS transition to STOPPING).

        Only allowed in RUNNING or ERROR state.
        Returns workspace with STOPPING status.
        Caller should schedule background task for actual stopping.
        """
        workspace = await self.get_workspace(session, user_id, workspace_id)

        if workspace.status not in STOPPABLE_STATES:
            raise InvalidStateError(
                f"Cannot stop workspace in {workspace.status.value} state. "
                f"Allowed states: {', '.join(s.value for s in STOPPABLE_STATES)}"
            )

        # CAS update: atomically change to STOPPING
        result = await session.execute(
            update(Workspace)
            .where(
                col(Workspace.id) == workspace_id,
                col(Workspace.status).in_([s.value for s in STOPPABLE_STATES]),
            )
            .values(
                status=WorkspaceStatus.STOPPING,
                updated_at=utc_now(),
            )
        )

        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise InvalidStateError("Workspace state changed during stop operation")

        await session.commit()
        await session.refresh(workspace)

        return workspace

    async def start_workspace(
        self,
        workspace_id: str,
        home_store_key: str,
        existing_ctx: str | None,
        image_ref: str,
    ) -> None:
        """Start a workspace (background task).

        Flow:
        1. Storage Provider.Provision(home_store_key, existing_ctx)
        2. Save home_ctx to DB
        3. Instance Controller.StartWorkspace(workspace_id, image_ref, home_mount)
        4. Poll GetStatus until healthy or timeout
        5. Update status to RUNNING or ERROR
        """
        settings = get_settings()
        engine = get_engine()
        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        async with session_factory() as session:
            try:
                # Step 1: Provision storage
                logger.info("Provisioning storage for workspace %s", workspace_id)
                provision_result = await self._storage.provision(
                    home_store_key, existing_ctx
                )

                # Step 2: Save home_ctx to DB
                await session.execute(
                    update(Workspace)
                    .where(col(Workspace.id) == workspace_id)
                    .values(home_ctx=provision_result.home_ctx, updated_at=utc_now())
                )
                await session.commit()

                # Step 3: Start container
                logger.info(
                    "Starting container for workspace %s (image=%s, home=%s)",
                    workspace_id,
                    image_ref,
                    provision_result.home_mount,
                )
                await self._instance.start_workspace(
                    workspace_id=workspace_id,
                    image_ref=image_ref,
                    home_mount=provision_result.home_mount,
                )

                # Step 4: Poll for healthy status
                interval = settings.workspace.healthcheck.interval_seconds()
                timeout = settings.workspace.healthcheck.timeout_seconds()
                elapsed = 0.0

                while elapsed < timeout:
                    await asyncio.sleep(interval)
                    elapsed += interval

                    status_result = await self._instance.get_status(workspace_id)
                    logger.debug(
                        "Workspace %s status: exists=%s, running=%s, healthy=%s",
                        workspace_id,
                        status_result.exists,
                        status_result.running,
                        status_result.healthy,
                    )

                    if status_result.running and status_result.healthy:
                        # Step 5a: Success - update to RUNNING
                        logger.info("Workspace %s is now RUNNING", workspace_id)
                        await session.execute(
                            update(Workspace)
                            .where(col(Workspace.id) == workspace_id)
                            .values(
                                status=WorkspaceStatus.RUNNING,
                                updated_at=utc_now(),
                            )
                        )
                        await session.commit()
                        return

                # Step 5b: Timeout - update to ERROR
                logger.warning(
                    "Workspace %s failed to become healthy within %s seconds",
                    workspace_id,
                    timeout,
                )
                await session.execute(
                    update(Workspace)
                    .where(col(Workspace.id) == workspace_id)
                    .values(
                        status=WorkspaceStatus.ERROR,
                        updated_at=utc_now(),
                    )
                )
                await session.commit()

            except Exception as e:
                # Any error - update to ERROR
                logger.exception("Failed to start workspace %s: %s", workspace_id, e)
                await session.execute(
                    update(Workspace)
                    .where(col(Workspace.id) == workspace_id)
                    .values(
                        status=WorkspaceStatus.ERROR,
                        updated_at=utc_now(),
                    )
                )
                await session.commit()

    async def stop_workspace(
        self,
        workspace_id: str,
        home_ctx: str | None,
    ) -> None:
        """Stop a workspace (background task).

        Flow:
        1. Instance Controller.StopWorkspace(workspace_id)
        2. Storage Provider.Deprovision(home_ctx)
        3. Clear home_ctx in DB
        4. Update status to STOPPED or ERROR
        """
        engine = get_engine()
        session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        async with session_factory() as session:
            try:
                # Step 1: Stop container
                logger.info("Stopping container for workspace %s", workspace_id)
                await self._instance.stop_workspace(workspace_id)

                # Step 2: Deprovision storage (no-op for local-dir)
                logger.info("Deprovisioning storage for workspace %s", workspace_id)
                await self._storage.deprovision(home_ctx)

                # Step 3 & 4: Clear home_ctx and update status to STOPPED
                logger.info("Workspace %s is now STOPPED", workspace_id)
                await session.execute(
                    update(Workspace)
                    .where(col(Workspace.id) == workspace_id)
                    .values(
                        status=WorkspaceStatus.STOPPED,
                        home_ctx=None,
                        updated_at=utc_now(),
                    )
                )
                await session.commit()

            except Exception as e:
                # Any error - update to ERROR
                logger.exception("Failed to stop workspace %s: %s", workspace_id, e)
                await session.execute(
                    update(Workspace)
                    .where(col(Workspace.id) == workspace_id)
                    .values(
                        status=WorkspaceStatus.ERROR,
                        updated_at=utc_now(),
                    )
                )
                await session.commit()
