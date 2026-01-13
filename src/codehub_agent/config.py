"""Agent configuration via environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentConfig(BaseSettings):
    """Agent configuration from environment variables."""

    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env")

    # Cluster identification
    cluster_id: str = Field(default="default", description="Cluster ID for S3 path prefix")

    # Resource naming
    resource_prefix: str = Field(default="codehub-", description="Prefix for Docker resources")

    # Docker settings
    docker_host: str = Field(default="unix:///var/run/docker.sock")
    docker_network: str = Field(default="codehub-net")
    coder_uid: int = Field(default=1000)
    coder_gid: int = Field(default=1000)
    container_port: int = Field(default=8080)
    api_timeout: float = Field(default=30.0)
    job_timeout: int = Field(default=600, description="Job timeout in seconds")
    image_pull_timeout: float = Field(default=600.0)
    container_wait_timeout: int = Field(default=600)

    # S3/MinIO settings
    s3_endpoint: str = Field(default="http://minio:9000")
    s3_internal_endpoint: str = Field(
        default="http://minio:9000",
        description="S3 endpoint for job containers (internal network)",
    )
    s3_bucket: str = Field(default="codehub-archives")
    s3_access_key: str = Field(default="minioadmin")
    s3_secret_key: str = Field(default="minioadmin")

    # Images
    default_image: str = Field(default="cagojeiger/code-server:4.107.1")
    storage_job_image: str = Field(default="codehub/storage-job:latest")

    # API authentication
    api_key: str = Field(default="", description="API key for Control Plane authentication")

    # Server settings
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8081)


@lru_cache
def get_agent_config() -> AgentConfig:
    """Get cached agent configuration."""
    return AgentConfig()
