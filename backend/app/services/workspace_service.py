"""Workspace service for orchestrating workspace lifecycle operations.

Handles the business logic for CRUD operations and lifecycle management.
Coordinates between Storage Provider, Instance Controller, and DB.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from app.core.config import get_settings
from app.core.errors import InvalidStateError, WorkspaceNotFoundError
from app.core.events import notify_workspace_deleted, notify_workspace_updated
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


# =============================================================================
# Helper Functions
# =============================================================================


@asynccontextmanager
async def _background_session() -> AsyncGenerator[AsyncSession]:
    """Create a new session for background tasks.

    Background tasks need their own session since they run outside
    the request lifecycle and the request session is already closed.
    """
    engine = get_engine()
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


async def _cas_transition(
    session: AsyncSession,
    workspace: Workspace,
    from_states: set[WorkspaceStatus],
    to_state: WorkspaceStatus,
    action_name: str,
) -> None:
    """Compare-and-swap state transition with optimistic locking.

    Atomically updates workspace status if current status is in from_states.
    Raises InvalidStateError if the state changed during the operation.

    Args:
        session: Database session
        workspace: Workspace to transition
        from_states: Allowed source states
        to_state: Target state
        action_name: Action name for error message (e.g., "delete", "start")
    """
    result = await session.execute(
        update(Workspace)
        .where(
            col(Workspace.id) == workspace.id,
            col(Workspace.status).in_([s.value for s in from_states]),
        )
        .values(status=to_state, updated_at=utc_now())
    )

    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise InvalidStateError(
            f"Workspace state changed during {action_name} operation"
        )

    await session.commit()
    await session.refresh(workspace)


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

    @staticmethod
    async def _get_workspace_by_id(
        session: AsyncSession, workspace_id: str
    ) -> Workspace | None:
        """Get workspace by ID for SSE notifications."""
        result = await session.execute(
            select(Workspace).where(col(Workspace.id) == workspace_id)
        )
        return result.scalar_one_or_none()

    async def list_workspaces(
        self,
        session: AsyncSession,
        user_id: str,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Workspace], int]:
        """List workspaces owned by user with pagination.

        Args:
            session: Database session
            user_id: Owner user ID
            page: Page number (1-indexed)
            per_page: Items per page

        Returns:
            Tuple of (workspaces list, total count)
        """
        from sqlalchemy import func

        base_filter = [
            col(Workspace.owner_user_id) == user_id,
            col(Workspace.deleted_at).is_(None),
        ]

        count_result = await session.execute(
            select(func.count()).select_from(Workspace).where(*base_filter)
        )
        total = count_result.scalar() or 0

        offset = (page - 1) * per_page
        result = await session.execute(
            select(Workspace)
            .where(*base_filter)
            .order_by(col(Workspace.created_at).desc())
            .offset(offset)
            .limit(per_page)
        )
        workspaces = list(result.scalars().all())

        return workspaces, total

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

    async def initiate_delete(
        self,
        session: AsyncSession,
        user_id: str,
        workspace_id: str,
    ) -> Workspace:
        """Initiate workspace delete (CAS transition to DELETING).

        Only allowed in CREATED, STOPPED, or ERROR state.
        Returns workspace with DELETING status.
        Caller should schedule background task for actual deletion.
        """
        workspace = await self.get_workspace(session, user_id, workspace_id)

        if workspace.status not in DELETABLE_STATES:
            raise InvalidStateError(
                f"Cannot delete workspace in {workspace.status.value} state. "
                f"Allowed states: {', '.join(s.value for s in DELETABLE_STATES)}"
            )

        await _cas_transition(
            session, workspace, DELETABLE_STATES, WorkspaceStatus.DELETING, "delete"
        )
        return workspace

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

        await _cas_transition(
            session, workspace, STARTABLE_STATES, WorkspaceStatus.PROVISIONING, "start"
        )
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

        await _cas_transition(
            session, workspace, STOPPABLE_STATES, WorkspaceStatus.STOPPING, "stop"
        )
        return workspace

    async def start_workspace(
        self,
        workspace_id: str,
        home_store_key: str,
        existing_ctx: str | None,
        image_ref: str,
        owner_user_id: str,
    ) -> None:
        """Start a workspace (background task).

        Flow:
        1. Storage Provider.Provision(home_store_key, existing_ctx)
        2. Save home_ctx to DB
        3. Instance Controller.StartWorkspace(workspace_id, image_ref, home_mount)
        4. Poll GetStatus until healthy or timeout
        5. Update status to RUNNING or ERROR
        6. Notify SSE clients
        """
        settings = get_settings()

        async with _background_session() as session:
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
                        # Notify SSE clients
                        workspace = await self._get_workspace_by_id(
                            session, workspace_id
                        )
                        if workspace:
                            await notify_workspace_updated(
                                workspace, settings.server.public_base_url
                            )
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
                # Notify SSE clients
                workspace = await self._get_workspace_by_id(session, workspace_id)
                if workspace:
                    await notify_workspace_updated(
                        workspace, settings.server.public_base_url
                    )

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
                # Notify SSE clients
                workspace = await self._get_workspace_by_id(session, workspace_id)
                if workspace:
                    await notify_workspace_updated(
                        workspace, settings.server.public_base_url
                    )

    async def stop_workspace(
        self,
        workspace_id: str,
        home_ctx: str | None,
        owner_user_id: str,
    ) -> None:
        """Stop a workspace (background task).

        Flow:
        1. Instance Controller.StopWorkspace(workspace_id)
        2. Storage Provider.Deprovision(home_ctx)
        3. Clear home_ctx in DB
        4. Update status to STOPPED or ERROR
        5. Notify SSE clients
        """
        settings = get_settings()

        async with _background_session() as session:
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
                # Notify SSE clients
                workspace = await self._get_workspace_by_id(session, workspace_id)
                if workspace:
                    await notify_workspace_updated(
                        workspace, settings.server.public_base_url
                    )

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
                # Notify SSE clients
                workspace = await self._get_workspace_by_id(session, workspace_id)
                if workspace:
                    await notify_workspace_updated(
                        workspace, settings.server.public_base_url
                    )

    async def delete_workspace(
        self,
        workspace_id: str,
        home_ctx: str | None,
        owner_user_id: str,
    ) -> None:
        """Delete a workspace (background task).

        Flow:
        1. Instance Controller.DeleteWorkspace(workspace_id)
        2. Storage Provider.Deprovision(home_ctx) if home_ctx exists
        3. Clear home_ctx in DB
        4. Soft delete (status = DELETED, deleted_at = now)
        5. On failure: status = ERROR
        6. Notify SSE clients
        """
        settings = get_settings()

        async with _background_session() as session:
            try:
                # Step 1: Delete container
                logger.info("Deleting container for workspace %s", workspace_id)
                await self._instance.delete_workspace(workspace_id)

                # Step 2: Deprovision storage (no-op for local-dir)
                if home_ctx:
                    logger.info("Deprovisioning storage for workspace %s", workspace_id)
                    await self._storage.deprovision(home_ctx)

                # Step 3 & 4: Clear home_ctx and soft delete
                now = utc_now()
                logger.info("Workspace %s is now DELETED", workspace_id)
                await session.execute(
                    update(Workspace)
                    .where(col(Workspace.id) == workspace_id)
                    .values(
                        status=WorkspaceStatus.DELETED,
                        home_ctx=None,
                        deleted_at=now,
                        updated_at=now,
                    )
                )
                await session.commit()
                # Notify SSE clients
                await notify_workspace_deleted(workspace_id, owner_user_id)

            except Exception as e:
                # Any error - update to ERROR
                logger.exception("Failed to delete workspace %s: %s", workspace_id, e)
                await session.execute(
                    update(Workspace)
                    .where(col(Workspace.id) == workspace_id)
                    .values(
                        status=WorkspaceStatus.ERROR,
                        updated_at=utc_now(),
                    )
                )
                await session.commit()
                # Notify SSE clients (ERROR state)
                workspace = await self._get_workspace_by_id(session, workspace_id)
                if workspace:
                    await notify_workspace_updated(
                        workspace, settings.server.public_base_url
                    )
