"""code-hub Backend - Minimal FastAPI Application for M1 Foundation."""

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
