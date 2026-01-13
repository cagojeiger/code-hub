"""API v1 schemas.

Consolidated request/response models for all API endpoints.
"""

from typing import Literal

from pydantic import BaseModel


# =============================================================================
# Common
# =============================================================================


class OperationResponse(BaseModel):
    """Common operation response."""

    status: Literal["created", "started", "deleted"]
    workspace_id: str


# =============================================================================
# Health
# =============================================================================


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str = "0.2.1"


# =============================================================================
# Instances
# =============================================================================


class StartInstanceRequest(BaseModel):
    """Start instance request."""

    image_ref: str | None = None


class InstanceStatusResponse(BaseModel):
    """Instance status response."""

    exists: bool
    running: bool
    healthy: bool
    reason: str
    message: str


class UpstreamResponse(BaseModel):
    """Upstream response."""

    hostname: str
    port: int
    url: str


class InstanceListResponse(BaseModel):
    """Instance list response."""

    instances: list[dict]


# =============================================================================
# Volumes
# =============================================================================


class VolumeStatusResponse(BaseModel):
    """Volume status response."""

    exists: bool
    name: str


class VolumeListResponse(BaseModel):
    """Volume list response."""

    volumes: list[dict]


# =============================================================================
# Jobs
# =============================================================================


class JobRequest(BaseModel):
    """Job request for archive/restore."""

    workspace_id: str
    op_id: str


class JobResponse(BaseModel):
    """Job result response."""

    exit_code: int
    logs: str


# =============================================================================
# Storage
# =============================================================================


class ProtectedItem(BaseModel):
    """Protected archive item."""

    workspace_id: str
    op_id: str


class GCRequest(BaseModel):
    """GC request with protected items."""

    protected: list[ProtectedItem]


class GCResponse(BaseModel):
    """GC result response."""

    deleted_count: int
    deleted_keys: list[str]
