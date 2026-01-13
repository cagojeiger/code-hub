"""Storage API endpoints for archive management."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from codehub_agent.api.dependencies import get_runtime
from codehub_agent.runtimes import DockerRuntime

router = APIRouter(prefix="/storage", tags=["storage"])


# =============================================================================
# Schemas
# =============================================================================


class ArchiveInfoResponse(BaseModel):
    """Archive info response."""

    workspace_id: str
    archive_key: str | None
    exists: bool
    reason: str
    message: str


class ArchiveListResponse(BaseModel):
    """Archive list response."""

    archives: list[ArchiveInfoResponse]


class ArchiveKeysResponse(BaseModel):
    """Archive keys response."""

    keys: list[str]


class ArchiveDeleteResponse(BaseModel):
    """Archive delete response."""

    deleted: bool
    archive_key: str


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


# =============================================================================
# Archive Endpoints
# =============================================================================


@router.get("/archives", response_model=ArchiveListResponse)
async def list_archives(
    prefix: str = Query(default="", description="Workspace ID prefix to filter"),
    runtime: DockerRuntime = Depends(get_runtime),
) -> ArchiveListResponse:
    """List archives in S3.

    Returns list of archives matching the prefix.
    Each workspace returns at most one archive (the latest).
    """
    archives = await runtime.storage.list_archives(prefix)
    return ArchiveListResponse(
        archives=[
            ArchiveInfoResponse(
                workspace_id=a.workspace_id,
                archive_key=a.archive_key,
                exists=a.exists,
                reason=a.reason,
                message=a.message,
            )
            for a in archives
        ]
    )


@router.get("/archives/keys", response_model=ArchiveKeysResponse)
async def list_archive_keys(
    prefix: str = Query(default="", description="Workspace ID prefix to filter"),
    runtime: DockerRuntime = Depends(get_runtime),
) -> ArchiveKeysResponse:
    """List all archive keys in S3.

    Returns all archive keys (including multiple versions per workspace).
    """
    keys = await runtime.storage.list_all_archive_keys(prefix)
    return ArchiveKeysResponse(keys=list(keys))


@router.delete("/archives", response_model=ArchiveDeleteResponse)
async def delete_archive(
    archive_key: str = Query(..., description="Full S3 key of the archive to delete"),
    runtime: DockerRuntime = Depends(get_runtime),
) -> ArchiveDeleteResponse:
    """Delete an archive from S3."""
    deleted = await runtime.storage.delete_archive(archive_key)
    return ArchiveDeleteResponse(deleted=deleted, archive_key=archive_key)


# =============================================================================
# GC Endpoint
# =============================================================================


@router.post("/gc", response_model=GCResponse)
async def run_gc(
    request: GCRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> GCResponse:
    """Run storage garbage collection.

    Deletes archives not in the protected list.
    Only deletes archives within this cluster's prefix.
    """
    protected = [(item.workspace_id, item.op_id) for item in request.protected]
    deleted_count, deleted_keys = await runtime.storage.run_gc(protected)
    return GCResponse(deleted_count=deleted_count, deleted_keys=deleted_keys)
