"""Application configuration using pydantic-settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DATABASE_")

    url: str = Field(default="postgresql+asyncpg://codehub:codehub@postgres:5432/codehub")
    echo: bool = False
    pool_size: int = 10
    max_overflow: int = 20


class RedisConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_")

    url: str = Field(default="redis://redis:6379")


class StorageConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="S3_")

    endpoint_url: str = Field(default="http://minio:9000", validation_alias="S3_ENDPOINT")
    # Internal endpoint for containers (e.g., storage-job) running on Docker network.
    # Defaults to endpoint_url. Set S3_ENDPOINT_INTERNAL for local tests.
    internal_endpoint_url: str = Field(
        default="http://minio:9000", validation_alias="S3_ENDPOINT_INTERNAL"
    )
    access_key: str = Field(default="codehub", validation_alias="S3_ACCESS_KEY")
    secret_key: str = Field(default="codehub123", validation_alias="S3_SECRET_KEY")
    bucket_name: str = Field(default="codehub-archives", validation_alias="S3_BUCKET")


class DockerConfig(BaseSettings):
    """Docker-related configuration for workspace containers."""

    model_config = SettingsConfigDict(env_prefix="DOCKER_")

    resource_prefix: str = Field(default="codehub-ws-")
    network_name: str = Field(default="codehub-net")
    container_port: int = Field(default=8080)
    coder_uid: int = Field(default=1000)
    coder_gid: int = Field(default=1000)
    default_image: str = Field(default="cagojeiger/code-server:4.107.0")
    storage_job_image: str = Field(default="codehub/storage-job:latest")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_",
        env_nested_delimiter="__",
    )

    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    docker: DockerConfig = Field(default_factory=DockerConfig)


@lru_cache
def get_settings() -> Settings:
    return Settings()
