"""Job API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from codehub_agent.api.dependencies import get_runtime
from codehub_agent.api.v1.schemas import JobRequest, JobResponse
from codehub_agent.runtimes import DockerRuntime

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/archive", response_model=JobResponse)
async def run_archive(
    request: JobRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> JobResponse:
    """Run archive job (Volume -> S3)."""
    try:
        result = await runtime.jobs.run_archive(request.workspace_id, request.op_id)
        return JobResponse(exit_code=result.exit_code, logs=result.logs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/restore", response_model=JobResponse)
async def run_restore(
    request: JobRequest,
    runtime: DockerRuntime = Depends(get_runtime),
) -> JobResponse:
    """Run restore job (S3 -> Volume)."""
    try:
        result = await runtime.jobs.run_restore(request.workspace_id, request.op_id)
        return JobResponse(exit_code=result.exit_code, logs=result.logs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
