"""code-hub Backend - Minimal FastAPI Application for M1 Foundation."""

import asyncio
import logging
from contextlib import asynccontextmanager

import docker
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.errors import CodeHubError, InternalError
from app.core.security import hash_password
from app.db import User, close_db, get_engine, init_db

logger = logging.getLogger(__name__)

# Initial admin username (fixed)
INITIAL_ADMIN_USERNAME = "admin"


async def _create_initial_admin(session: AsyncSession, password: str) -> None:
    """Create initial admin user if not exists.

    Args:
        session: Async database session
        password: Initial admin password
    """
    result = await session.execute(
        select(User).where(User.username == INITIAL_ADMIN_USERNAME)
    )
    existing_user = result.scalar_one_or_none()

    if existing_user:
        logger.info("Initial admin already exists: %s", INITIAL_ADMIN_USERNAME)
        return

    admin = User(
        username=INITIAL_ADMIN_USERNAME,
        password_hash=hash_password(password),
    )
    session.add(admin)
    await session.commit()
    logger.info("Initial admin created: %s", INITIAL_ADMIN_USERNAME)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application lifespan handler - initializes DB and validates config on startup."""
    settings = get_settings()
    print(f"[config] Server bind: {settings.server.bind}")
    print(f"[config] Public base URL: {settings.server.public_base_url}")
    print(f"[config] Home store backend: {settings.home_store.backend}")
    print(f"[config] Home store control_plane_base_dir: {settings.home_store.control_plane_base_dir}")
    print(f"[config] Database URL: {settings.database.url}")

    # Initialize database with WAL mode
    await init_db(settings.database.url, settings.database.echo)

    # Create initial admin user
    engine = get_engine()
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        await _create_initial_admin(session, settings.auth.initial_admin_password)

    yield

    # Cleanup
    await close_db()


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
