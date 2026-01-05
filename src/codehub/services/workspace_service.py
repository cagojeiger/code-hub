"""Workspace service for CRUD and state management."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from codehub.app.config import get_settings
from codehub.core.domain import DesiredState, Operation, Phase
from codehub.core.errors import (
    ForbiddenError,
    RunningLimitExceededError,
    WorkspaceNotFoundError,
)
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
        last_access_at=now,  # TTL 규칙 적용 위해 생성 시점 설정
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


async def count_running_workspaces(db: AsyncSession, user_id: str) -> int:
    """Count RUNNING or starting workspaces for a user.

    Counts workspaces that are either:
    - Already running (phase=RUNNING)
    - Requested to start (desired_state=RUNNING)

    Args:
        db: Database session
        user_id: Owner user ID

    Returns:
        Number of RUNNING or starting workspaces
    """
    stmt = select(func.count()).select_from(Workspace).where(
        Workspace.owner_user_id == user_id,
        Workspace.deleted_at.is_(None),
        or_(
            Workspace.phase == Phase.RUNNING.value,
            Workspace.desired_state == DesiredState.RUNNING.value,
        ),
    )
    result = await db.execute(stmt)
    return result.scalar() or 0


async def list_running_workspaces(db: AsyncSession, user_id: str) -> list[Workspace]:
    """List RUNNING workspaces for a user.

    Args:
        db: Database session
        user_id: Owner user ID

    Returns:
        List of RUNNING workspaces
    """
    stmt = (
        select(Workspace)
        .where(
            Workspace.owner_user_id == user_id,
            Workspace.phase == Phase.RUNNING.value,
            Workspace.deleted_at.is_(None),
        )
        .order_by(Workspace.created_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def set_desired_state(
    db: AsyncSession,
    workspace_id: str,
    desired_state: DesiredState,
) -> None:
    """Set desired_state for a workspace (internal use, no ownership check).

    Args:
        db: Database session
        workspace_id: Workspace ID
        desired_state: New desired state
    """
    stmt = select(Workspace).where(
        Workspace.id == workspace_id,
        Workspace.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    workspace = result.scalar_one_or_none()

    if workspace is None:
        raise WorkspaceNotFoundError()

    workspace.desired_state = desired_state.value
    workspace.updated_at = datetime.now(UTC)

    await db.commit()


async def _can_start_workspace(db: AsyncSession, user_id: str) -> bool:
    """Check if user can start a new workspace.

    Args:
        db: Database session
        user_id: Owner user ID

    Returns:
        True if user hasn't exceeded running limit
    """
    count = await count_running_workspaces(db, user_id)
    return count < _settings.limits.max_running_per_user


async def request_start(
    db: AsyncSession,
    workspace_id: str,
    user_id: str,
) -> Workspace:
    """Request to start a workspace - single entry point.

    All start paths (API, Proxy) must go through this function.
    - Idempotent: returns workspace if already starting
    - Raises RunningLimitExceededError if limit exceeded

    Args:
        db: Database session
        workspace_id: Workspace ID
        user_id: Owner user ID (for verification)

    Returns:
        Workspace with desired_state=RUNNING

    Raises:
        WorkspaceNotFoundError: If workspace not found
        ForbiddenError: If user doesn't own the workspace
        RunningLimitExceededError: If running limit exceeded
    """
    workspace = await get_workspace(db, workspace_id, user_id)

    # Idempotent: already starting
    if workspace.desired_state == DesiredState.RUNNING.value:
        return workspace

    # Check running limit
    if not await _can_start_workspace(db, user_id):
        raise RunningLimitExceededError()

    # Update desired_state
    workspace.desired_state = DesiredState.RUNNING.value
    workspace.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(workspace)

    return workspace
