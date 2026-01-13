"""S3 client for Agent."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from aiobotocore.session import AioSession, get_session
from types_aiobotocore_s3 import S3Client

from codehub_agent.config import AgentConfig, get_agent_config
from codehub_agent.logging_schema import LogEvent

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
            endpoint_url=self._config.s3.endpoint,
            aws_access_key_id=self._config.s3.access_key,
            aws_secret_access_key=self._config.s3.secret_key,
            region_name=self._config.s3.region,
        ) as client:
            yield client

    async def init(self) -> None:
        """Ensure bucket exists, create if not."""
        bucket = self._config.s3.bucket
        async with self.client() as s3:
            try:
                await s3.head_bucket(Bucket=bucket)
                logger.info(
                    "S3 bucket exists",
                    extra={"event": LogEvent.S3_BUCKET_READY, "bucket": bucket, "created": False},
                )
            except Exception:
                await s3.create_bucket(Bucket=bucket)
                logger.info(
                    "S3 bucket created",
                    extra={"event": LogEvent.S3_BUCKET_READY, "bucket": bucket, "created": True},
                )

    async def list_objects(self, prefix: str) -> list[str]:
        keys = []
        bucket = self._config.s3.bucket
        async with self.client() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
        return keys

    async def delete_object(self, key: str) -> bool:
        try:
            async with self.client() as s3:
                await s3.delete_object(Bucket=self._config.s3.bucket, Key=key)
                logger.debug(
                    "S3 object deleted",
                    extra={"event": LogEvent.S3_OBJECT_DELETED, "key": key},
                )
                return True
        except Exception as e:
            logger.warning(
                "Failed to delete S3 object",
                extra={"event": LogEvent.S3_DELETE_FAILED, "key": key, "error": str(e)},
            )
            return False

    async def delete_objects(self, keys: list[str]) -> list[str]:
        """Delete multiple objects in batch. Returns list of successfully deleted keys."""
        if not keys:
            return []

        deleted_keys: list[str] = []
        bucket = self._config.s3.bucket
        # S3 delete_objects supports up to 1000 keys per request
        batch_size = 1000

        async with self.client() as s3:
            for i in range(0, len(keys), batch_size):
                batch = keys[i : i + batch_size]
                try:
                    response = await s3.delete_objects(
                        Bucket=bucket,
                        Delete={"Objects": [{"Key": key} for key in batch]},
                    )
                    # Collect successfully deleted keys
                    for deleted in response.get("Deleted", []):
                        deleted_keys.append(deleted["Key"])
                    # Log errors if any
                    for error in response.get("Errors", []):
                        logger.warning(
                            "Failed to delete S3 object",
                            extra={
                                "event": LogEvent.S3_DELETE_FAILED,
                                "key": error.get("Key"),
                                "error": error.get("Message"),
                            },
                        )
                except Exception as e:
                    logger.warning(
                        "Batch delete failed",
                        extra={
                            "event": LogEvent.S3_DELETE_FAILED,
                            "batch_size": len(batch),
                            "error": str(e),
                        },
                    )

        return deleted_keys

    async def object_exists(self, key: str) -> bool:
        try:
            async with self.client() as s3:
                await s3.head_object(Bucket=self._config.s3.bucket, Key=key)
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
