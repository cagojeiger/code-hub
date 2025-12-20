"""code-hub Backend - Minimal FastAPI Application for M1 Foundation."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import docker
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import router as api_v1_router
from app.api.v1.dependencies import get_instance_controller, get_storage_provider
from app.core.config import get_settings
from app.core.errors import CodeHubError, InternalError
from app.core.logging import setup_logging
from app.core.middleware import RequestIdMiddleware
from app.core.security import hash_password
from app.db import User, close_db, get_engine, init_db
from app.proxy import close_http_client
from app.proxy import router as proxy_router
from app.services.recovery import startup_recovery

setup_logging()
logger = logging.getLogger(__name__)

INITIAL_ADMIN_USERNAME = "admin"


async def _create_initial_admin(session: AsyncSession, password: str) -> None:
    """Create initial admin user if not exists."""
    result = await session.execute(
        select(User).where(
            User.username == INITIAL_ADMIN_USERNAME  # type: ignore[arg-type]
        )
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
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan handler - initializes DB and validates config on startup."""
    settings = get_settings()
    logger.info(
        "Configuration loaded",
        extra={
            "server_bind": settings.server.bind,
            "public_base_url": settings.server.public_base_url,
            "home_store_backend": settings.home_store.backend,
            "control_plane_base_dir": settings.home_store.control_plane_base_dir,
            "database_url": settings.database.url.split("@")[-1],  # Hide credentials
        },
    )

    await init_db(settings.database.url, settings.database.echo)

    engine = get_engine()
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        await _create_initial_admin(session, settings.auth.initial_admin_password)

    # Startup recovery: reconcile transitional states before accepting requests
    async with session_factory() as session:
        recovered = await startup_recovery(
            session, get_instance_controller(), get_storage_provider()
        )
        if recovered > 0:
            logger.info("Startup recovery: %d workspace(s) recovered", recovered)

    yield

    await close_http_client()
    await close_db()


app = FastAPI(
    title="code-hub",
    description="Cloud Development Environment Platform - Local MVP",
    version="0.1.0",
    lifespan=lifespan,
)

# Add middleware (order matters: first added = outermost)
app.add_middleware(RequestIdMiddleware)

# Include API routers
app.include_router(api_v1_router)

# Include proxy router (routes: /w/{workspace_id}/*)
app.include_router(proxy_router)


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


# Mount static files
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root() -> FileResponse:
    """Serve the dashboard UI."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login")
async def login_page() -> FileResponse:
    """Serve the login page."""
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/debug/containers")
async def list_containers() -> dict[str, Any]:
    """List codehub namespace containers (for testing Docker socket proxy)."""

    def _list() -> list[dict[str, Any]]:
        client = docker.from_env()
        containers = client.containers.list(all=True)
        result = []
        for c in containers:
            if c.name and c.name.startswith("codehub-"):
                image_tag = "unknown"
                if c.image and c.image.tags:
                    image_tag = c.image.tags[0]
                result.append({
                    "name": c.name,
                    "status": c.status,
                    "image": image_tag,
                })
        return result

    result = await asyncio.to_thread(_list)
    return {"containers": result}
