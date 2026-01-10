"""S3/MinIO storage client management."""

import logging
from types import TracebackType

import aioboto3
from botocore.exceptions import ClientError
from types_aiobotocore_s3 import S3Client

from codehub.app.config import get_settings
from codehub.core.logging_schema import LogEvent

logger = logging.getLogger(__name__)

_session: aioboto3.Session | None = None


async def init_storage() -> None:
    global _session

    _session = aioboto3.Session()

    settings = get_settings()
    try:
        async with _session.client(
            "s3",
            endpoint_url=settings.storage.endpoint_url,
            aws_access_key_id=settings.storage.access_key,
            aws_secret_access_key=settings.storage.secret_key,
        ) as s3:
            # Ensure bucket exists
            try:
                await s3.head_bucket(Bucket=settings.storage.bucket_name)
                logger.info(
                    "S3 storage connected",
                    extra={
                        "event": LogEvent.S3_CONNECTED,
                        "bucket": settings.storage.bucket_name,
                        "endpoint": settings.storage.endpoint_url,
                    },
                )
            except ClientError:
                await s3.create_bucket(Bucket=settings.storage.bucket_name)
                logger.info(
                    "S3 bucket created",
                    extra={
                        "event": LogEvent.S3_BUCKET_CREATED,
                        "bucket": settings.storage.bucket_name,
                        "endpoint": settings.storage.endpoint_url,
                    },
                )
    except Exception as e:
        logger.error(
            "S3 connection failed",
            extra={
                "event": LogEvent.S3_ERROR,
                "error_type": type(e).__name__,
                "error": str(e),
                "bucket": settings.storage.bucket_name,
                "endpoint": settings.storage.endpoint_url,
            },
        )
        raise


async def close_storage() -> None:
    global _session
    _session = None


class S3ClientContext:
    """Context manager for S3 client."""

    def __init__(self) -> None:
        self._client: S3Client | None = None
        self._context: object | None = None

    async def __aenter__(self) -> S3Client:
        if _session is None:
            raise RuntimeError("Storage not initialized")

        settings = get_settings()
        self._context = _session.client(
            "s3",
            endpoint_url=settings.storage.endpoint_url,
            aws_access_key_id=settings.storage.access_key,
            aws_secret_access_key=settings.storage.secret_key,
        )
        self._client = await self._context.__aenter__()
        return self._client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._context:
            await self._context.__aexit__(exc_type, exc_val, exc_tb)


def get_s3_client() -> S3ClientContext:
    return S3ClientContext()
