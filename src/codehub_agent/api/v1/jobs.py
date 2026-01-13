"""Job API endpoints."""

from fastapi import APIRouter, HTTPException

from codehub_agent.api.dependencies import get_runtime
from codehub_agent.api.v1.schemas import JobRequest, JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/archive", response_model=JobResponse)
async def run_archive(request: JobRequest) -> JobResponse:
    """Run archive job (Volume -> S3)."""
    runtime = get_runtime()
    try:
        result = await runtime.jobs.run_archive(request.workspace_id, request.op_id)
        return JobResponse(exit_code=result.exit_code, logs=result.logs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/restore", response_model=JobResponse)
async def run_restore(request: JobRequest) -> JobResponse:
    """Run restore job (S3 -> Volume)."""
    runtime = get_runtime()
    try:
        result = await runtime.jobs.run_restore(request.workspace_id, request.op_id)
        return JobResponse(exit_code=result.exit_code, logs=result.logs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
