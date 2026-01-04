"""Workspace service for CRUD and state management."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codehub.app.config import get_settings
from codehub.core.domain import DesiredState, Operation, Phase
from codehub.core.errors import ForbiddenError, WorkspaceNotFoundError
from codehub.core.models import Workspace

# Load settings once at module level
_settings = get_settings()


async def create_workspace(
    db: AsyncSession,
    user_id: str,
    name: str,
    description: str | None = None,
    image_ref: str | None = None,
) -> Workspace:
    """Create a new workspace.

    Args:
        db: Database session
        user_id: Owner user ID
        name: Workspace name
        description: Optional description
        image_ref: Container image reference

    Returns:
        Created workspace
    """
    workspace_id = str(uuid4())
    now = datetime.now(UTC)

    # Use default image if not provided
    final_image_ref = image_ref or _settings.docker.default_image
    resource_prefix = _settings.docker.resource_prefix

    workspace = Workspace(
        id=workspace_id,
        owner_user_id=user_id,
        name=name,
        description=description,
        image_ref=final_image_ref,
        instance_backend="local-docker",
        storage_backend="minio",
        home_store_key=f"{resource_prefix}{workspace_id}-home",
        phase=Phase.PENDING.value,
        operation=Operation.NONE.value,
        desired_state=DesiredState.RUNNING.value,
        created_at=now,
        updated_at=now,
    )

    db.add(workspace)
    await db.commit()
    await db.refresh(workspace)

    return workspace


async def get_workspace(
    db: AsyncSession,
    workspace_id: str,
    user_id: str | None = None,
) -> Workspace:
    """Get workspace by ID.

    Args:
        db: Database session
        workspace_id: Workspace ID
        user_id: If provided, verify ownership

    Returns:
        Workspace

    Raises:
        WorkspaceNotFoundError: If workspace not found
        ForbiddenError: If user doesn't own the workspace
    """
    stmt = select(Workspace).where(
        Workspace.id == workspace_id,
        Workspace.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    workspace = result.scalar_one_or_none()

    if workspace is None:
        raise WorkspaceNotFoundError()

    if user_id is not None and workspace.owner_user_id != user_id:
        raise ForbiddenError()

    return workspace


async def list_workspaces(
    db: AsyncSession,
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[Workspace]:
    """List workspaces for a user.

    Args:
        db: Database session
        user_id: Owner user ID
        limit: Max results
        offset: Offset for pagination

    Returns:
        List of workspaces
    """
    stmt = (
        select(Workspace)
        .where(
            Workspace.owner_user_id == user_id,
            Workspace.deleted_at.is_(None),
        )
        .order_by(Workspace.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def update_workspace(
    db: AsyncSession,
    workspace_id: str,
    user_id: str,
    name: str | None = None,
    description: str | None = None,
    memo: str | None = None,
    desired_state: DesiredState | None = None,
) -> Workspace:
    """Update workspace.

    Args:
        db: Database session
        workspace_id: Workspace ID
        user_id: Owner user ID (for verification)
        name: New name
        description: New description
        memo: New memo
        desired_state: New desired state

    Returns:
        Updated workspace
    """
    workspace = await get_workspace(db, workspace_id, user_id)

    if name is not None:
        workspace.name = name
    if description is not None:
        workspace.description = description
    if memo is not None:
        workspace.memo = memo
    if desired_state is not None:
        workspace.desired_state = desired_state.value

    workspace.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(workspace)

    return workspace


async def delete_workspace(
    db: AsyncSession,
    workspace_id: str,
    user_id: str,
) -> None:
    """Soft delete workspace.

    Sets deleted_at and desired_state to DELETED.
    - deleted_at: Immediately excludes from list queries
    - desired_state: Triggers reconciler to cleanup resources

    Args:
        db: Database session
        workspace_id: Workspace ID
        user_id: Owner user ID (for verification)
    """
    workspace = await get_workspace(db, workspace_id, user_id)

    now = datetime.now(UTC)
    workspace.deleted_at = now  # Soft delete (spec: API sets deleted_at)
    workspace.desired_state = DesiredState.DELETED.value
    workspace.updated_at = now

    await db.commit()
