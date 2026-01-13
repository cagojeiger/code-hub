"""Storage GC API endpoint."""

import logging

from fastapi import APIRouter, HTTPException

from codehub_agent.config import get_agent_config
from codehub_agent.api.v1.schemas import GCRequest, GCResponse, ProtectedItem
from codehub_agent.infra import delete_object, list_objects

router = APIRouter(prefix="/storage", tags=["storage"])
logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = ["router", "ProtectedItem", "GCRequest", "GCResponse"]


def _build_archive_key(cluster_id: str, workspace_id: str, op_id: str) -> str:
    """Build S3 archive key."""
    return f"{cluster_id}/{workspace_id}/{op_id}/home.tar.zst"


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
        all_keys = await list_objects(f"{cluster_id}/")

        # Find keys to delete
        keys_to_delete = [key for key in all_keys if key not in protected_keys]

        # Delete unprotected keys
        deleted_keys = []
        for key in keys_to_delete:
            if await delete_object(key):
                deleted_keys.append(key)

        logger.info("GC completed: deleted %d archives", len(deleted_keys))
        return GCResponse(deleted_count=len(deleted_keys), deleted_keys=deleted_keys)

    except Exception as e:
        logger.error("GC failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
