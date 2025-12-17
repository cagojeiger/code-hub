"""code-hub Backend - Minimal FastAPI Application for M1 Foundation."""

import asyncio

import docker
from fastapi import FastAPI

app = FastAPI(
    title="code-hub",
    description="Cloud Development Environment Platform - Local MVP",
    version="0.1.0",
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
