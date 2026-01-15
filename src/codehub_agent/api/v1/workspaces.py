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
from codehub_agent.runtimes.docker.job import JobType
from codehub_agent.runtimes.docker.lock import get_workspace_lock

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


class RestoreStatus(BaseModel):
    """Restore status within a workspace (from .restore_marker)."""

    restore_op_id: str
    archive_key: str


class WorkspaceState(BaseModel):
    """Complete state of a workspace."""

    workspace_id: str
    container: ContainerStatus | None = None
    volume: VolumeStatus | None = None
    archive: ArchiveStatus | None = None
    restore: RestoreStatus | None = None


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
    """Restore request with archive_key and restore_op_id."""

    archive_key: str
    restore_op_id: str


class RestoreResponse(BaseModel):
    """Restore operation response with restore_marker."""

    status: str
    workspace_id: str
    restore_marker: str


class StartRequest(BaseModel):
    """Start request with optional image."""

    image: str | None = None


class GCRequest(BaseModel):
    """GC request with protected archives and retention policy.

    Two types of protection:
    - archive_keys: Direct archive_key column values (RESTORING target)
    - protected_workspaces: (ws_id, archive_op_id) tuples for path calculation (ARCHIVING crash)

    Retention:
    - retention_count: Number of archives to keep per workspace (default: 3)
    """

    archive_keys: list[str]
    protected_workspaces: list[tuple[str, str]]
    retention_count: int = 3


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
    - Restore status (restore_op_id, archive_key from .restore_marker)
    """
    # Get all data in parallel
    containers, volumes, archives, restore_markers = await asyncio.gather(
        runtime.instances.list_all(),
        runtime.volumes.list_all(),
        runtime.storage.list_archives(),
        runtime.storage.list_restore_markers(),
    )

    # Update metrics
    AGENT_CONTAINERS_TOTAL.set(len(containers))
    AGENT_VOLUMES_TOTAL.set(len(volumes))

    # Index by workspace_id for fast lookup
    container_map: dict[str, dict] = {c["workspace_id"]: c for c in containers}
    volume_map: dict[str, dict] = {v["workspace_id"]: v for v in volumes}
    archive_map: dict[str, object] = {a.workspace_id: a for a in archives}
    restore_map: dict[str, object] = {r.workspace_id: r for r in restore_markers}

    # Collect all unique workspace IDs
    all_workspace_ids = (
        set(container_map.keys())
        | set(volume_map.keys())
        | set(archive_map.keys())
        | set(restore_map.keys())
    )

    # Build workspace states
    workspaces = []
    for ws_id in sorted(all_workspace_ids):
        container_info = container_map.get(ws_id)
        volume_info = volume_map.get(ws_id)
        archive_info = archive_map.get(ws_id)
        restore_info = restore_map.get(ws_id)

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
            restore=(
                RestoreStatus(
                    restore_op_id=restore_info.restore_op_id,
                    archive_key=restore_info.archive_key,
                )
                if restore_info
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
    async with get_workspace_lock(workspace_id):
        result = await runtime.volumes.create(workspace_id)
        return OperationResponse(status=result.status.value, workspace_id=workspace_id)


@router.post("/{workspace_id}/start", response_model=OperationResponse)
async def start(
    workspace_id: str,
    request: StartRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> OperationResponse:
    """Start workspace container (Fire-and-Forget).

    Fire-and-Forget pattern:
    - Container start is initiated in background
    - Returns immediately with status=in_progress
    - WC detects completion via Observer (container.running=true)
    """
    async with get_workspace_lock(workspace_id):
        asyncio.create_task(runtime.instances.start(workspace_id, request.image))
        return OperationResponse(status="in_progress", workspace_id=workspace_id)


@router.post("/{workspace_id}/stop", response_model=OperationResponse)
async def stop(
    workspace_id: str,
    runtime: DockerRuntime = Depends(get_runtime),
) -> OperationResponse:
    """Stop workspace container (Fire-and-Forget).

    Fire-and-Forget pattern:
    - Container deletion is initiated in background
    - Returns immediately with status=in_progress
    - WC detects completion via Observer (container=null)
    """
    async with get_workspace_lock(workspace_id):
        asyncio.create_task(runtime.instances.delete(workspace_id))
        return OperationResponse(status="in_progress", workspace_id=workspace_id)


@router.delete("/{workspace_id}", response_model=OperationResponse)
async def delete(
    workspace_id: str,
    runtime: DockerRuntime = Depends(get_runtime),
) -> OperationResponse:
    """Delete workspace completely (Fire-and-Forget).

    Fire-and-Forget pattern:
    - Container + Volume deletion initiated in background
    - Returns immediately with status=in_progress
    - WC detects completion via Observer (container=null, volume=null)
    """
    async def _delete_all() -> None:
        """Delete container then volume."""
        try:
            await runtime.instances.delete(workspace_id)
        except Exception:
            pass  # Container might not exist
        try:
            await runtime.volumes.delete(workspace_id)
        except Exception:
            pass  # Volume might not exist

    async with get_workspace_lock(workspace_id):
        asyncio.create_task(_delete_all())
        return OperationResponse(status="in_progress", workspace_id=workspace_id)


# =============================================================================
# Persistence Endpoints
# =============================================================================


@router.post("/{workspace_id}/archive", response_model=ArchiveResponse)
async def archive(
    workspace_id: str,
    request: ArchiveRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> ArchiveResponse:
    """Archive workspace to S3 (Fire-and-Forget).

    Preconditions:
    - Container must NOT be running
    - Volume must exist

    Fire-and-Forget pattern:
    - Job is started in background
    - Returns immediately with status=in_progress
    - WC detects completion via Observer (S3 .meta marker)
    """
    from codehub_agent.api.errors import ContainerRunningError, VolumeNotFoundError

    async with get_workspace_lock(workspace_id):
        # Precondition checks
        container_status = await runtime.instances.get_status(workspace_id)
        if container_status.running:
            raise ContainerRunningError(
                f"Cannot archive while container is running for workspace {workspace_id}"
            )

        volume_status = await runtime.volumes.exists(workspace_id)
        if not volume_status.exists:
            raise VolumeNotFoundError(
                f"Volume does not exist for workspace {workspace_id}"
            )

        # Check if job is already running (idempotency)
        existing = await runtime.jobs.find_running_job(
            workspace_id, JobType.ARCHIVE, request.archive_op_id
        )
        if not existing:
            # Fire-and-Forget: Start job in background, don't wait
            asyncio.create_task(
                runtime.jobs.run_archive(workspace_id, request.archive_op_id)
            )

        # Always return in_progress - WC will detect completion via Observer
        archive_key = runtime.get_archive_key(workspace_id, request.archive_op_id)

        return ArchiveResponse(
            status="in_progress",
            workspace_id=workspace_id,
            archive_key=archive_key,
        )


@router.post("/{workspace_id}/restore", response_model=RestoreResponse)
async def restore(
    workspace_id: str,
    request: RestoreRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> RestoreResponse:
    """Restore workspace from S3 archive (Fire-and-Forget).

    Preconditions:
    - Container must NOT be running
    - Archive must exist in S3

    Fire-and-Forget pattern:
    - Job is started in background
    - Returns immediately with status=in_progress
    - WC detects completion via Observer (S3 .restore_marker)
    """
    from codehub_agent.api.errors import ArchiveNotFoundError, ContainerRunningError

    async with get_workspace_lock(workspace_id):
        # Precondition checks
        container_status = await runtime.instances.get_status(workspace_id)
        if container_status.running:
            raise ContainerRunningError(
                f"Cannot restore while container is running for workspace {workspace_id}"
            )

        archive_exists = await runtime.storage.archive_exists(request.archive_key)
        if not archive_exists:
            raise ArchiveNotFoundError(
                f"Archive not found: {request.archive_key}"
            )

        # Check if job is already running (idempotency)
        existing = await runtime.jobs.find_running_job(workspace_id, JobType.RESTORE)
        if not existing:
            # Fire-and-Forget: Start job in background, don't wait
            asyncio.create_task(
                runtime.jobs.run_restore(
                    workspace_id, request.archive_key, request.restore_op_id
                )
            )

        # Always return in_progress - WC will detect completion via Observer
        return RestoreResponse(
            status="in_progress",
            workspace_id=workspace_id,
            restore_marker=request.restore_op_id,
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

    Retention + Protection based GC:
    - Retention: Keep latest N archives per workspace
    - Protection: Never delete RESTORING/ARCHIVING archives
    """
    deleted_count, deleted_keys = await runtime.storage.run_gc(
        request.archive_keys,
        request.protected_workspaces,
        request.retention_count,
    )
    return GCResponse(deleted_count=deleted_count, deleted_keys=deleted_keys)
