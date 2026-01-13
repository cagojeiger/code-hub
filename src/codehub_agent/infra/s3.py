"""S3 client for Agent."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from aiobotocore.session import AioSession, get_session
from types_aiobotocore_s3 import S3Client

from codehub_agent.config import AgentConfig, get_agent_config

logger = logging.getLogger(__name__)


class S3Operations:
    """S3 operations with session reuse for connection efficiency."""

    def __init__(self, config: AgentConfig, session: AioSession | None = None) -> None:
        self._config = config
        # Reuse session for connection pooling via aiohttp connector
        self._session = session or get_session()

    @asynccontextmanager
    async def client(self) -> AsyncGenerator[S3Client, None]:
        async with self._session.create_client(
            "s3",
            endpoint_url=self._config.s3_endpoint,
            aws_access_key_id=self._config.s3_access_key,
            aws_secret_access_key=self._config.s3_secret_key,
            region_name="us-east-1",
        ) as client:
            yield client

    async def init(self) -> None:
        """Ensure bucket exists, create if not."""
        async with self.client() as s3:
            try:
                await s3.head_bucket(Bucket=self._config.s3_bucket)
                logger.info("S3 bucket exists: %s", self._config.s3_bucket)
            except Exception:
                await s3.create_bucket(Bucket=self._config.s3_bucket)
                logger.info("Created S3 bucket: %s", self._config.s3_bucket)

    async def list_objects(self, prefix: str) -> list[str]:
        keys = []
        async with self.client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(
                Bucket=self._config.s3_bucket, Prefix=prefix
            ):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
        return keys

    async def delete_object(self, key: str) -> bool:
        try:
            async with self.client() as s3:
                await s3.delete_object(Bucket=self._config.s3_bucket, Key=key)
                logger.debug("Deleted S3 object: %s", key)
                return True
        except Exception as e:
            logger.warning("Failed to delete S3 object %s: %s", key, e)
            return False

    async def delete_objects(self, keys: list[str]) -> list[str]:
        """Delete multiple objects in batch. Returns list of successfully deleted keys."""
        if not keys:
            return []

        deleted_keys: list[str] = []
        # S3 delete_objects supports up to 1000 keys per request
        batch_size = 1000

        async with self.client() as s3:
            for i in range(0, len(keys), batch_size):
                batch = keys[i : i + batch_size]
                try:
                    response = await s3.delete_objects(
                        Bucket=self._config.s3_bucket,
                        Delete={"Objects": [{"Key": key} for key in batch]},
                    )
                    # Collect successfully deleted keys
                    for deleted in response.get("Deleted", []):
                        deleted_keys.append(deleted["Key"])
                    # Log errors if any
                    for error in response.get("Errors", []):
                        logger.warning(
                            "Failed to delete S3 object %s: %s",
                            error.get("Key"),
                            error.get("Message"),
                        )
                except Exception as e:
                    logger.warning("Batch delete failed for %d objects: %s", len(batch), e)

        return deleted_keys

    async def object_exists(self, key: str) -> bool:
        try:
            async with self.client() as s3:
                await s3.head_object(Bucket=self._config.s3_bucket, Key=key)
                return True
        except Exception:
            return False


# Module-level functions for main.py lifespan
_s3_ops: S3Operations | None = None


async def init_s3() -> None:
    global _s3_ops
    _s3_ops = S3Operations(get_agent_config())
    await _s3_ops.init()


async def close_s3() -> None:
    global _s3_ops
    _s3_ops = None
