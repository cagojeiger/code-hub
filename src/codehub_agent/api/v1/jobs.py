"""Job API endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from codehub_agent.api.dependencies import get_runtime
from codehub_agent.runtimes import DockerRuntime

router = APIRouter(prefix="/jobs", tags=["jobs"])


# =============================================================================
# Schemas
# =============================================================================


class JobRequest(BaseModel):
    """Job request for archive/restore."""

    workspace_id: str
    op_id: str


class JobResponse(BaseModel):
    """Job result response."""

    exit_code: int
    logs: str


@router.post("/archive", response_model=JobResponse)
async def run_archive(
    request: JobRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> JobResponse:
    """Run archive job (Volume -> S3)."""
    result = await runtime.jobs.run_archive(request.workspace_id, request.op_id)
    return JobResponse(exit_code=result.exit_code, logs=result.logs)


@router.post("/restore", response_model=JobResponse)
async def run_restore(
    request: JobRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> JobResponse:
    """Run restore job (S3 -> Volume)."""
    result = await runtime.jobs.run_restore(request.workspace_id, request.op_id)
    return JobResponse(exit_code=result.exit_code, logs=result.logs)
