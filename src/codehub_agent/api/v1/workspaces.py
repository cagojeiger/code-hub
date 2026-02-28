"""Workspace API endpoints."""

import asyncio
import time
from collections import defaultdict

from fastapi import APIRouter, Depends

from codehub_agent.api.background import spawn_background_task
from codehub_agent.api.dependencies import get_runtime
from codehub_agent.api.v1.schemas import (
    ArchiveLatest,
    ArchiveRequest,
    ArchiveResponse,
    ArchiveStatus,
    ContainerStatus,
    ErrorLast,
    ErrorStatus,
    GCRequest,
    GCResponse,
    ObserveResponse,
    OperationResponse,
    OperationStatusValue,
    RestoreLast,
    RestoreRequest,
    RestoreResponse,
    RestoreStatus,
    StartRequest,
    UpstreamResponse,
    VolumeStatus,
    WorkspaceState,
)
from codehub_agent.metrics import AGENT_CONTAINERS_TOTAL, AGENT_OBSERVE_API_DURATION, AGENT_VOLUMES_TOTAL
from codehub_agent.runtimes.docker.lock import get_workspace_lock
from codehub_agent.runtimes.protocols import RuntimeProtocol

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


# =============================================================================
# Observe Endpoint (Main)
# =============================================================================


@router.get("", response_model=ObserveResponse)
async def observe(
    runtime: RuntimeProtocol = Depends(get_runtime),
) -> ObserveResponse:
    """Return complete state snapshot of all workspaces."""

    async def _timed_containers():
        start = time.monotonic()
        result = await runtime.instances.list_all()
        AGENT_OBSERVE_API_DURATION.labels(api="containers").observe(time.monotonic() - start)
        return result

    async def _timed_volumes():
        start = time.monotonic()
        result = await runtime.volumes.list_all()
        AGENT_OBSERVE_API_DURATION.labels(api="volumes").observe(time.monotonic() - start)
        return result

    async def _timed_archives():
        start = time.monotonic()
        result = await runtime.storage.list_archives_and_markers()
        AGENT_OBSERVE_API_DURATION.labels(api="archives").observe(time.monotonic() - start)
        return result

    containers, volumes, (archives, restore_markers, error_markers) = await asyncio.gather(
        _timed_containers(),
        _timed_volumes(),
        _timed_archives(),
    )

    AGENT_CONTAINERS_TOTAL.set(len(containers))
    AGENT_VOLUMES_TOTAL.set(len(volumes))

    workspace_data: dict[str, dict] = defaultdict(
        lambda: {
            "container": None,
            "volume": None,
            "archive": None,
            "restore": None,
            "error": None,
        }
    )

    for c in containers:
        workspace_data[c.workspace_id]["container"] = c
    for v in volumes:
        workspace_data[v.workspace_id]["volume"] = v
    for a in archives:
        workspace_data[a.workspace_id]["archive"] = a
    for r in restore_markers:
        workspace_data[r.workspace_id]["restore"] = r
    for e in error_markers:
        workspace_data[e.workspace_id]["error"] = e

    workspaces = []
    for ws_id, data in workspace_data.items():
        container_info = data["container"]
        volume_info = data["volume"]
        archive_info = data["archive"]
        restore_info = data["restore"]
        error_info = data["error"]

        state = WorkspaceState(
            workspace_id=ws_id,
            container=(
                ContainerStatus(
                    running=container_info.running,
                    healthy=container_info.healthy,
                )
                if container_info
                else None
            ),
            volume=(
                VolumeStatus(
                    exists=volume_info.exists,
                )
                if volume_info
                else None
            ),
            archive=(
                ArchiveStatus(
                    latest=(
                        ArchiveLatest(
                            archive_key=archive_info.archive_key,
                            archive_op_id=archive_info.archive_op_id,
                            archived_at=archive_info.archived_at.isoformat() if archive_info.archived_at else "",
                        )
                        if archive_info.archive_key and archive_info.archive_op_id and archive_info.archived_at
                        else None
                    ),
                )
                if archive_info
                else None
            ),
            restore=(
                RestoreStatus(
                    last=(
                        RestoreLast(
                            source_archive_key=restore_info.archive_key,
                            restore_op_id=restore_info.restore_op_id,
                            restored_at=restore_info.restored_at or "",
                        )
                        if restore_info.restored_at
                        else None
                    ),
                )
                if restore_info
                else None
            ),
            error=(
                ErrorStatus(
                    last=(
                        ErrorLast(
                            operation=error_info.operation,
                            code=str(error_info.error_code),
                            error_at=error_info.error_at,
                            archive_op_id=error_info.archive_op_id,
                            restore_op_id=error_info.restore_op_id,
                        )
                    ),
                )
                if error_info
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
    runtime: RuntimeProtocol = Depends(get_runtime),
) -> OperationResponse:
    async with get_workspace_lock(workspace_id):
        result = await runtime.volumes.create(workspace_id)
        return OperationResponse(
            status=OperationStatusValue(result.status.value),
            workspace_id=workspace_id,
        )


@router.post("/{workspace_id}/start", response_model=OperationResponse)
async def start(
    workspace_id: str,
    request: StartRequest,
    runtime: RuntimeProtocol = Depends(get_runtime),
) -> OperationResponse:
    """Start workspace container. Fire-and-Forget."""
    async with get_workspace_lock(workspace_id):
        await runtime.prepare_start(workspace_id)
        spawn_background_task(
            runtime.instances.start(workspace_id, request.image),
            {"workspace_id": workspace_id, "operation": "start"},
        )
    return OperationResponse(
        status=OperationStatusValue.in_progress,
        workspace_id=workspace_id,
    )


@router.post("/{workspace_id}/stop", response_model=OperationResponse)
async def stop(
    workspace_id: str,
    runtime: RuntimeProtocol = Depends(get_runtime),
) -> OperationResponse:
    """Stop workspace container. Fire-and-Forget."""
    async with get_workspace_lock(workspace_id):
        spawn_background_task(
            runtime.instances.delete(workspace_id),
            {"workspace_id": workspace_id, "operation": "stop"},
        )
        return OperationResponse(
            status=OperationStatusValue.in_progress,
            workspace_id=workspace_id,
        )


@router.delete("/{workspace_id}", response_model=OperationResponse)
async def delete(
    workspace_id: str,
    runtime: RuntimeProtocol = Depends(get_runtime),
) -> OperationResponse:
    """Delete workspace completely. Fire-and-Forget."""
    async with get_workspace_lock(workspace_id):
        spawn_background_task(
            runtime.delete_workspace(workspace_id),
            {"workspace_id": workspace_id, "operation": "delete"},
        )
    return OperationResponse(
        status=OperationStatusValue.in_progress,
        workspace_id=workspace_id,
    )


# =============================================================================
# Persistence Endpoints
# =============================================================================


@router.post("/{workspace_id}/archive", response_model=ArchiveResponse)
async def archive(
    workspace_id: str,
    request: ArchiveRequest,
    runtime: RuntimeProtocol = Depends(get_runtime),
) -> ArchiveResponse:
    """Archive workspace to S3. Fire-and-Forget."""
    async with get_workspace_lock(workspace_id):
        result = await runtime.prepare_archive(workspace_id, request.archive_op_id)

    if result.should_run_job:
        spawn_background_task(
            runtime.jobs.run_archive(workspace_id, request.archive_op_id),
            {
                "workspace_id": workspace_id,
                "operation": "archive",
                "archive_op_id": request.archive_op_id,
            },
        )

    return ArchiveResponse(
        status=OperationStatusValue.in_progress,
        workspace_id=workspace_id,
        archive_key=result.archive_key,
    )


@router.post("/{workspace_id}/restore", response_model=RestoreResponse)
async def restore(
    workspace_id: str,
    request: RestoreRequest,
    runtime: RuntimeProtocol = Depends(get_runtime),
) -> RestoreResponse:
    """Restore workspace from S3 archive. Fire-and-Forget."""
    async with get_workspace_lock(workspace_id):
        result = await runtime.prepare_restore(
            workspace_id, request.archive_key, request.restore_op_id
        )

    if result.should_run_job:
        spawn_background_task(
            runtime.jobs.run_restore(
                workspace_id, request.archive_key, request.restore_op_id
            ),
            {
                "workspace_id": workspace_id,
                "operation": "restore",
                "restore_op_id": request.restore_op_id,
                "archive_key": request.archive_key,
            },
        )

    return RestoreResponse(
        status=OperationStatusValue.in_progress,
        workspace_id=workspace_id,
        restore_marker=result.restore_marker,
    )


# =============================================================================
# Routing Endpoint
# =============================================================================


@router.get("/{workspace_id}/upstream", response_model=UpstreamResponse)
async def get_upstream(
    workspace_id: str,
    runtime: RuntimeProtocol = Depends(get_runtime),
) -> UpstreamResponse:
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
    runtime: RuntimeProtocol = Depends(get_runtime),
) -> GCResponse:
    """Run garbage collection on archives."""
    deleted_count, deleted_keys = await runtime.storage.run_gc(
        request.archive_keys,
        [p.root for p in request.protected_workspaces],
        request.retention_count or 3,
    )
    return GCResponse(deleted_count=deleted_count, deleted_keys=deleted_keys)
