"""code-hub Backend - Minimal FastAPI Application for M1 Foundation."""

import asyncio
import logging
from contextlib import asynccontextmanager

import docker
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import CodeHubError, InternalError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application lifespan handler - validates config on startup."""
    settings = get_settings()
    print(f"[config] Server bind: {settings.server.bind}")
    print(f"[config] Public base URL: {settings.server.public_base_url}")
    print(f"[config] Home store backend: {settings.home_store.backend}")
    print(f"[config] Home store control_plane_base_dir: {settings.home_store.control_plane_base_dir}")
    yield


app = FastAPI(
    title="code-hub",
    description="Cloud Development Environment Platform - Local MVP",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(CodeHubError)
async def codehub_error_handler(_request: Request, exc: CodeHubError) -> JSONResponse:
    """Handle CodeHubError exceptions and return standardized error responses."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response().model_dump(),
    )


@app.exception_handler(Exception)
async def generic_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions and return standardized error responses."""
    logger.exception("Unexpected error: %s", exc)
    error = InternalError()
    return JSONResponse(
        status_code=error.status_code,
        content=error.to_response().model_dump(),
    )


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint for container orchestration."""
    return {"status": "ok"}


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {"message": "code-hub API", "version": "0.1.0"}


@app.get("/debug/containers")
async def list_containers() -> dict:
    """List codehub namespace containers (for testing Docker socket proxy)."""

    def _list():
        client = docker.from_env()
        containers = client.containers.list(all=True)
        return [
            {"name": c.name, "status": c.status, "image": c.image.tags[0] if c.image.tags else "unknown"}
            for c in containers
            if c.name.startswith("codehub-")
        ]

    result = await asyncio.to_thread(_list)
    return {"containers": result}
