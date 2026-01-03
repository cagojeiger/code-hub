"""S3/MinIO storage client management."""

from types import TracebackType

import aioboto3
from botocore.exceptions import ClientError
from types_aiobotocore_s3 import S3Client

from codehub.app.config import get_settings

_session: aioboto3.Session | None = None


async def init_storage() -> None:
    global _session

    _session = aioboto3.Session()

    settings = get_settings()
    async with _session.client(
        "s3",
        endpoint_url=settings.storage.endpoint_url,
        aws_access_key_id=settings.storage.access_key,
        aws_secret_access_key=settings.storage.secret_key,
    ) as s3:
        # Ensure bucket exists
        try:
            await s3.head_bucket(Bucket=settings.storage.bucket_name)
        except ClientError:
            await s3.create_bucket(Bucket=settings.storage.bucket_name)


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
