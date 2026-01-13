"""Agent configuration using pydantic-settings.

Configuration hierarchy:
- DockerConfig: Container runtime settings
- S3Config: Object storage settings
- LoggingConfig: Logging behavior
- AgentConfig: Main config aggregating all sub-configs

Environment variable prefix: AGENT_
Example: AGENT_DOCKER_NETWORK=my-network
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DockerConfig(BaseSettings):
    """Docker runtime configuration.

    Scale guide (N = concurrent workspaces):
      10  -> api_timeout=30s, job_timeout=300s
      50  -> api_timeout=30s, job_timeout=600s
      100 -> api_timeout=60s, job_timeout=900s
    """

    model_config = SettingsConfigDict(env_prefix="AGENT_DOCKER_")

    # Connection
    host: str = Field(
        default="unix:///var/run/docker.sock",
        description="Docker daemon socket or TCP address",
    )
    network: str = Field(default="codehub-net", description="Docker network name")

    # Container user
    coder_uid: int = Field(default=1000, description="UID for workspace user")
    coder_gid: int = Field(default=1000, description="GID for workspace user")

    # Networking
    container_port: int = Field(default=8080, description="Exposed port in workspace container")

    # DNS settings for VPN/network stability
    # Docker Desktop 4.48.0+ has DNS routing issues with VPN
    # See: https://github.com/docker/for-mac/issues/4751
    dns_servers: list[str] = Field(default=["8.8.8.8", "1.1.1.1"])
    dns_options: list[str] = Field(default=["use-vc"])  # Force TCP for DNS

    # Timeouts
    api_timeout: float = Field(default=30.0, description="Docker API call timeout (seconds)")
    image_pull_timeout: float = Field(default=600.0, description="Image pull timeout (seconds)")
    container_wait_timeout: int = Field(default=600, description="Container wait timeout (seconds)")
    job_timeout: int = Field(default=600, description="Archive/restore job timeout (seconds)")
    timeout_buffer: int = Field(
        default=10,
        description="Additional buffer for Docker wait API (seconds)",
    )


class S3Config(BaseSettings):
    """S3/MinIO storage configuration.

    Environment variables use S3_ prefix for compatibility with AWS tools.
    Example: AGENT_S3_ENDPOINT=http://minio:9000
    """

    model_config = SettingsConfigDict(env_prefix="AGENT_S3_")

    endpoint: str = Field(
        default="http://minio:9000",
        description="S3 endpoint URL",
    )
    internal_endpoint: str = Field(
        default="http://minio:9000",
        description="S3 endpoint for job containers (internal network)",
    )
    bucket: str = Field(default="codehub-archives", description="S3 bucket name")
    region: str = Field(default="us-east-1", description="S3 region")

    # Credentials - empty defaults force explicit configuration
    access_key: str = Field(default="", description="S3 access key (required)")
    secret_key: str = Field(default="", description="S3 secret key (required)")


class LoggingConfig(BaseSettings):
    """Logging configuration.

    Supports both text and JSON formats for different environments:
    - text: Human-readable for local development
    - json: Structured logging for production (log aggregation)
    """

    model_config = SettingsConfigDict(env_prefix="AGENT_LOGGING_")

    level: str = Field(default="INFO", description="Log level (DEBUG, INFO, WARNING, ERROR)")
    format: str = Field(default="text", description="Log format (text, json)")
    service_name: str = Field(default="codehub-agent", description="Service identifier in logs")
    slow_threshold_ms: float = Field(
        default=1000.0,
        description="Threshold for slow operation warnings (milliseconds)",
    )


class RuntimeConfig(BaseSettings):
    """Runtime resource configuration.

    These settings define naming conventions and default images.
    """

    model_config = SettingsConfigDict(env_prefix="AGENT_RUNTIME_")

    # Resource naming
    resource_prefix: str = Field(
        default="codehub-ws-",
        description="Prefix for Docker resources (containers, volumes)",
    )

    # Archive naming
    archive_suffix: str = Field(
        default="home.tar.zst",
        description="Archive file suffix",
    )

    # Default images
    default_image: str = Field(
        default="cagojeiger/code-server:latest",
        description="Default workspace image",
    )
    storage_job_image: str = Field(
        default="codehub/storage-job:latest",
        description="Image for archive/restore jobs",
    )


class ServerConfig(BaseSettings):
    """HTTP server configuration."""

    model_config = SettingsConfigDict(env_prefix="AGENT_SERVER_")

    host: str = Field(default="0.0.0.0", description="Server bind address")
    port: int = Field(default=8081, description="Server port")
    api_key: str = Field(default="", description="API key for authentication")
    cors_origins: list[str] = Field(
        default=["*"],
        description="Allowed CORS origins",
    )


class AgentConfig(BaseSettings):
    """Main agent configuration aggregating all sub-configs.

    Environment variable prefix: AGENT_
    Sub-configs use their own prefixes (AGENT_DOCKER_, AGENT_S3_, etc.)
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_nested_delimiter="__",
    )

    # Cluster identification (for multi-cluster deployments)
    cluster_id: str = Field(
        default="default",
        description="Cluster ID (currently unused, reserved for multi-cluster)",
    )

    # Sub-configurations
    docker: DockerConfig = Field(default_factory=DockerConfig)
    s3: S3Config = Field(default_factory=S3Config)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)


@lru_cache
def get_agent_config() -> AgentConfig:
    """Get cached agent configuration singleton."""
    return AgentConfig()
