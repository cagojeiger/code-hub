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

from codehub import __version__
from codehub.adapters.instance import DockerInstanceController
from codehub.adapters.storage import S3StorageProvider
from codehub.app.api.v1 import auth_router, events_router, workspaces_router
from codehub.app.config import get_settings
from codehub.app.logging import setup_logging
from codehub.app.middleware import LoggingMiddleware
from codehub.core.errors import CodeHubError
from codehub.core.logging_schema import LogEvent
from codehub.core.models import User
from codehub.core.security import hash_password
from codehub.app.proxy import router as proxy_router
from codehub.app.proxy.activity import get_activity_buffer
from codehub.app.proxy.client import close_http_client
from codehub.app.metrics import setup_metrics, get_metrics_response
from codehub.app.metrics.collector import (
    POSTGRESQL_CONNECTED_WORKERS,
    POSTGRESQL_MAX_OVERFLOW,
    POSTGRESQL_POOL_ACTIVE,
    POSTGRESQL_POOL_IDLE,
    POSTGRESQL_POOL_OVERFLOW,
    POSTGRESQL_POOL_SIZE,
    POSTGRESQL_POOL_TOTAL,
    REDIS_CONNECTED_WORKERS,
    REDIS_MAX_CONNECTIONS,
    REDIS_POOL_ACTIVE,
    REDIS_POOL_IDLE,
    REDIS_POOL_TOTAL,
    WORKERS_TOTAL,
)
from codehub.control.coordinator import (
    ArchiveGC,
    EventListener,
    ObserverCoordinator,
    TTLManager,
    WorkspaceController,
)
from codehub.infra.pg_leader import SQLAlchemyLeaderElection
from codehub.infra import (
    close_db,
    close_docker,
    close_redis,
    close_storage,
    get_activity_store,
    get_engine,
    get_redis,
    get_s3_client,
    init_db,
    init_redis,
    init_storage,
)
from codehub.infra.redis_pubsub import ChannelPublisher, ChannelSubscriber

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
        logger.info(
            "Ensured admin user",
            extra={"event": LogEvent.APP_STARTED, "username": username},
        )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.metrics.enabled:
        setup_metrics(settings.metrics.multiproc_dir)
        _init_config_metrics()

    await init_db()
    await init_redis()
    await init_storage()
    await _ensure_admin_user()

    logger.info("Starting application", extra={"event": LogEvent.APP_STARTED})

    coordinator_task = asyncio.create_task(_run_coordinators())
    metrics_task = asyncio.create_task(_metrics_updater_loop())

    yield

    logger.info("Shutting down application", extra={"event": LogEvent.APP_STOPPED})
    coordinator_task.cancel()
    metrics_task.cancel()
    try:
        await coordinator_task
    except asyncio.CancelledError:
        pass
    try:
        await metrics_task
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

    # Redis wrappers
    publisher = ChannelPublisher(redis_client)
    activity_store = get_activity_store()

    def make_runner(coordinator_cls: type, *args) -> callable:
        """Factory for coordinator runner coroutines.

        Uses coordinator_cls.COORDINATOR_TYPE to create LeaderElection.
        Uses Redis PUB/SUB for wake notifications (broadcasting to all coordinators).
        """
        async def runner() -> None:
            async with engine.connect() as conn:
                leader = SQLAlchemyLeaderElection(conn, coordinator_cls.COORDINATOR_TYPE)
                subscriber = ChannelSubscriber(redis_client)
                coordinator = coordinator_cls(conn, leader, subscriber, *args)
                await coordinator.run()
        return runner

    async def event_listener_runner() -> None:
        """Run EventListener.

        Uses asyncpg connection for PG LISTEN support.
        Uses PostgreSQL Advisory Lock for leader election (only 1 instance writes).
        """
        settings = get_settings()
        # Convert SQLAlchemy URL to asyncpg URL (remove +asyncpg suffix)
        db_url = settings.database.url.replace("+asyncpg", "")
        listener = EventListener(db_url, redis_client)
        await listener.run()

    async def activity_buffer_flush_loop() -> None:
        """Flush activity buffer to Redis periodically.

        Runs based on ActivityConfig.flush_interval to batch memory buffer to Redis.
        TTL Manager then syncs Redis to DB every 60 seconds.

        Reference: docs/architecture_v2/ttl-manager.md
        """
        flush_interval = get_settings().activity.flush_interval
        buffer = get_activity_buffer()
        while True:
            await asyncio.sleep(flush_interval)
            try:
                count = await buffer.flush(activity_store)
                if count > 0:
                    logger.debug("Flushed %d activities to Redis", count)
            except Exception as e:
                logger.warning(
                    "Activity buffer flush error",
                    extra={"event": LogEvent.REDIS_CONNECTION_ERROR, "error": str(e)},
                )

    try:
        await asyncio.gather(
            make_runner(ObserverCoordinator, ic, sp)(),
            make_runner(WorkspaceController, ic, sp)(),
            make_runner(TTLManager, activity_store, publisher)(),
            make_runner(ArchiveGC, sp, ic)(),
            event_listener_runner(),
            activity_buffer_flush_loop(),
        )
    except asyncio.CancelledError:
        logger.info("Coordinators cancelled", extra={"event": LogEvent.APP_STOPPED})
        raise
    except Exception as e:
        logger.exception(
            "Coordinator error",
            extra={"event": LogEvent.APP_STOPPED, "error": str(e)},
        )
    finally:
        await ic.close()
        await sp.close()


app = FastAPI(title="CodeHub", version=__version__, lifespan=lifespan)
app.add_middleware(LoggingMiddleware)


@app.exception_handler(CodeHubError)
async def codehub_error_handler(request: Request, exc: CodeHubError) -> JSONResponse:
    """Handle CodeHubError exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response().model_dump(),
    )


app.include_router(auth_router, prefix="/api/v1")
app.include_router(events_router, prefix="/api/v1")
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
        "version": __version__,
        "services": services,
    }


def _init_config_metrics() -> None:
    """Initialize configuration metrics (called once at startup)."""
    settings = get_settings()
    POSTGRESQL_POOL_SIZE.set(settings.database.pool_size)
    POSTGRESQL_MAX_OVERFLOW.set(settings.database.max_overflow)
    REDIS_MAX_CONNECTIONS.set(settings.redis.max_connections)
    workers = int(os.getenv("WORKERS", "1"))
    WORKERS_TOTAL.set(workers)


def _update_postgresql_pool_metrics() -> None:
    """Update PostgreSQL pool metrics."""
    try:
        engine = get_engine()
        pool = engine.pool
        idle = pool.checkedin()
        active = pool.checkedout()
        overflow = pool.overflow()

        POSTGRESQL_CONNECTED_WORKERS.set(1)
        POSTGRESQL_POOL_IDLE.set(idle)
        POSTGRESQL_POOL_ACTIVE.set(active)
        POSTGRESQL_POOL_TOTAL.set(idle + active)
        POSTGRESQL_POOL_OVERFLOW.set(overflow)
    except Exception:
        POSTGRESQL_CONNECTED_WORKERS.set(0)


def _update_redis_pool_metrics() -> None:
    """Update Redis pool metrics."""
    try:
        client = get_redis()
        pool = client.connection_pool

        # redis-py ConnectionPool internal attributes
        idle = len(pool._available_connections)
        active = len(pool._in_use_connections)

        REDIS_CONNECTED_WORKERS.set(1)
        REDIS_POOL_IDLE.set(idle)
        REDIS_POOL_ACTIVE.set(active)
        REDIS_POOL_TOTAL.set(idle + active)
    except Exception:
        REDIS_CONNECTED_WORKERS.set(0)


async def _metrics_updater_loop() -> None:
    """Update metrics periodically in background.

    Runs in each worker to keep pool metrics fresh.
    Interval is configured via METRICS_UPDATE_INTERVAL.
    """
    interval = get_settings().metrics.update_interval
    while True:
        _update_postgresql_pool_metrics()
        _update_redis_pool_metrics()
        await asyncio.sleep(interval)


@app.get("/metrics", include_in_schema=False)
async def metrics():
    """Prometheus metrics endpoint."""
    return get_metrics_response()


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
