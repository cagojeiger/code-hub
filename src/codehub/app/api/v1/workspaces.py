"""Workspace API endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from codehub.app.config import get_settings
from codehub.app.proxy.auth import get_user_id_from_session
from codehub.core.domain import DesiredState
from codehub.infra import get_session
from codehub.services import workspace_service

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

DbSession = Annotated[AsyncSession, Depends(get_session)]

# Default image from settings
_settings = get_settings()
_default_image = _settings.docker.default_image


# =============================================================================
# Request/Response Models
# =============================================================================


class CreateWorkspaceRequest(BaseModel):
    """Create workspace request."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)
    image_ref: str = Field(default=_default_image, max_length=512)


class UpdateWorkspaceRequest(BaseModel):
    """Update workspace request."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)
    memo: str | None = None
    desired_state: DesiredState | None = None


class WorkspaceResponse(BaseModel):
    """Workspace response.

    Note: progress field is calculated in frontend (state.js).
    Backend only provides phase, operation, desired_state.
    """

    id: str
    owner_user_id: str
    name: str
    description: str | None
    memo: str | None
    image_ref: str
    phase: str
    operation: str
    desired_state: str
    archive_key: str | None
    error_reason: str | None
    error_count: int
    created_at: datetime
    updated_at: datetime
    last_access_at: datetime | None  # 마지막 활동 시간
    phase_changed_at: datetime | None  # phase 변경 시간 (TTL 계산용)

    model_config = {"from_attributes": True}


class WorkspaceListResponse(BaseModel):
    """Workspace list response."""

    items: list[WorkspaceResponse]
    total: int


# =============================================================================
# Helper
# =============================================================================


def _to_response(ws) -> WorkspaceResponse:
    """Convert workspace model to response."""
    return WorkspaceResponse.model_validate(ws)


# =============================================================================
# Endpoints
# =============================================================================


@router.post("", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    request: CreateWorkspaceRequest,
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> WorkspaceResponse:
    """Create a new workspace.

    Creates workspace and requests start. Returns 429 if running limit exceeded.
    """
    user_id = await get_user_id_from_session(db, session)

    # Check limit before creating (avoid creating workspace that can't start)
    # Create workspace (with desired_state=RUNNING but not counted yet)
    workspace = await workspace_service.create_workspace(
        db=db,
        user_id=user_id,
        name=request.name,
        description=request.description,
        image_ref=request.image_ref,
    )
    # request_start validates and commits the start request
    # RunningLimitExceededError is handled by FastAPI exception handler
    workspace = await workspace_service.request_start(db, workspace.id, user_id)

    return _to_response(workspace)


@router.get("", response_model=WorkspaceListResponse)
async def list_workspaces(
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> WorkspaceListResponse:
    """List user's workspaces."""
    user_id = await get_user_id_from_session(db, session)

    workspaces = await workspace_service.list_workspaces(
        db=db,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )

    return WorkspaceListResponse(
        items=[_to_response(ws) for ws in workspaces],
        total=len(workspaces),
    )


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> WorkspaceResponse:
    """Get workspace by ID."""
    user_id = await get_user_id_from_session(db, session)

    workspace = await workspace_service.get_workspace(
        db=db,
        workspace_id=workspace_id,
        user_id=user_id,
    )

    return _to_response(workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    request: UpdateWorkspaceRequest,
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> WorkspaceResponse:
    """Update workspace.

    If desired_state=RUNNING, uses request_start() for limit enforcement.
    Returns 429 if running limit exceeded.
    """
    user_id = await get_user_id_from_session(db, session)

    # desired_state=RUNNING → request_start() 단일 진입점 사용
    # RunningLimitExceededError is handled by FastAPI exception handler
    if request.desired_state == DesiredState.RUNNING:
        workspace = await workspace_service.request_start(db, workspace_id, user_id)
        # Update other fields if provided
        if request.name or request.description is not None or request.memo is not None:
            workspace = await workspace_service.update_workspace(
                db=db,
                workspace_id=workspace_id,
                user_id=user_id,
                name=request.name,
                description=request.description,
                memo=request.memo,
            )
    else:
        workspace = await workspace_service.update_workspace(
            db=db,
            workspace_id=workspace_id,
            user_id=user_id,
            name=request.name,
            description=request.description,
            memo=request.memo,
            desired_state=request.desired_state,
        )

    return _to_response(workspace)


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: str,
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> None:
    """Delete workspace (soft delete)."""
    user_id = await get_user_id_from_session(db, session)

    await workspace_service.delete_workspace(
        db=db,
        workspace_id=workspace_id,
        user_id=user_id,
    )
