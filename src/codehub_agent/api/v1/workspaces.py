"""Workspace API endpoints.

This is the new unified API for workspace management.
It provides a single endpoint (observe) that returns complete workspace state,
combining container, volume, and archive information.
"""

import asyncio

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from codehub_agent.api.dependencies import get_runtime
from codehub_agent.metrics import AGENT_CONTAINERS_TOTAL, AGENT_VOLUMES_TOTAL
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
    """Archive request with archive_op_id."""

    archive_op_id: str


class ArchiveResponse(BaseModel):
    """Archive operation response."""

    status: str
    workspace_id: str
    archive_key: str


class RestoreRequest(BaseModel):
    """Restore request with archive_key."""

    archive_key: str


class RestoreResponse(BaseModel):
    """Restore operation response with restore_marker."""

    status: str
    workspace_id: str
    restore_marker: str


class StartRequest(BaseModel):
    """Start request with optional image."""

    image: str | None = None


class GCRequest(BaseModel):
    """GC request with protected archives.

    Two types of protection:
    - archive_keys: Direct archive_key column values (RESTORING target)
    - protected_workspaces: (ws_id, archive_op_id) tuples for path calculation (ARCHIVING crash)
    """

    archive_keys: list[str]
    protected_workspaces: list[tuple[str, str]]


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
    # Get all data in parallel
    containers, volumes, archives = await asyncio.gather(
        runtime.instances.list_all(),
        runtime.volumes.list_all(),
        runtime.storage.list_archives(),
    )

    # Update metrics
    AGENT_CONTAINERS_TOTAL.set(len(containers))
    AGENT_VOLUMES_TOTAL.set(len(volumes))

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
    result = await runtime.volumes.create(workspace_id)
    return OperationResponse(status=result.status.value, workspace_id=workspace_id)


@router.post("/{workspace_id}/start", response_model=OperationResponse)
async def start(
    workspace_id: str,
    request: StartRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> OperationResponse:
    """Start workspace container."""
    result = await runtime.instances.start(workspace_id, request.image)
    return OperationResponse(status=result.status.value, workspace_id=workspace_id)


@router.post("/{workspace_id}/stop", response_model=OperationResponse)
async def stop(
    workspace_id: str,
    runtime: DockerRuntime = Depends(get_runtime),
) -> OperationResponse:
    """Stop workspace container (delete container, keep volume)."""
    result = await runtime.instances.delete(workspace_id)
    return OperationResponse(status=result.status.value, workspace_id=workspace_id)


@router.delete("/{workspace_id}", response_model=OperationResponse)
async def delete(
    workspace_id: str,
    runtime: DockerRuntime = Depends(get_runtime),
) -> OperationResponse:
    """Delete workspace completely (container + volume).

    Note: Container must be deleted before volume (volume in use error).
    We delete container first, then volume in sequence.
    Idempotent - returns success even if resources don't exist.
    """
    # Delete container first (required before volume deletion)
    try:
        await runtime.instances.delete(workspace_id)
    except Exception:
        pass  # Container might not exist

    # Then delete volume (must be after container deletion)
    try:
        await runtime.volumes.delete(workspace_id)
    except Exception:
        pass  # Volume might not exist or be in use

    # Always return "deleted" for idempotency
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
    """Archive workspace to S3.

    Returns status=in_progress if archive job already running (idempotency).
    """
    result = await runtime.jobs.run_archive(workspace_id, request.archive_op_id)

    # For in_progress, return expected key (job is still running)
    if result.archive_key:
        archive_key = result.archive_key
    else:
        archive_key = runtime.get_archive_key(workspace_id, request.archive_op_id)

    return ArchiveResponse(
        status=result.status.value,
        workspace_id=workspace_id,
        archive_key=archive_key,
    )


@router.post("/{workspace_id}/restore", response_model=RestoreResponse)
async def restore(
    workspace_id: str,
    request: RestoreRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> RestoreResponse:
    """Restore workspace from S3 archive.

    Per spec L229: Job receives full archive_key directly (no parsing).
    Returns restore_marker for crash recovery verification.
    Returns status=in_progress if restore job already running (idempotency).
    """
    result = await runtime.jobs.run_restore(workspace_id, request.archive_key)

    # Use result.restore_marker if completed, else use the requested key
    restore_marker = result.restore_marker or request.archive_key

    return RestoreResponse(
        status=result.status.value,
        workspace_id=workspace_id,
        restore_marker=restore_marker,
    )


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
    Two types of protection:
    - archive_keys: Direct paths (RESTORING target)
    - protected_workspaces: (ws_id, archive_op_id) for path calculation (ARCHIVING crash)
    """
    deleted_count, deleted_keys = await runtime.storage.run_gc(
        request.archive_keys,
        request.protected_workspaces,
    )
    return GCResponse(deleted_count=deleted_count, deleted_keys=deleted_keys)
