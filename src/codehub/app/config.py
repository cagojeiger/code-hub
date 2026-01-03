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
    model_config = SettingsConfigDict(env_prefix="MINIO_")

    endpoint_url: str = Field(default="http://minio:9000", alias="MINIO_ENDPOINT")
    access_key: str = Field(default="codehub", alias="MINIO_ACCESS_KEY")
    secret_key: str = Field(default="codehub123", alias="MINIO_SECRET_KEY")
    bucket_name: str = Field(default="codehub-archives", alias="MINIO_BUCKET")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_",
        env_nested_delimiter="__",
    )

    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)


@lru_cache
def get_settings() -> Settings:
    return Settings()
