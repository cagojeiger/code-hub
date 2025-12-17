"""Application configuration using Pydantic Settings.

Configuration is loaded from environment variables only (no config files).
All environment variables use the CODEHUB_ prefix.

Example environment variables:
    CODEHUB_SERVER__BIND=:8080
    CODEHUB_SERVER__PUBLIC_BASE_URL=http://localhost:8080
    CODEHUB_HOME_STORE__BASE_DIR=/data/home
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    """Server configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_SERVER__",
        extra="ignore",
    )

    bind: str = Field(
        default=":8080",
        description="Server bind address (e.g., ':8080' or '0.0.0.0:8080')",
    )
    public_base_url: str = Field(
        default="http://localhost:8080",
        description="Public base URL for generating workspace URLs",
    )

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, v: str) -> str:
        """Ensure public_base_url doesn't have trailing slash."""
        return v.rstrip("/")


class SessionSettings(BaseSettings):
    """Session configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_AUTH__SESSION_",
        extra="ignore",
    )

    cookie_name: str = Field(
        default="session",
        description="Session cookie name",
    )
    ttl_seconds: int = Field(
        default=86400,  # 24 hours
        gt=0,
        description="Session TTL in seconds (default: 24h = 86400s)",
    )


class AuthSettings(BaseSettings):
    """Authentication configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_AUTH__",
        extra="ignore",
    )

    mode: Literal["local"] = Field(
        default="local",
        description="Authentication mode (currently only 'local' is supported)",
    )
    session: SessionSettings = Field(default_factory=SessionSettings)


class HealthcheckSettings(BaseSettings):
    """Workspace healthcheck configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_WORKSPACE__HEALTHCHECK_",
        extra="ignore",
    )

    type: Literal["http", "tcp"] = Field(
        default="http",
        description="Healthcheck type: 'http' or 'tcp'",
    )
    path: str = Field(
        default="/healthz",
        description="HTTP healthcheck path (used when type='http')",
    )
    interval_seconds: float = Field(
        default=2.0,
        gt=0,
        description="Polling interval in seconds",
    )
    timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Maximum wait time in seconds (ERROR if exceeded)",
    )

    @model_validator(mode="after")
    def validate_timeout_greater_than_interval(self) -> "HealthcheckSettings":
        """Ensure timeout is greater than interval."""
        if self.timeout_seconds <= self.interval_seconds:
            raise ValueError(
                f"timeout_seconds ({self.timeout_seconds}) must be greater than "
                f"interval_seconds ({self.interval_seconds})"
            )
        return self


class WorkspaceSettings(BaseSettings):
    """Workspace configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_WORKSPACE__",
        extra="ignore",
    )

    default_image: str = Field(
        default="codercom/code-server:latest",
        description="Default Docker image for workspaces",
    )
    healthcheck: HealthcheckSettings = Field(default_factory=HealthcheckSettings)

    @field_validator("default_image")
    @classmethod
    def validate_default_image(cls, v: str) -> str:
        """Ensure default_image is not empty."""
        if not v or not v.strip():
            raise ValueError("default_image cannot be empty")
        return v.strip()


class HomeStoreSettings(BaseSettings):
    """Home store configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_HOME_STORE__",
        extra="ignore",
    )

    backend: Literal["local-dir"] = Field(
        default="local-dir",
        description="Storage backend (MVP: only 'local-dir' supported)",
    )
    base_dir: str = Field(
        default="/data/home",
        description="Base directory for home storage (container path)",
    )
    host_path: str | None = Field(
        default=None,
        description="Host path for Docker bind mounts (required for local-docker)",
    )

    @field_validator("base_dir")
    @classmethod
    def validate_base_dir(cls, v: str) -> str:
        """Ensure base_dir is an absolute path."""
        if not v.startswith("/"):
            raise ValueError(f"base_dir must be an absolute path, got: {v}")
        return v.rstrip("/")

    @field_validator("host_path")
    @classmethod
    def validate_host_path(cls, v: str | None) -> str | None:
        """Ensure host_path is an absolute path if provided."""
        if v is not None:
            if not v.startswith("/"):
                raise ValueError(f"host_path must be an absolute path, got: {v}")
            return v.rstrip("/")
        return v


class Settings(BaseSettings):
    """Main application settings.

    All settings can be configured via environment variables with CODEHUB_ prefix.
    Nested settings use double underscore (__) as separator.

    Example:
        CODEHUB_SERVER__BIND=:8080
        CODEHUB_HOME_STORE__BASE_DIR=/data/home
    """

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    env: Literal["development", "production", "test"] = Field(
        default="development",
        description="Environment: development, production, or test",
    )

    server: ServerSettings = Field(default_factory=ServerSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)
    home_store: HomeStoreSettings = Field(default_factory=HomeStoreSettings)

    @model_validator(mode="after")
    def validate_production_requirements(self) -> "Settings":
        """Validate production-specific requirements."""
        if self.env == "production":
            if self.home_store.host_path is None:
                raise ValueError(
                    "home_store.host_path is required in production environment. "
                    "Set CODEHUB_HOME_STORE__HOST_PATH environment variable."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings.

    Settings are loaded once and cached for subsequent calls.
    Returns singleton Settings instance.

    Raises:
        pydantic.ValidationError: If environment variables have invalid values.
            The error message will indicate which field failed validation
            and what the expected format is.
    """
    return Settings()
