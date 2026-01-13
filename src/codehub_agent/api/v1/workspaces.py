"""Workspace API endpoints.

This is the new unified API for workspace management.
It provides a single endpoint (observe) that returns complete workspace state,
combining container, volume, and archive information.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from codehub_agent.api.dependencies import get_runtime
from codehub_agent.runtimes import DockerRuntime

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


# =============================================================================
# Schemas
# =============================================================================


class ContainerStatus(BaseModel):
    """Container status within a workspace."""

    running: bool
    healthy: bool


class VolumeStatus(BaseModel):
    """Volume status within a workspace."""

    exists: bool


class ArchiveStatus(BaseModel):
    """Archive status within a workspace."""

    exists: bool
    archive_key: str | None = None


class WorkspaceState(BaseModel):
    """Complete state of a workspace."""

    workspace_id: str
    container: ContainerStatus | None = None
    volume: VolumeStatus | None = None
    archive: ArchiveStatus | None = None


class ObserveResponse(BaseModel):
    """Response for observe endpoint."""

    workspaces: list[WorkspaceState]


class UpstreamResponse(BaseModel):
    """Upstream address for proxy routing."""

    hostname: str
    port: int
    url: str


class OperationResponse(BaseModel):
    """Common operation response."""

    status: str
    workspace_id: str


class ArchiveRequest(BaseModel):
    """Archive request with op_id."""

    op_id: str


class ArchiveResponse(BaseModel):
    """Archive operation response."""

    status: str
    workspace_id: str
    archive_key: str


class RestoreRequest(BaseModel):
    """Restore request with archive_key."""

    archive_key: str


class StartRequest(BaseModel):
    """Start request with optional image."""

    image: str | None = None


class ProtectedItem(BaseModel):
    """Protected archive item for GC."""

    workspace_id: str
    op_id: str


class GCRequest(BaseModel):
    """GC request with protected items."""

    protected: list[ProtectedItem]


class GCResponse(BaseModel):
    """GC result response."""

    deleted_count: int
    deleted_keys: list[str]


class DeleteArchiveResponse(BaseModel):
    """Delete archive response."""

    deleted: bool
    archive_key: str


# =============================================================================
# Observe Endpoint (Main)
# =============================================================================


@router.get("", response_model=ObserveResponse)
async def observe(
    runtime: DockerRuntime = Depends(get_runtime),
) -> ObserveResponse:
    """Observe all workspaces and return their current state.

    This is the primary endpoint for Observer coordinator.
    Returns a complete snapshot of all workspaces combining:
    - Container status (running, healthy)
    - Volume status (exists)
    - Archive status (exists, archive_key)
    """
    # Get all data in parallel-like manner
    containers = await runtime.instances.list_all()
    volumes = await runtime.volumes.list_all()
    archives = await runtime.storage.list_archives()

    # Index by workspace_id for fast lookup
    container_map: dict[str, dict] = {c["workspace_id"]: c for c in containers}
    volume_map: dict[str, dict] = {v["workspace_id"]: v for v in volumes}
    archive_map: dict[str, object] = {a.workspace_id: a for a in archives}

    # Collect all unique workspace IDs
    all_workspace_ids = set(container_map.keys()) | set(volume_map.keys()) | set(
        archive_map.keys()
    )

    # Build workspace states
    workspaces = []
    for ws_id in sorted(all_workspace_ids):
        container_info = container_map.get(ws_id)
        volume_info = volume_map.get(ws_id)
        archive_info = archive_map.get(ws_id)

        state = WorkspaceState(
            workspace_id=ws_id,
            container=(
                ContainerStatus(
                    running=container_info.get("running", False),
                    healthy=container_info.get("running", False),  # Simplified
                )
                if container_info
                else None
            ),
            volume=(
                VolumeStatus(exists=volume_info.get("exists", False))
                if volume_info
                else None
            ),
            archive=(
                ArchiveStatus(
                    exists=archive_info.exists,
                    archive_key=archive_info.archive_key,
                )
                if archive_info
                else None
            ),
        )
        workspaces.append(state)

    return ObserveResponse(workspaces=workspaces)


# =============================================================================
# Lifecycle Endpoints
# =============================================================================


@router.post("/{workspace_id}/provision", status_code=201, response_model=OperationResponse)
async def provision(
    workspace_id: str,
    runtime: DockerRuntime = Depends(get_runtime),
) -> OperationResponse:
    """Provision a new workspace (create volume)."""
    await runtime.volumes.create(workspace_id)
    return OperationResponse(status="provisioned", workspace_id=workspace_id)


@router.post("/{workspace_id}/start", response_model=OperationResponse)
async def start(
    workspace_id: str,
    request: StartRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> OperationResponse:
    """Start workspace container."""
    await runtime.instances.start(workspace_id, request.image)
    return OperationResponse(status="started", workspace_id=workspace_id)


@router.post("/{workspace_id}/stop", response_model=OperationResponse)
async def stop(
    workspace_id: str,
    runtime: DockerRuntime = Depends(get_runtime),
) -> OperationResponse:
    """Stop workspace container."""
    # Stop but don't remove the container
    container_name = runtime._naming.container_name(workspace_id)
    await runtime.instances._containers.stop(container_name)
    return OperationResponse(status="stopped", workspace_id=workspace_id)


@router.delete("/{workspace_id}", response_model=OperationResponse)
async def delete(
    workspace_id: str,
    runtime: DockerRuntime = Depends(get_runtime),
) -> OperationResponse:
    """Delete workspace completely (container + volume)."""
    # Delete container first
    try:
        await runtime.instances.delete(workspace_id)
    except Exception:
        pass  # Container might not exist

    # Then delete volume
    try:
        await runtime.volumes.delete(workspace_id)
    except Exception:
        pass  # Volume might not exist or be in use

    return OperationResponse(status="deleted", workspace_id=workspace_id)


# =============================================================================
# Persistence Endpoints
# =============================================================================


@router.post("/{workspace_id}/archive", response_model=ArchiveResponse)
async def archive(
    workspace_id: str,
    request: ArchiveRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> ArchiveResponse:
    """Archive workspace to S3."""
    await runtime.jobs.run_archive(workspace_id, request.op_id)
    archive_key = runtime._naming.archive_s3_key(workspace_id, request.op_id)
    return ArchiveResponse(
        status="archived",
        workspace_id=workspace_id,
        archive_key=archive_key,
    )


@router.post("/{workspace_id}/restore", response_model=OperationResponse)
async def restore(
    workspace_id: str,
    request: RestoreRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> OperationResponse:
    """Restore workspace from S3 archive."""
    # Extract op_id from archive_key
    # Format: cluster_id/workspace_id/op_id/home.tar.zst
    parts = request.archive_key.split("/")
    if len(parts) >= 3:
        op_id = parts[2]
    else:
        op_id = parts[-2] if len(parts) >= 2 else "unknown"

    await runtime.jobs.run_restore(workspace_id, op_id)
    return OperationResponse(status="restored", workspace_id=workspace_id)


@router.delete("/archives", response_model=DeleteArchiveResponse)
async def delete_archive(
    archive_key: str = Query(..., description="Full S3 key of the archive to delete"),
    runtime: DockerRuntime = Depends(get_runtime),
) -> DeleteArchiveResponse:
    """Delete an archive from S3."""
    deleted = await runtime.storage.delete_archive(archive_key)
    return DeleteArchiveResponse(deleted=deleted, archive_key=archive_key)


# =============================================================================
# Routing Endpoint
# =============================================================================


@router.get("/{workspace_id}/upstream", response_model=UpstreamResponse)
async def get_upstream(
    workspace_id: str,
    runtime: DockerRuntime = Depends(get_runtime),
) -> UpstreamResponse:
    """Get upstream address for proxy routing."""
    upstream = await runtime.instances.get_upstream(workspace_id)
    return UpstreamResponse(
        hostname=upstream.hostname,
        port=upstream.port,
        url=upstream.url,
    )


# =============================================================================
# GC Endpoint
# =============================================================================


@router.post("/gc", response_model=GCResponse)
async def run_gc(
    request: GCRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> GCResponse:
    """Run garbage collection on archives.

    Deletes archives not in the protected list.
    """
    protected = [(item.workspace_id, item.op_id) for item in request.protected]
    deleted_count, deleted_keys = await runtime.storage.run_gc(protected)
    return GCResponse(deleted_count=deleted_count, deleted_keys=deleted_keys)
