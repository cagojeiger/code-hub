"""CodeHub Agent FastAPI application."""

import logging
from contextlib import asynccontextmanager
from typing import Callable

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from codehub_agent import __version__
from codehub_agent.api.v1 import (
    health_router,
    workspaces_router,
)
from codehub_agent.api.errors import AgentError
from codehub_agent.config import get_agent_config
from codehub_agent.api.dependencies import close_runtime, init_runtime
from codehub_agent.infra import ContainerAPI, close_docker
from codehub_agent.logging import setup_logging
from codehub_agent.logging_schema import LogEvent

# Import metrics to ensure they are registered
import codehub_agent.metrics  # noqa: F401

# Configure logging using config
_config = get_agent_config()
setup_logging(_config.logging)
logger = logging.getLogger(__name__)


async def cleanup_orphaned_job_containers() -> None:
    """Startup cleanup for orphaned job containers.

    Job containers (codehub-job-*) may be left behind if the agent
    restarts while jobs are running. These orphaned containers block
    volume deletion, causing ARCHIVING operations to fail.

    Cleanup is parallelized for faster startup.
    """
    import asyncio

    logger.info("Cleaning up orphaned job containers", extra={"event": LogEvent.CLEANUP_STARTED})
    api = ContainerAPI()
    try:
        containers = await api.list(filters={"name": ["codehub-job-"]})
        if not containers:
            logger.info(
                "Cleanup complete",
                extra={"event": LogEvent.CLEANUP_COMPLETED, "removed_count": 0},
            )
            return

        # Extract container names
        names = [c["Names"][0].lstrip("/") for c in containers]

        # Log containers to be removed
        for name in names:
            logger.info(
                "Removing orphaned job container",
                extra={"event": LogEvent.CONTAINER_REMOVED, "container": name},
            )

        # Remove all containers in parallel
        await asyncio.gather(
            *[api.remove(name, force=True) for name in names],
            return_exceptions=True,
        )

        logger.info(
            "Cleanup complete",
            extra={"event": LogEvent.CLEANUP_COMPLETED, "removed_count": len(containers)},
        )
    except Exception as e:
        logger.warning(
            "Failed to cleanup job containers",
            extra={"event": LogEvent.CLEANUP_FAILED, "error": str(e)},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info(
        "Starting CodeHub Agent",
        extra={
            "event": LogEvent.APP_STARTED,
            "version": __version__,
        },
    )

    # Initialize runtime (includes S3)
    await init_runtime()

    # Cleanup orphaned job containers from previous runs
    await cleanup_orphaned_job_containers()

    yield
    logger.info("Shutting down CodeHub Agent", extra={"event": LogEvent.APP_STOPPED})
    await close_runtime()
    await close_docker()


app = FastAPI(
    title="CodeHub Agent",
    description="Runtime agent for workspace management",
    version=__version__,
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=_config.server.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Error handler for AgentError
@app.exception_handler(AgentError)
async def agent_error_handler(request: Request, exc: AgentError) -> JSONResponse:
    """Handle AgentError exceptions."""
    logger.warning(
        "Agent error",
        extra={
            "event": LogEvent.AGENT_ERROR,
            "error_code": exc.code.value,
            "error_message": exc.message,
            "path": request.url.path,
            "method": request.method,
        },
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response().model_dump(),
    )


# Error handler for unhandled exceptions
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unhandled exceptions with logging."""
    logger.exception(
        "Unhandled exception",
        extra={
            "event": LogEvent.UNHANDLED_EXCEPTION,
            "path": request.url.path,
            "method": request.method,
        },
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# API key authentication middleware
@app.middleware("http")
async def api_key_middleware(
    request: Request, call_next: Callable[[Request], Response]
) -> Response:
    """Validate API key for non-health endpoints."""
    config = get_agent_config()

    # Skip auth for health and metrics endpoints
    if request.url.path in ("/health", "/metrics"):
        return await call_next(request)

    # If API key is configured, validate it
    if config.server.api_key:
        auth_header = request.headers.get("Authorization", "")
        expected = f"Bearer {config.server.api_key}"
        if auth_header != expected:
            return Response(
                content='{"detail": "Invalid API key"}',
                status_code=401,
                media_type="application/json",
            )

    return await call_next(request)


# Register routers
# /health endpoint without prefix (for health checks)
app.include_router(health_router)


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Expose Prometheus metrics."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


# API v1 endpoints with /api/v1 prefix (Fire-and-Forget pattern)
app.include_router(workspaces_router, prefix="/api/v1")


def main() -> None:
    """Run the agent server."""
    config = get_agent_config()
    uvicorn.run(
        "codehub_agent.main:app",
        host=config.server.host,
        port=config.server.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
