"""Job runner interface for Storage Job execution.

Spec-v2 Storage Job:
- Input: ARCHIVE_URL, S3 credentials
- Output: exit code (0=success)
- Design: Crash-Only, Stateless, Idempotent
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel


class JobResult(BaseModel):
    """Result of a job execution."""

    exit_code: int
    logs: str

    model_config = {"frozen": True}


class JobRunner(ABC):
    """Interface for Storage Job execution.

    Implementations:
    - DockerJobRunner: Docker containers
    - K8sJobRunner: Kubernetes Jobs (future)
    """

    @abstractmethod
    async def run_archive(
        self,
        archive_url: str,
        volume_name: str,
        s3_endpoint: str,
        s3_access_key: str,
        s3_secret_key: str,
    ) -> JobResult:
        """Run archive job (Volume -> S3).

        Args:
            archive_url: Full S3 URL (s3://bucket/key)
            volume_name: Volume name to archive
            s3_endpoint: S3 endpoint URL
            s3_access_key: S3 access key
            s3_secret_key: S3 secret key

        Returns:
            JobResult with exit code and logs
        """
        ...

    @abstractmethod
    async def run_restore(
        self,
        archive_url: str,
        volume_name: str,
        s3_endpoint: str,
        s3_access_key: str,
        s3_secret_key: str,
    ) -> JobResult:
        """Run restore job (S3 -> Volume).

        Args:
            archive_url: Full S3 URL (s3://bucket/key)
            volume_name: Volume name to restore to
            s3_endpoint: S3 endpoint URL
            s3_access_key: S3 access key
            s3_secret_key: S3 secret key

        Returns:
            JobResult with exit code and logs
        """
        ...
