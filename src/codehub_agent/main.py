"""CodeHub Agent FastAPI application."""

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from codehub_agent import __version__
from codehub_agent.api import (
    health_router,
    instances_router,
    jobs_router,
    storage_router,
    volumes_router,
)
from codehub_agent.config import get_agent_config
from codehub_agent.infra import close_docker

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


# API key authentication middleware
@app.middleware("http")
async def api_key_middleware(request: Request, call_next) -> Response:
    """Validate API key for non-health endpoints."""
    config = get_agent_config()

    # Skip auth for health endpoint
    if request.url.path == "/health":
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
app.include_router(health_router)
app.include_router(instances_router)
app.include_router(volumes_router)
app.include_router(jobs_router)
app.include_router(storage_router)


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
