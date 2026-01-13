"""Instance API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from codehub_agent.runtimes import DockerRuntime

router = APIRouter(prefix="/instances", tags=["instances"])

# Singleton runtime instance
_runtime: DockerRuntime | None = None


def get_runtime() -> DockerRuntime:
    """Get runtime singleton."""
    global _runtime
    if _runtime is None:
        _runtime = DockerRuntime()
    return _runtime


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


@router.get("", response_model=InstanceListResponse)
async def list_instances() -> InstanceListResponse:
    """List all managed instances."""
    runtime = get_runtime()
    instances = await runtime.instances.list_all()
    return InstanceListResponse(instances=instances)


@router.post("/{workspace_id}/start", status_code=200)
async def start_instance(workspace_id: str, request: StartInstanceRequest) -> dict:
    """Start container for workspace."""
    runtime = get_runtime()
    try:
        await runtime.instances.start(workspace_id, request.image_ref)
        return {"status": "started", "workspace_id": workspace_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{workspace_id}", status_code=200)
async def delete_instance(workspace_id: str) -> dict:
    """Delete container for workspace."""
    runtime = get_runtime()
    try:
        await runtime.instances.delete(workspace_id)
        return {"status": "deleted", "workspace_id": workspace_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{workspace_id}/status", response_model=InstanceStatusResponse)
async def get_instance_status(workspace_id: str) -> InstanceStatusResponse:
    """Get instance status."""
    runtime = get_runtime()
    status = await runtime.instances.get_status(workspace_id)
    return InstanceStatusResponse(
        exists=status.exists,
        running=status.running,
        healthy=status.healthy,
        reason=status.reason,
        message=status.message,
    )


@router.get("/{workspace_id}/upstream", response_model=UpstreamResponse)
async def get_upstream(workspace_id: str) -> UpstreamResponse:
    """Get upstream address for proxy."""
    runtime = get_runtime()
    upstream = await runtime.instances.get_upstream(workspace_id)
    return UpstreamResponse(
        hostname=upstream.hostname,
        port=upstream.port,
        url=upstream.url,
    )
