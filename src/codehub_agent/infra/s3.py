"""S3 client for Agent."""

import logging
from contextlib import AsyncExitStack

from aiobotocore.session import AioSession, get_session
from types_aiobotocore_s3 import S3Client

from codehub_agent.config import AgentConfig
from codehub_agent.logging_schema import LogEvent

logger = logging.getLogger(__name__)


class S3Operations:
    """S3 operations with singleton client for connection reuse."""

    def __init__(self, config: AgentConfig, session: AioSession | None = None) -> None:
        self._config = config
        self._session = session or get_session()
        self._exit_stack: AsyncExitStack | None = None
        self._client: S3Client | None = None

    async def init(self) -> None:
        """Initialize S3 client singleton and ensure bucket exists."""
        # Create singleton client using AsyncExitStack
        self._exit_stack = AsyncExitStack()
        self._client = await self._exit_stack.enter_async_context(
            self._session.create_client(
                "s3",
                endpoint_url=self._config.s3.endpoint,
                aws_access_key_id=self._config.s3.access_key,
                aws_secret_access_key=self._config.s3.secret_key,
                region_name=self._config.s3.region,
            )
        )

        # Ensure bucket exists
        bucket = self._config.s3.bucket
        try:
            await self._client.head_bucket(Bucket=bucket)
            logger.info(
                "S3 bucket exists",
                extra={"event": LogEvent.S3_BUCKET_READY, "bucket": bucket, "bucket_created": False},
            )
        except Exception:
            await self._client.create_bucket(Bucket=bucket)
            logger.info(
                "S3 bucket created",
                extra={"event": LogEvent.S3_BUCKET_READY, "bucket": bucket, "bucket_created": True},
            )

    async def close(self) -> None:
        """Close S3 client and release resources."""
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._client = None

    async def list_objects(self, prefix: str) -> list[str]:
        """List object keys with given prefix."""
        keys = []
        bucket = self._config.s3.bucket
        paginator = self._client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    async def list_objects_with_metadata(self, prefix: str) -> list[dict]:
        """List objects with Key and LastModified for sorting by recency."""
        objects = []
        bucket = self._config.s3.bucket
        paginator = self._client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                objects.append({
                    "Key": obj["Key"],
                    "LastModified": obj["LastModified"],
                })
        return objects

    async def delete_object(self, key: str) -> bool:
        try:
            await self._client.delete_object(Bucket=self._config.s3.bucket, Key=key)
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

        for i in range(0, len(keys), batch_size):
            batch = keys[i : i + batch_size]
            try:
                response = await self._client.delete_objects(
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
            await self._client.head_object(Bucket=self._config.s3.bucket, Key=key)
            return True
        except Exception:
            return False


