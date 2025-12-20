"""Startup recovery for workspaces stuck in transitional states.

Handles crash recovery by reconciling DB state with actual instance state.
Called during server startup before accepting API requests.
"""

import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Workspace, WorkspaceStatus
from app.db.models import utc_now
from app.services.instance.interface import InstanceController
from app.services.storage.interface import StorageProvider

logger = logging.getLogger(__name__)

# Transitional states that need recovery
TRANSITIONAL_STATES = {
    WorkspaceStatus.PROVISIONING,
    WorkspaceStatus.STOPPING,
    WorkspaceStatus.DELETING,
}


async def startup_recovery(
    session: AsyncSession,
    instance_controller: InstanceController,
    storage_provider: StorageProvider,
) -> int:
    """Recover workspaces stuck in transitional states.

    Runs at server startup to reconcile DB state with actual instance state.
    For each stuck workspace, queries the instance controller and updates
    the DB to a terminal state.

    Recovery Matrix:
    | DB Status     | Instance State         | Result   |
    |---------------|------------------------|----------|
    | PROVISIONING  | running + healthy      | RUNNING  |
    | PROVISIONING  | otherwise              | ERROR    |
    | STOPPING      | not running            | STOPPED  |
    | STOPPING      | running                | RUNNING  |
    | DELETING      | not exists             | DELETED  |
    | DELETING      | exists                 | ERROR    |

    Returns:
        Number of workspaces recovered.
    """
    # Find all workspaces in transitional states
    result = await session.execute(
        select(Workspace).where(
            Workspace.status.in_([s.value for s in TRANSITIONAL_STATES])  # type: ignore[attr-defined]
        )
    )
    stuck_workspaces = list(result.scalars().all())

    if not stuck_workspaces:
        logger.info("No workspaces in transitional states, skipping recovery")
        return 0

    logger.info(
        "Found %d workspace(s) in transitional states, starting recovery",
        len(stuck_workspaces),
    )

    recovered_count = 0

    for ws in stuck_workspaces:
        try:
            recovered = await _recover_workspace(
                session, ws, instance_controller, storage_provider
            )
            if recovered:
                recovered_count += 1
        except Exception as e:
            logger.exception(
                "Failed to recover workspace %s (status=%s): %s",
                ws.id,
                ws.status.value,
                e,
            )
            # Continue with other workspaces even if one fails
            continue

    logger.info(
        "Recovery complete: %d/%d workspace(s) recovered",
        recovered_count,
        len(stuck_workspaces),
    )

    return recovered_count


async def _recover_workspace(
    session: AsyncSession,
    workspace: Workspace,
    instance_controller: InstanceController,
    storage_provider: StorageProvider,
) -> bool:
    """Recover a single workspace from transitional state.

    Returns True if workspace was successfully recovered.
    """
    logger.info(
        "Recovering workspace %s from %s state",
        workspace.id,
        workspace.status.value,
    )

    # Query actual instance state
    instance_status = await instance_controller.get_status(workspace.id)
    logger.debug(
        "Workspace %s instance status: exists=%s, running=%s, healthy=%s",
        workspace.id,
        instance_status.exists,
        instance_status.running,
        instance_status.healthy,
    )

    # Determine new status and actions based on recovery matrix
    new_status: WorkspaceStatus
    clear_home_ctx = False
    set_deleted_at = False

    if workspace.status == WorkspaceStatus.PROVISIONING:
        if instance_status.running and instance_status.healthy:
            new_status = WorkspaceStatus.RUNNING
            logger.info(
                "Workspace %s: PROVISIONING -> RUNNING (container healthy)",
                workspace.id,
            )
        else:
            new_status = WorkspaceStatus.ERROR
            logger.warning(
                "Workspace %s: PROVISIONING -> ERROR (container not healthy)",
                workspace.id,
            )

    elif workspace.status == WorkspaceStatus.STOPPING:
        if not instance_status.running:
            new_status = WorkspaceStatus.STOPPED
            clear_home_ctx = True
            logger.info(
                "Workspace %s: STOPPING -> STOPPED (container stopped)",
                workspace.id,
            )
        else:
            # Container still running, revert to RUNNING for retry
            new_status = WorkspaceStatus.RUNNING
            logger.warning(
                "Workspace %s: STOPPING -> RUNNING (container still running)",
                workspace.id,
            )

    elif workspace.status == WorkspaceStatus.DELETING:
        if not instance_status.exists:
            new_status = WorkspaceStatus.DELETED
            clear_home_ctx = True
            set_deleted_at = True
            logger.info(
                "Workspace %s: DELETING -> DELETED (container deleted)",
                workspace.id,
            )
        else:
            new_status = WorkspaceStatus.ERROR
            logger.warning(
                "Workspace %s: DELETING -> ERROR (container still exists)",
                workspace.id,
            )

    else:
        # Should not happen, but handle gracefully
        logger.error(
            "Unexpected workspace status during recovery: %s",
            workspace.status.value,
        )
        return False

    # If we need to clear home_ctx, call deprovision first
    if clear_home_ctx and workspace.home_ctx:
        try:
            logger.info(
                "Deprovisioning storage for workspace %s (home_ctx cleanup)",
                workspace.id,
            )
            await storage_provider.deprovision(workspace.home_ctx)
        except Exception as e:
            logger.warning(
                "Failed to deprovision storage for workspace %s: %s (continuing)",
                workspace.id,
                e,
            )
            # Continue with state update even if deprovision fails

    # Build update values
    now = utc_now()
    update_values: dict[str, object] = {
        "status": new_status,
        "updated_at": now,
    }

    if clear_home_ctx:
        update_values["home_ctx"] = None

    if set_deleted_at:
        update_values["deleted_at"] = now

    # Update workspace state
    await session.execute(
        update(Workspace)
        .where(Workspace.id == workspace.id)  # type: ignore[arg-type]
        .values(**update_values)
    )
    await session.commit()

    logger.info(
        "Workspace %s recovered: %s -> %s",
        workspace.id,
        workspace.status.value,
        new_status.value,
    )

    return True
