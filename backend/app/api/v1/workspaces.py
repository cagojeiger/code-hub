"""Workspace CRUD API endpoints.

Endpoints:
- GET /api/v1/workspaces - List workspaces (owner only)
- POST /api/v1/workspaces - Create workspace
- GET /api/v1/workspaces/{id} - Get workspace detail
- PATCH /api/v1/workspaces/{id} - Update workspace
- DELETE /api/v1/workspaces/{id} - Delete workspace (CREATED/STOPPED/ERROR only)
"""

from datetime import datetime

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlmodel import col

from app.api.v1.dependencies import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.errors import InvalidStateError, WorkspaceNotFoundError
from app.db import Workspace, WorkspaceStatus
from app.db.models import generate_ulid, utc_now

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    """Request schema for creating a workspace."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    memo: str | None = Field(default=None)


class WorkspaceUpdate(BaseModel):
    """Request schema for updating a workspace."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    memo: str | None = Field(default=None)


class WorkspaceResponse(BaseModel):
    """Response schema for workspace."""

    id: str
    name: str
    description: str | None
    memo: str | None
    status: WorkspaceStatus
    url: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


def _build_workspace_url(workspace_id: str) -> str:
    """Build workspace URL from workspace ID."""
    settings = get_settings()
    return f"{settings.server.public_base_url}/w/{workspace_id}/"


def _workspace_to_response(workspace: Workspace) -> WorkspaceResponse:
    """Convert Workspace model to response schema."""
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        description=workspace.description,
        memo=workspace.memo,
        status=workspace.status,
        url=_build_workspace_url(workspace.id),
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def _build_home_store_key(user_id: str, workspace_id: str) -> str:
    """Build home store key from user and workspace IDs."""
    return f"users/{user_id}/workspaces/{workspace_id}/home"


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    session: DbSession,
    current_user: CurrentUser,
) -> list[WorkspaceResponse]:
    """List workspaces owned by current user."""
    result = await session.execute(
        select(Workspace).where(
            col(Workspace.owner_user_id) == current_user.id,
            col(Workspace.deleted_at).is_(None),
        )
    )
    workspaces = result.scalars().all()
    return [_workspace_to_response(ws) for ws in workspaces]


@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    body: WorkspaceCreate,
    session: DbSession,
    current_user: CurrentUser,
) -> WorkspaceResponse:
    """Create a new workspace."""
    settings = get_settings()

    workspace_id = generate_ulid()
    home_store_key = _build_home_store_key(current_user.id, workspace_id)

    workspace = Workspace(
        id=workspace_id,
        owner_user_id=current_user.id,
        name=body.name,
        description=body.description,
        memo=body.memo,
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

    return _workspace_to_response(workspace)


async def _get_workspace_or_404(
    session: DbSession,
    workspace_id: str,
    current_user: CurrentUser,
) -> Workspace:
    """Get workspace by ID or raise 404."""
    result = await session.execute(
        select(Workspace).where(
            col(Workspace.id) == workspace_id,
            col(Workspace.owner_user_id) == current_user.id,
            col(Workspace.deleted_at).is_(None),
        )
    )
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFoundError()
    return workspace


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    session: DbSession,
    current_user: CurrentUser,
) -> WorkspaceResponse:
    """Get workspace by ID."""
    workspace = await _get_workspace_or_404(session, workspace_id, current_user)
    return _workspace_to_response(workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    body: WorkspaceUpdate,
    session: DbSession,
    current_user: CurrentUser,
) -> WorkspaceResponse:
    """Update workspace metadata."""
    workspace = await _get_workspace_or_404(session, workspace_id, current_user)

    update_data = body.model_dump(exclude_unset=True)
    if update_data:
        update_data["updated_at"] = utc_now()
        for key, value in update_data.items():
            setattr(workspace, key, value)
        await session.commit()
        await session.refresh(workspace)

    return _workspace_to_response(workspace)


DELETABLE_STATES = {
    WorkspaceStatus.CREATED,
    WorkspaceStatus.STOPPED,
    WorkspaceStatus.ERROR,
}


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: str,
    session: DbSession,
    current_user: CurrentUser,
) -> Response:
    """Delete workspace (soft delete).

    Only allowed in CREATED, STOPPED, or ERROR state.
    Uses CAS pattern to prevent race conditions.
    """
    # First verify workspace exists and is owned by current user
    workspace = await _get_workspace_or_404(session, workspace_id, current_user)

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

    return Response(status_code=status.HTTP_204_NO_CONTENT)
