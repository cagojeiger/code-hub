"""Job API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from codehub_agent.runtimes import DockerRuntime

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Singleton runtime instance
_runtime: DockerRuntime | None = None


def get_runtime() -> DockerRuntime:
    """Get runtime singleton."""
    global _runtime
    if _runtime is None:
        _runtime = DockerRuntime()
    return _runtime


class ArchiveRequest(BaseModel):
    """Archive job request."""

    workspace_id: str
    op_id: str


class RestoreRequest(BaseModel):
    """Restore job request."""

    workspace_id: str
    op_id: str


class JobResponse(BaseModel):
    """Job result response."""

    exit_code: int
    logs: str


@router.post("/archive", response_model=JobResponse)
async def run_archive(request: ArchiveRequest) -> JobResponse:
    """Run archive job (Volume -> S3)."""
    runtime = get_runtime()
    try:
        result = await runtime.jobs.run_archive(request.workspace_id, request.op_id)
        return JobResponse(exit_code=result.exit_code, logs=result.logs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/restore", response_model=JobResponse)
async def run_restore(request: RestoreRequest) -> JobResponse:
    """Run restore job (S3 -> Volume)."""
    runtime = get_runtime()
    try:
        result = await runtime.jobs.run_restore(request.workspace_id, request.op_id)
        return JobResponse(exit_code=result.exit_code, logs=result.logs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
