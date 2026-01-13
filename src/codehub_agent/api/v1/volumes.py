"""Volume API endpoints."""

from fastapi import APIRouter, HTTPException

from codehub_agent.api.dependencies import get_runtime
from codehub_agent.api.v1.schemas import (
    OperationResponse,
    VolumeListResponse,
    VolumeStatusResponse,
)

router = APIRouter(prefix="/volumes", tags=["volumes"])


@router.get("", response_model=VolumeListResponse)
async def list_volumes() -> VolumeListResponse:
    """List all managed volumes."""
    runtime = get_runtime()
    volumes = await runtime.volumes.list_all()
    return VolumeListResponse(volumes=volumes)


@router.post("/{workspace_id}", status_code=201, response_model=OperationResponse)
async def create_volume(workspace_id: str) -> OperationResponse:
    """Create volume for workspace."""
    runtime = get_runtime()
    try:
        await runtime.volumes.create(workspace_id)
        return OperationResponse(status="created", workspace_id=workspace_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{workspace_id}", status_code=200, response_model=OperationResponse)
async def delete_volume(workspace_id: str) -> OperationResponse:
    """Delete volume for workspace."""
    runtime = get_runtime()
    try:
        await runtime.volumes.delete(workspace_id)
        return OperationResponse(status="deleted", workspace_id=workspace_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{workspace_id}/exists", response_model=VolumeStatusResponse)
async def volume_exists(workspace_id: str) -> VolumeStatusResponse:
    """Check if volume exists."""
    runtime = get_runtime()
    status = await runtime.volumes.exists(workspace_id)
    return VolumeStatusResponse(exists=status.exists, name=status.name)
