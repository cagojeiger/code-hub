"""Volume API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from codehub_agent.runtimes import DockerRuntime

router = APIRouter(prefix="/volumes", tags=["volumes"])

# Singleton runtime instance
_runtime: DockerRuntime | None = None


def get_runtime() -> DockerRuntime:
    """Get runtime singleton."""
    global _runtime
    if _runtime is None:
        _runtime = DockerRuntime()
    return _runtime


class VolumeStatusResponse(BaseModel):
    """Volume status response."""

    exists: bool
    name: str


class VolumeListResponse(BaseModel):
    """Volume list response."""

    volumes: list[dict]


@router.get("", response_model=VolumeListResponse)
async def list_volumes() -> VolumeListResponse:
    """List all managed volumes."""
    runtime = get_runtime()
    volumes = await runtime.volumes.list_all()
    return VolumeListResponse(volumes=volumes)


@router.post("/{workspace_id}", status_code=201)
async def create_volume(workspace_id: str) -> dict:
    """Create volume for workspace."""
    runtime = get_runtime()
    try:
        await runtime.volumes.create(workspace_id)
        return {"status": "created", "workspace_id": workspace_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{workspace_id}", status_code=200)
async def delete_volume(workspace_id: str) -> dict:
    """Delete volume for workspace."""
    runtime = get_runtime()
    try:
        await runtime.volumes.delete(workspace_id)
        return {"status": "deleted", "workspace_id": workspace_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{workspace_id}/exists", response_model=VolumeStatusResponse)
async def volume_exists(workspace_id: str) -> VolumeStatusResponse:
    """Check if volume exists."""
    runtime = get_runtime()
    status = await runtime.volumes.exists(workspace_id)
    return VolumeStatusResponse(exists=status.exists, name=status.name)
