"""Volume API endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from codehub_agent.api.dependencies import get_runtime
from codehub_agent.api.v1.instances import OperationResponse
from codehub_agent.runtimes import DockerRuntime

router = APIRouter(prefix="/volumes", tags=["volumes"])


# =============================================================================
# Schemas
# =============================================================================


class VolumeStatusResponse(BaseModel):
    """Volume status response."""

    exists: bool
    name: str


class VolumeListResponse(BaseModel):
    """Volume list response."""

    volumes: list[dict]


@router.get("", response_model=VolumeListResponse)
async def list_volumes(
    runtime: DockerRuntime = Depends(get_runtime),
) -> VolumeListResponse:
    """List all managed volumes."""
    volumes = await runtime.volumes.list_all()
    return VolumeListResponse(volumes=volumes)


@router.post("/{workspace_id}", status_code=201, response_model=OperationResponse)
async def create_volume(
    workspace_id: str,
    runtime: DockerRuntime = Depends(get_runtime),
) -> OperationResponse:
    """Create volume for workspace."""
    await runtime.volumes.create(workspace_id)
    return OperationResponse(status="created", workspace_id=workspace_id)


@router.delete("/{workspace_id}", status_code=200, response_model=OperationResponse)
async def delete_volume(
    workspace_id: str,
    runtime: DockerRuntime = Depends(get_runtime),
) -> OperationResponse:
    """Delete volume for workspace."""
    await runtime.volumes.delete(workspace_id)
    return OperationResponse(status="deleted", workspace_id=workspace_id)


@router.get("/{workspace_id}/exists", response_model=VolumeStatusResponse)
async def volume_exists(
    workspace_id: str,
    runtime: DockerRuntime = Depends(get_runtime),
) -> VolumeStatusResponse:
    """Check if volume exists."""
    status = await runtime.volumes.exists(workspace_id)
    return VolumeStatusResponse(exists=status.exists, name=status.name)
