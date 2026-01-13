"""Storage GC API endpoint."""

import logging

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from codehub_agent.config import get_agent_config

router = APIRouter(prefix="/storage", tags=["storage"])
logger = logging.getLogger(__name__)


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


def _build_archive_key(cluster_id: str, workspace_id: str, op_id: str) -> str:
    """Build S3 archive key."""
    return f"{cluster_id}/{workspace_id}/{op_id}/home.tar.zst"


async def _list_s3_objects(config, prefix: str) -> list[str]:
    """List S3 objects with given prefix."""
    # Use simple HTTP client to list objects
    async with httpx.AsyncClient() as client:
        url = f"{config.s3_endpoint}/{config.s3_bucket}"
        params = {"prefix": prefix, "list-type": "2"}

        # MinIO/S3 requires signing, but for simplicity use boto3-like approach
        # For now, this is a placeholder - real implementation would use aioboto3
        logger.warning("S3 GC listing not fully implemented - requires S3 client")
        return []


async def _delete_s3_object(config, key: str) -> bool:
    """Delete S3 object."""
    logger.warning("S3 GC delete not fully implemented - requires S3 client")
    return False


@router.post("/gc", response_model=GCResponse)
async def run_gc(request: GCRequest) -> GCResponse:
    """Run storage garbage collection.

    Deletes archives not in the protected list.
    Only deletes archives within this cluster's prefix.
    """
    config = get_agent_config()
    cluster_id = config.cluster_id

    # Build set of protected keys
    protected_keys = {
        _build_archive_key(cluster_id, item.workspace_id, item.op_id)
        for item in request.protected
    }

    try:
        # List all archives in this cluster
        all_keys = await _list_s3_objects(config, f"{cluster_id}/")

        # Find keys to delete
        keys_to_delete = [key for key in all_keys if key not in protected_keys]

        # Delete unprotected keys
        deleted_keys = []
        for key in keys_to_delete:
            if await _delete_s3_object(config, key):
                deleted_keys.append(key)

        logger.info("GC completed: deleted %d archives", len(deleted_keys))
        return GCResponse(deleted_count=len(deleted_keys), deleted_keys=deleted_keys)

    except Exception as e:
        logger.error("GC failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
