"""Instance API endpoints."""

from fastapi import APIRouter, HTTPException

from codehub_agent.api.dependencies import get_runtime
from codehub_agent.api.v1.schemas import (
    InstanceListResponse,
    InstanceStatusResponse,
    OperationResponse,
    StartInstanceRequest,
    UpstreamResponse,
)

router = APIRouter(prefix="/instances", tags=["instances"])


@router.get("", response_model=InstanceListResponse)
async def list_instances() -> InstanceListResponse:
    """List all managed instances."""
    runtime = get_runtime()
    instances = await runtime.instances.list_all()
    return InstanceListResponse(instances=instances)


@router.post("/{workspace_id}/start", status_code=200, response_model=OperationResponse)
async def start_instance(
    workspace_id: str, request: StartInstanceRequest
) -> OperationResponse:
    """Start container for workspace."""
    runtime = get_runtime()
    try:
        await runtime.instances.start(workspace_id, request.image_ref)
        return OperationResponse(status="started", workspace_id=workspace_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{workspace_id}", status_code=200, response_model=OperationResponse)
async def delete_instance(workspace_id: str) -> OperationResponse:
    """Delete container for workspace."""
    runtime = get_runtime()
    try:
        await runtime.instances.delete(workspace_id)
        return OperationResponse(status="deleted", workspace_id=workspace_id)
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
