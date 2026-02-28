"""S3 client for Agent."""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime

from aiobotocore.session import AioSession, get_session
from pydantic import BaseModel
from types_aiobotocore_s3 import S3Client

from codehub_agent.config import AgentConfig
from codehub_agent.logging_schema import LogEvent
from codehub_agent.metrics import AGENT_S3_BYTES, AGENT_S3_DURATION, AGENT_S3_ERRORS

logger = logging.getLogger(__name__)

@asynccontextmanager
async def _s3_timer(operation: str) -> AsyncIterator[None]:
    """Time S3 operations and classify errors."""
    start = time.monotonic()
    try:
        yield
    except TimeoutError:
        AGENT_S3_ERRORS.labels(operation=operation, error_type="timeout").inc()
        raise
    except ConnectionError:
        AGENT_S3_ERRORS.labels(operation=operation, error_type="connection").inc()
        raise
    except Exception as exc:
        error_type = "not_found" if "NoSuchKey" in str(type(exc).__name__) or "404" in str(exc) else "connection"
        AGENT_S3_ERRORS.labels(operation=operation, error_type=error_type).inc()
        raise
    finally:
        AGENT_S3_DURATION.labels(operation=operation).observe(time.monotonic() - start)


class S3ObjectInfo(BaseModel):
    """S3 object metadata."""

    Key: str
    LastModified: datetime

    model_config = {"frozen": True}


class S3Operations:
    """S3 operations with singleton client for connection reuse."""

    def __init__(self, config: AgentConfig, session: AioSession | None = None) -> None:
        self._config = config
        self._session = session or get_session()
        self._exit_stack: AsyncExitStack | None = None
        self._client: S3Client | None = None

    async def init(self) -> None:
        """Initialize S3 client."""
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

    async def ensure_bucket(self) -> None:
        """Ensure bucket exists (idempotent)."""
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
        async with _s3_timer("list"):
            keys: list[str] = []
            paginator = self._client.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._config.s3.bucket, Prefix=prefix):
                keys.extend(obj["Key"] for obj in page.get("Contents", []))
            return keys

    async def list_objects_with_metadata(self, prefix: str) -> list[S3ObjectInfo]:
        """List objects with Key and LastModified for sorting by recency."""
        async with _s3_timer("list"):
            objects: list[S3ObjectInfo] = []
            paginator = self._client.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._config.s3.bucket, Prefix=prefix):
                objects.extend(
                    S3ObjectInfo(Key=obj["Key"], LastModified=obj["LastModified"])
                    for obj in page.get("Contents", [])
                )
            return objects

    async def delete_object(self, key: str) -> bool:
        """Delete a single object from S3."""
        start = time.monotonic()
        try:
            await self._client.delete_object(Bucket=self._config.s3.bucket, Key=key)
            logger.debug(
                "S3 object deleted",
                extra={"event": LogEvent.S3_OBJECT_DELETED, "key": key},
            )
            return True
        except Exception as e:
            AGENT_S3_ERRORS.labels(operation="delete", error_type="connection").inc()
            logger.warning(
                "Failed to delete S3 object",
                extra={"event": LogEvent.S3_DELETE_FAILED, "key": key, "error": str(e)},
            )
            return False
        finally:
            AGENT_S3_DURATION.labels(operation="delete").observe(time.monotonic() - start)

    async def delete_objects(self, keys: list[str]) -> list[str]:
        """Delete multiple objects in batch. Returns list of successfully deleted keys."""
        if not keys:
            return []

        start = time.monotonic()
        deleted_keys: list[str] = []
        bucket = self._config.s3.bucket
        # S3 delete_objects supports up to 1000 keys per request
        batch_size = 1000

        try:
            for i in range(0, len(keys), batch_size):
                batch = keys[i : i + batch_size]
                try:
                    response = await self._client.delete_objects(
                        Bucket=bucket,
                        Delete={"Objects": [{"Key": key} for key in batch]},
                    )
                    deleted_keys.extend(d["Key"] for d in response.get("Deleted", []))
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
                    AGENT_S3_ERRORS.labels(operation="delete_batch", error_type="connection").inc()
                    logger.warning(
                        "Batch delete failed",
                        extra={
                            "event": LogEvent.S3_DELETE_FAILED,
                            "batch_size": len(batch),
                            "error": str(e),
                        },
                    )
        finally:
            AGENT_S3_DURATION.labels(operation="delete_batch").observe(time.monotonic() - start)

        return deleted_keys

    async def object_exists(self, key: str) -> bool:
        """Check if an object exists in S3."""
        start = time.monotonic()
        try:
            await self._client.head_object(Bucket=self._config.s3.bucket, Key=key)
            return True
        except Exception:
            # Expected: not_found is normal for existence checks, not an error
            return False
        finally:
            AGENT_S3_DURATION.labels(operation="exists").observe(time.monotonic() - start)

    async def get_object(self, key: str) -> bytes | None:
        """Get object content as bytes. Returns None if object doesn't exist."""
        start = time.monotonic()
        try:
            response = await self._client.get_object(
                Bucket=self._config.s3.bucket, Key=key
            )
            async with response["Body"] as stream:
                data = await stream.read()
            AGENT_S3_BYTES.labels(direction="download").inc(len(data))
            return data
        except Exception:
            # Expected: not_found is normal for get attempts, not an error
            return None
        finally:
            AGENT_S3_DURATION.labels(operation="get_object").observe(time.monotonic() - start)


