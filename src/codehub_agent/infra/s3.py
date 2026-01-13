"""S3 client for Agent.

Provides async S3 access for storage operations.
Uses aiobotocore for async support.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from aiobotocore.session import get_session
from types_aiobotocore_s3 import S3Client

from codehub_agent.config import get_agent_config

logger = logging.getLogger(__name__)

# Global session for connection reuse
_session = None


async def init_s3() -> None:
    """Initialize S3 session and ensure bucket exists."""
    config = get_agent_config()

    async with get_s3_client() as s3:
        try:
            await s3.head_bucket(Bucket=config.s3_bucket)
            logger.info("S3 bucket exists: %s", config.s3_bucket)
        except Exception:
            # Create bucket if not exists
            await s3.create_bucket(Bucket=config.s3_bucket)
            logger.info("Created S3 bucket: %s", config.s3_bucket)


async def close_s3() -> None:
    """Close S3 session."""
    global _session
    _session = None


@asynccontextmanager
async def get_s3_client() -> AsyncGenerator[S3Client, None]:
    """Get S3 client context manager.

    Usage:
        async with get_s3_client() as s3:
            await s3.list_buckets()
    """
    config = get_agent_config()

    session = get_session()
    async with session.create_client(
        "s3",
        endpoint_url=config.s3_endpoint,
        aws_access_key_id=config.s3_access_key,
        aws_secret_access_key=config.s3_secret_key,
        region_name="us-east-1",  # Required but not used by MinIO
    ) as client:
        yield client


async def list_objects(prefix: str) -> list[str]:
    """List S3 objects with given prefix.

    Args:
        prefix: S3 key prefix to list.

    Returns:
        List of object keys.
    """
    config = get_agent_config()
    keys = []

    async with get_s3_client() as s3:
        paginator = s3.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=config.s3_bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])

    return keys


async def delete_object(key: str) -> bool:
    """Delete S3 object.

    Args:
        key: Object key to delete.

    Returns:
        True if deleted successfully.
    """
    config = get_agent_config()

    try:
        async with get_s3_client() as s3:
            await s3.delete_object(Bucket=config.s3_bucket, Key=key)
            logger.debug("Deleted S3 object: %s", key)
            return True
    except Exception as e:
        logger.warning("Failed to delete S3 object %s: %s", key, e)
        return False


async def object_exists(key: str) -> bool:
    """Check if S3 object exists.

    Args:
        key: Object key to check.

    Returns:
        True if object exists.
    """
    config = get_agent_config()

    try:
        async with get_s3_client() as s3:
            await s3.head_object(Bucket=config.s3_bucket, Key=key)
            return True
    except Exception:
        return False
