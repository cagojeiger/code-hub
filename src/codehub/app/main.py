"""FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from codehub.adapters.instance import DockerInstanceController
from codehub.core.errors import CodeHubError
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
    CoordinatorType,
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


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    await init_redis()
    await init_storage()

    logger.info("Starting")

    coordinator_task = asyncio.create_task(_run_coordinators())

    yield

    logger.info("Shutting down")
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
    engine = get_engine()
    redis_client = get_redis()

    # Adapters
    ic = DockerInstanceController()
    sp = S3StorageProvider()

    # Shared publisher (Observer → WC wakeup)
    publisher = NotifyPublisher(redis_client)

    try:
        async with engine.connect() as conn:
            # Per-coordinator DI
            ob_leader = LeaderElection(conn, CoordinatorType.OBSERVER)
            ob_notify = NotifySubscriber(redis_client)

            wc_leader = LeaderElection(conn, CoordinatorType.WC)
            wc_notify = NotifySubscriber(redis_client)

            ttl_leader = LeaderElection(conn, CoordinatorType.TTL)
            ttl_notify = NotifySubscriber(redis_client)

            gc_leader = LeaderElection(conn, CoordinatorType.GC)
            gc_notify = NotifySubscriber(redis_client)

            coordinators = [
                ObserverCoordinator(conn, ob_leader, ob_notify, ic, sp, publisher),
                WorkspaceController(conn, wc_leader, wc_notify, ic, sp),
                TTLManager(conn, ttl_leader, ttl_notify),
                ArchiveGC(conn, gc_leader, gc_notify),
            ]
            # gather가 cancel되면 모든 자식 task를 cancel하고
            # 각 run()에서 _cleanup() 호출됨
            await asyncio.gather(*[c.run() for c in coordinators])
    except asyncio.CancelledError:
        logger.info("Coordinators cancelled")
        raise
    except Exception as e:
        logger.exception("Coordinator error: %s", e)
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


@app.get("/health")
async def health():
    status: dict = {"status": "ok", "services": {}}

    try:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        status["services"]["postgres"] = "connected"
    except RuntimeError:
        status["services"]["postgres"] = "not initialized"
        status["status"] = "degraded"
    except Exception as e:
        status["services"]["postgres"] = f"error: {e}"
        status["status"] = "degraded"

    try:
        redis_client = get_redis()
        await redis_client.ping()
        status["services"]["redis"] = "connected"
    except Exception as e:
        status["services"]["redis"] = f"error: {e}"
        status["status"] = "degraded"

    try:
        async with get_s3_client() as s3:
            await s3.list_buckets()
        status["services"]["s3"] = "connected"
    except Exception as e:
        status["services"]["s3"] = f"error: {e}"
        status["status"] = "degraded"

    return status


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
