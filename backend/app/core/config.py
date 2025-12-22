"""Configuration module for code-hub.

Supports env-only configuration with clear validation errors.
All environment variables use CODEHUB_ prefix with double underscore for nested fields.

Examples:
    CODEHUB_SERVER__BIND=:8080
    CODEHUB_SERVER__PUBLIC_BASE_URL=http://localhost:8080
    CODEHUB_HOME_STORE__CONTROL_PLANE_BASE_DIR=/var/lib/codehub/homes
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseSettings):
    """Server configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_SERVER__",
        extra="ignore",
    )

    bind: str = Field(
        default=":8080",
        description="Server bind address (host:port or :port)",
    )
    public_base_url: str = Field(
        default="http://localhost:8080",
        description="Public URL for generating workspace URLs",
    )

    @field_validator("bind")
    @classmethod
    def validate_bind(cls, v: str) -> str:
        """Validate bind address format."""
        if not v:
            raise ValueError("bind address cannot be empty")
        if ":" not in v:
            raise ValueError(
                f"Invalid bind address '{v}': must be in format 'host:port' or ':port'"
            )
        parts = v.rsplit(":", 1)
        try:
            port = int(parts[1])
            if not 1 <= port <= 65535:
                raise ValueError(f"Invalid port {port}: must be between 1 and 65535")
        except ValueError as e:
            if "invalid literal" in str(e):
                raise ValueError(f"Invalid port '{parts[1]}': must be a number") from e
            raise
        return v

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, v: str) -> str:
        """Validate and normalize public_base_url."""
        if not v:
            raise ValueError("public_base_url cannot be empty")
        if not v.startswith(("http://", "https://")):
            raise ValueError(
                f"Invalid public_base_url '{v}': must start with http:// or https://"
            )
        return v.rstrip("/")


class SessionConfig(BaseSettings):
    """Session configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_AUTH__SESSION__",
        extra="ignore",
    )

    cookie_name: str = Field(
        default="session",
        description="Session cookie name",
    )
    ttl: str = Field(
        default="24h",
        description="Session TTL (e.g., '24h', '7d', '30m')",
    )

    @field_validator("ttl")
    @classmethod
    def validate_ttl(cls, v: str) -> str:
        """Validate TTL format."""
        if not v:
            raise ValueError("session TTL cannot be empty")

        import re

        if not re.match(r"^\d+[smhd]$", v):
            raise ValueError(
                f"Invalid TTL format '{v}': must be a number followed by "
                "s (seconds), m (minutes), h (hours), or d (days). "
                "Examples: '30m', '24h', '7d'"
            )
        return v

    def ttl_seconds(self) -> int:
        """Convert TTL to seconds."""
        import re

        match = re.match(r"^(\d+)([smhd])$", self.ttl)
        if not match:
            raise ValueError(f"Invalid TTL format: {self.ttl}")

        value, unit = int(match.group(1)), match.group(2)
        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        return value * multipliers[unit]


class AuthConfig(BaseSettings):
    """Authentication configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_AUTH__",
        extra="ignore",
    )

    mode: Literal["local"] = Field(
        default="local",
        description="Authentication mode (only 'local' supported in MVP)",
    )
    initial_admin_password: str = Field(
        default="admin",
        description="Initial admin password (default: admin)",
    )
    session: SessionConfig = Field(default_factory=SessionConfig)


class HealthcheckConfig(BaseSettings):
    """Workspace healthcheck configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_WORKSPACE__HEALTHCHECK__",
        extra="ignore",
    )

    type: Literal["http", "tcp"] = Field(
        default="http",
        description="Healthcheck type",
    )
    path: str = Field(
        default="/healthz",
        description="Healthcheck path (for http type)",
    )
    interval: str = Field(
        default="2s",
        description="Polling interval (e.g., '2s', '500ms')",
    )
    timeout: str = Field(
        default="60s",
        description="Maximum wait time before marking as ERROR",
    )

    @field_validator("interval", "timeout")
    @classmethod
    def validate_duration(cls, v: str, info: ValidationInfo) -> str:
        """Validate duration format."""
        if not v:
            raise ValueError(f"{info.field_name} cannot be empty")

        import re

        if not re.match(r"^\d+(ms|s|m)$", v):
            raise ValueError(
                f"Invalid {info.field_name} format '{v}': must be a number followed by "
                "ms (milliseconds), s (seconds), or m (minutes). "
                "Examples: '500ms', '2s', '1m'"
            )
        return v

    def interval_seconds(self) -> float:
        """Convert interval to seconds."""
        return self._parse_duration(self.interval)

    def timeout_seconds(self) -> float:
        """Convert timeout to seconds."""
        return self._parse_duration(self.timeout)

    @staticmethod
    def _parse_duration(duration: str) -> float:
        """Parse duration string to seconds."""
        import re

        match = re.match(r"^(\d+)(ms|s|m)$", duration)
        if not match:
            raise ValueError(f"Invalid duration format: {duration}")

        value, unit = int(match.group(1)), match.group(2)
        multipliers = {"ms": 0.001, "s": 1, "m": 60}
        return value * multipliers[unit]


class WorkspaceConfig(BaseSettings):
    """Workspace configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_WORKSPACE__",
        extra="ignore",
    )

    default_image: str = Field(
        default="cagojeiger/code-server:4.107.0",
        description="Default container image for workspaces",
    )
    container_prefix: str = Field(
        default="codehub-ws-",
        description="Prefix for Docker container names",
    )
    network_name: str = Field(
        default="codehub-net",
        description="Docker network name for workspaces",
    )
    healthcheck: HealthcheckConfig = Field(default_factory=HealthcheckConfig)

    @field_validator("default_image")
    @classmethod
    def validate_default_image(cls, v: str) -> str:
        """Validate default image format."""
        if not v:
            raise ValueError("default_image cannot be empty")
        if " " in v:
            raise ValueError(
                f"Invalid default_image '{v}': image name cannot contain spaces"
            )
        return v


class DatabaseConfig(BaseSettings):
    """Database configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_DATABASE__",
        extra="ignore",
    )

    url: str = Field(
        default="postgresql+asyncpg://codehub:codehub@localhost:5432/codehub",
        description="Database URL (PostgreSQL for production, SQLite for tests)",
    )
    echo: bool = Field(
        default=False,
        description="Echo SQL statements for debugging",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate database URL."""
        if not v:
            raise ValueError("database URL cannot be empty")
        return v


class HomeStoreConfig(BaseSettings):
    """Home store configuration."""

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_HOME_STORE__",
        extra="ignore",
    )

    backend: Literal["local-dir"] = Field(
        default="local-dir",
        description="Storage backend (only 'local-dir' supported in MVP)",
    )
    control_plane_base_dir: str = Field(
        default="/var/lib/codehub/homes",
        description="Base directory from Control Plane container perspective",
    )
    workspace_base_dir: str | None = Field(
        default=None,
        description="Host path for Docker bind mount",
    )

    @field_validator("control_plane_base_dir")
    @classmethod
    def validate_control_plane_base_dir(cls, v: str) -> str:
        """Validate control_plane_base_dir is an absolute path."""
        if not v:
            raise ValueError("control_plane_base_dir cannot be empty")
        if not v.startswith("/"):
            raise ValueError(
                f"Invalid control_plane_base_dir '{v}': "
                "must be an absolute path starting with '/'"
            )
        return v.rstrip("/")

    @model_validator(mode="after")
    def validate_workspace_base_dir_for_local_dir(self) -> Self:
        """Validate workspace_base_dir is set when using local-dir backend."""
        if self.backend == "local-dir" and not self.workspace_base_dir:
            raise ValueError(
                "home_store.workspace_base_dir is required "
                "when using 'local-dir' backend. "
                "Set CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR env var."
            )
        return self


class Settings(BaseSettings):
    """Main application settings.

    All settings can be configured via environment variables with CODEHUB_ prefix.
    Nested settings use double underscore as separator.

    Examples:
        CODEHUB_SERVER__BIND=:8080
        CODEHUB_SERVER__PUBLIC_BASE_URL=http://localhost:8080
        CODEHUB_HOME_STORE__CONTROL_PLANE_BASE_DIR=/var/lib/codehub/homes
        CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR=/host/path/to/homes
    """

    model_config = SettingsConfigDict(
        env_prefix="CODEHUB_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    home_store: HomeStoreConfig = Field(default_factory=HomeStoreConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings.

    Returns:
        Settings instance (cached after first call)

    Raises:
        ValidationError: If configuration is invalid with detailed error message
    """
    return Settings()


def validate_settings() -> Settings:
    """Validate and return settings (not cached).

    Useful for testing configuration without caching.

    Returns:
        Settings instance

    Raises:
        ValidationError: If configuration is invalid with detailed error message
    """
    return Settings()
