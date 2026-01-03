"""FastAPI application entry point."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from codehub.adapters.instance import DockerInstanceController
from codehub.core.errors import CodeHubError
from codehub.core.models import User
from codehub.core.security import hash_password
from codehub.adapters.storage import S3StorageProvider
from codehub.app.api.v1 import auth_router, workspaces_router
from codehub.app.logging import setup_logging
from codehub.app.proxy import router as proxy_router
from codehub.app.proxy.client import close_http_client
from codehub.control.coordinator import (
    ArchiveGC,
    ObserverCoordinator,
    TTLManager,
    WorkspaceController,
)
from codehub.control.coordinator.base import (
    LeaderElection,
    NotifyPublisher,
    NotifySubscriber,
)
from codehub.infra import (
    close_db,
    close_docker,
    close_redis,
    close_storage,
    get_engine,
    get_redis,
    get_s3_client,
    init_db,
    init_redis,
    init_storage,
)

setup_logging()
logger = logging.getLogger(__name__)


async def _ensure_admin_user() -> None:
    """Create or update admin user from environment variables.

    Uses PostgreSQL upsert to handle concurrent worker startup safely.

    Env vars:
    - ADMIN_USERNAME: Admin username (default: admin)
    - ADMIN_PASSWORD: Admin password (default: qwer1234)
    """
    from datetime import UTC, datetime

    from ulid import ULID

    username = os.getenv("ADMIN_USERNAME", "admin")
    password = os.getenv("ADMIN_PASSWORD", "qwer1234")
    password_hash = hash_password(password)
    now = datetime.now(UTC)

    engine = get_engine()
    async with AsyncSession(engine) as session:
        stmt = insert(User).values(
            id=str(ULID()),
            username=username,
            password_hash=password_hash,
            created_at=now,
            failed_login_attempts=0,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["username"],
            set_={"password_hash": stmt.excluded.password_hash},
        )
        await session.execute(stmt)
        await session.commit()
        logger.info("Ensured admin user: %s", username)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    await init_redis()
    await init_storage()
    await _ensure_admin_user()

    logger.info("[main] Starting application")

    coordinator_task = asyncio.create_task(_run_coordinators())

    yield

    logger.info("[main] Shutting down application")
    coordinator_task.cancel()
    try:
        await coordinator_task
    except asyncio.CancelledError:
        pass

    await close_http_client()
    await close_docker()
    await close_storage()
    await close_redis()
    await close_db()


async def _run_coordinators() -> None:
    """Run all coordinators with separate DB connections.

    Each coordinator gets its own connection to avoid SQLAlchemy
    AsyncConnection sharing issues across concurrent tasks.
    """
    engine = get_engine()
    redis_client = get_redis()

    # Adapters (thread-safe, can be shared)
    ic = DockerInstanceController()
    sp = S3StorageProvider()

    # Shared publisher (Observer → WC wakeup)
    publisher = NotifyPublisher(redis_client)

    def make_runner(coordinator_cls: type, *args) -> callable:
        """Factory for coordinator runner coroutines.

        Uses coordinator_cls.COORDINATOR_TYPE to create LeaderElection.
        """
        async def runner() -> None:
            async with engine.connect() as conn:
                leader = LeaderElection(conn, coordinator_cls.COORDINATOR_TYPE)
                notify = NotifySubscriber(redis_client)
                coordinator = coordinator_cls(conn, leader, notify, *args)
                await coordinator.run()
        return runner

    try:
        await asyncio.gather(
            make_runner(ObserverCoordinator, ic, sp, publisher)(),
            make_runner(WorkspaceController, ic, sp)(),
            make_runner(TTLManager)(),
            make_runner(ArchiveGC)(),
        )
    except asyncio.CancelledError:
        logger.info("[main] Coordinators cancelled")
        raise
    except Exception as e:
        logger.exception("[main] Coordinator error: %s", e)
    finally:
        await ic.close()
        await sp.close()


app = FastAPI(title="CodeHub", version="0.1.0", lifespan=lifespan)


@app.exception_handler(CodeHubError)
async def codehub_error_handler(request: Request, exc: CodeHubError) -> JSONResponse:
    """Handle CodeHubError exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response().model_dump(),
    )


# Register routers
app.include_router(auth_router, prefix="/api/v1")
app.include_router(workspaces_router, prefix="/api/v1")
app.include_router(proxy_router)


async def _check_service(check_fn: callable) -> str:
    """Check service health and return status string."""
    try:
        await check_fn()
        return "connected"
    except RuntimeError:
        return "not initialized"
    except Exception as e:
        return f"error: {e}"


async def _check_postgres() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis() -> None:
    redis_client = get_redis()
    await redis_client.ping()


async def _check_s3() -> None:
    async with get_s3_client() as s3:
        await s3.list_buckets()


@app.get("/health")
async def health():
    results = await asyncio.gather(
        _check_service(_check_postgres),
        _check_service(_check_redis),
        _check_service(_check_s3),
    )

    services = {
        "postgres": results[0],
        "redis": results[1],
        "s3": results[2],
    }

    is_degraded = any(s != "connected" for s in services.values())

    return {
        "status": "degraded" if is_degraded else "ok",
        "services": services,
    }


# Static files
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
