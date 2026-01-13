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
from codehub_agent.infra import close_docker

# Import metrics to ensure they are registered
import codehub_agent.metrics  # noqa: F401

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    config = get_agent_config()
    logger.info(
        "Starting CodeHub Agent v%s (cluster_id=%s)",
        __version__,
        config.cluster_id,
    )
    yield
    logger.info("Shutting down CodeHub Agent")
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Error handler for AgentError
@app.exception_handler(AgentError)
async def agent_error_handler(request: Request, exc: AgentError) -> JSONResponse:
    """Handle AgentError exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response().model_dump(),
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
    if config.api_key:
        auth_header = request.headers.get("Authorization", "")
        expected = f"Bearer {config.api_key}"
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


# API v1 endpoints with /api/v1 prefix
app.include_router(workspaces_router, prefix="/api/v1")


def main() -> None:
    """Run the agent server."""
    config = get_agent_config()
    uvicorn.run(
        "codehub_agent.main:app",
        host=config.host,
        port=config.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
