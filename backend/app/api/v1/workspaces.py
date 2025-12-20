"""Workspace CRUD API endpoints.

Endpoints:
- GET /api/v1/workspaces - List workspaces (owner only)
- POST /api/v1/workspaces - Create workspace
- GET /api/v1/workspaces/{id} - Get workspace detail
- PATCH /api/v1/workspaces/{id} - Update workspace
- DELETE /api/v1/workspaces/{id} - Delete workspace (CREATED/STOPPED/ERROR only)
- POST /api/v1/workspaces/{id}:start - Start workspace
- POST /api/v1/workspaces/{id}:stop - Stop workspace
"""

import math
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Query, Response, status
from pydantic import BaseModel, Field

from app.api.v1.dependencies import CurrentUser, DbSession, WsService
from app.core.config import get_settings
from app.db import Workspace, WorkspaceStatus

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

# Pagination constants
DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


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


class WorkspaceActionResponse(BaseModel):
    """Response schema for workspace actions (start/stop)."""

    id: str
    status: WorkspaceStatus


class PaginationMeta(BaseModel):
    """Pagination metadata."""

    page: int
    per_page: int
    total: int
    total_pages: int
    has_next: bool
    has_prev: bool


class PaginatedWorkspaceResponse(BaseModel):
    """Paginated response for workspace list."""

    items: list[WorkspaceResponse]
    pagination: PaginationMeta


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


@router.get("", response_model=PaginatedWorkspaceResponse)
async def list_workspaces(
    session: DbSession,
    current_user: CurrentUser,
    ws_service: WsService,
    page: int = Query(default=DEFAULT_PAGE, ge=1, description="Page number"),
    per_page: int = Query(
        default=DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE, description="Items per page"
    ),
) -> PaginatedWorkspaceResponse:
    """List workspaces owned by current user with pagination."""
    workspaces, total = await ws_service.list_workspaces(
        session, current_user.id, page=page, per_page=per_page
    )

    total_pages = math.ceil(total / per_page) if total > 0 else 0

    return PaginatedWorkspaceResponse(
        items=[_workspace_to_response(ws) for ws in workspaces],
        pagination=PaginationMeta(
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    body: WorkspaceCreate,
    session: DbSession,
    current_user: CurrentUser,
    ws_service: WsService,
) -> WorkspaceResponse:
    """Create a new workspace."""
    workspace = await ws_service.create_workspace(
        session=session,
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        memo=body.memo,
    )
    return _workspace_to_response(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    session: DbSession,
    current_user: CurrentUser,
    ws_service: WsService,
) -> WorkspaceResponse:
    """Get workspace by ID."""
    workspace = await ws_service.get_workspace(session, current_user.id, workspace_id)
    return _workspace_to_response(workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    body: WorkspaceUpdate,
    session: DbSession,
    current_user: CurrentUser,
    ws_service: WsService,
) -> WorkspaceResponse:
    """Update workspace metadata."""
    update_data = body.model_dump(exclude_unset=True)
    workspace = await ws_service.update_workspace(
        session=session,
        user_id=current_user.id,
        workspace_id=workspace_id,
        **update_data,
    )
    return _workspace_to_response(workspace)


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: str,
    session: DbSession,
    current_user: CurrentUser,
    ws_service: WsService,
    background_tasks: BackgroundTasks,
) -> Response:
    """Delete workspace (soft delete).

    Only allowed in CREATED, STOPPED, or ERROR state.
    Uses CAS pattern to prevent race conditions.

    Returns immediately with 204 status.
    Actual deletion (container + storage cleanup) happens asynchronously.
    """
    workspace = await ws_service.initiate_delete(session, current_user.id, workspace_id)

    background_tasks.add_task(
        ws_service.delete_workspace,
        workspace_id=workspace_id,
        home_ctx=workspace.home_ctx,
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{workspace_id}:start", response_model=WorkspaceActionResponse)
async def start_workspace(
    workspace_id: str,
    session: DbSession,
    current_user: CurrentUser,
    ws_service: WsService,
    background_tasks: BackgroundTasks,
) -> WorkspaceActionResponse:
    """Start a workspace.

    Only allowed in CREATED, STOPPED, or ERROR state.
    Uses CAS pattern to prevent race conditions.

    Returns immediately with PROVISIONING status.
    Final status (RUNNING/ERROR) is determined asynchronously.
    """
    workspace = await ws_service.initiate_start(session, current_user.id, workspace_id)

    background_tasks.add_task(
        ws_service.start_workspace,
        workspace_id=workspace_id,
        home_store_key=workspace.home_store_key,
        existing_ctx=workspace.home_ctx,
        image_ref=workspace.image_ref,
    )

    return WorkspaceActionResponse(
        id=workspace_id,
        status=WorkspaceStatus.PROVISIONING,
    )


@router.post("/{workspace_id}:stop", response_model=WorkspaceActionResponse)
async def stop_workspace(
    workspace_id: str,
    session: DbSession,
    current_user: CurrentUser,
    ws_service: WsService,
    background_tasks: BackgroundTasks,
) -> WorkspaceActionResponse:
    """Stop a workspace.

    Only allowed in RUNNING or ERROR state.
    Uses CAS pattern to prevent race conditions.

    Returns immediately with STOPPING status.
    Final status (STOPPED/ERROR) is determined asynchronously.
    """
    workspace = await ws_service.initiate_stop(session, current_user.id, workspace_id)

    background_tasks.add_task(
        ws_service.stop_workspace,
        workspace_id=workspace_id,
        home_ctx=workspace.home_ctx,
    )

    return WorkspaceActionResponse(
        id=workspace_id,
        status=WorkspaceStatus.STOPPING,
    )
