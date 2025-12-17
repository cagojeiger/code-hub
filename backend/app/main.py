"""code-hub Backend - Minimal FastAPI Application for M1 Foundation."""

import asyncio
from contextlib import asynccontextmanager

import docker
from fastapi import FastAPI

from app.core.config import Settings, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    settings = get_settings()
    app.state.settings = settings
    yield


app = FastAPI(
    title="code-hub",
    description="Cloud Development Environment Platform - Local MVP",
    version="0.1.0",
    lifespan=lifespan,
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


@app.get("/debug/config")
async def show_config() -> dict:
    """Show current configuration (development only)."""
    settings = get_settings()
    if settings.env != "development":
        return {"error": "Config endpoint only available in development"}

    return {
        "env": settings.env,
        "server": {
            "bind": settings.server.bind,
            "public_base_url": settings.server.public_base_url,
        },
        "auth": {
            "mode": settings.auth.mode,
            "session": {
                "cookie_name": settings.auth.session.cookie_name,
                "ttl_seconds": settings.auth.session.ttl_seconds,
            },
        },
        "workspace": {
            "default_image": settings.workspace.default_image,
            "healthcheck": {
                "type": settings.workspace.healthcheck.type,
                "path": settings.workspace.healthcheck.path,
                "interval_seconds": settings.workspace.healthcheck.interval_seconds,
                "timeout_seconds": settings.workspace.healthcheck.timeout_seconds,
            },
        },
        "home_store": {
            "backend": settings.home_store.backend,
            "base_dir": settings.home_store.base_dir,
            "host_path": settings.home_store.host_path,
        },
    }
