"""Workspace schemas for API and SSE events."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.db import WorkspaceStatus
from app.schemas.pagination import PaginationMeta


# Request schemas
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


# Response schemas
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


class PaginatedWorkspaceResponse(BaseModel):
    """Paginated response for workspace list."""

    items: list[WorkspaceResponse]
    pagination: PaginationMeta


# SSE event schemas
class WorkspaceDeletedEvent(BaseModel):
    """SSE workspace_deleted event payload."""

    id: str
