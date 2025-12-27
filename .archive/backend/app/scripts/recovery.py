"""Standalone recovery script for multi-worker deployment.

Runs once before backend workers start to reconcile DB state with actual instance state.
Usage: uv run python -m app.scripts.recovery
"""

import asyncio
import logging
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db import close_db, get_engine, init_db
from app.services.instance.local_docker import LocalDockerInstanceController
from app.services.recovery import startup_recovery
from app.services.storage.local_dir import LocalDirStorageProvider

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> int:
    """Run startup recovery."""
    settings = get_settings()

    logger.info("Starting recovery service")

    # Initialize database
    await init_db(settings.database.url, settings.database.echo, create_tables=False)

    # Create dependencies
    instance_controller = LocalDockerInstanceController(
        container_prefix=settings.workspace.container_prefix,
        network_name=settings.workspace.network_name,
    )
    storage_provider = LocalDirStorageProvider(
        control_plane_base_dir=settings.home_store.control_plane_base_dir,
        workspace_base_dir=settings.home_store.workspace_base_dir,
    )

    # Run recovery
    engine = get_engine()
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_factory() as session:
        recovered = await startup_recovery(session, instance_controller, storage_provider)

    await close_db()

    logger.info("Recovery complete: %d workspace(s) recovered", recovered)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
