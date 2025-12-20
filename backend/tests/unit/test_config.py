"""Tests for configuration module.

Exit Criteria:
- env-only로도 부팅 가능 (env-only boot possible)
- 잘못된 값은 명확한 에러 (invalid values produce clear errors)

Testing approach:
- Use direct constructor arguments instead of mocking
- Use pytest fixtures for environment setup
"""

import os

import pytest
from pydantic import ValidationError

from app.core.config import (
    AuthConfig,
    HealthcheckConfig,
    HomeStoreConfig,
    ServerConfig,
    SessionConfig,
    Settings,
    WorkspaceConfig,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Remove CODEHUB_ env vars to ensure clean test environment."""
    for key in list(os.environ.keys()):
        if key.startswith("CODEHUB_"):
            monkeypatch.delenv(key, raising=False)


class TestServerConfig:
    """Tests for ServerConfig."""

    def test_default_values(self):
        """Server config should have sensible defaults."""
        config = ServerConfig()
        assert config.bind == ":8080"
        assert config.public_base_url == "http://localhost:8080"

    def test_custom_values(self):
        """Server config should accept custom values."""
        config = ServerConfig(bind=":9000", public_base_url="https://example.com")
        assert config.bind == ":9000"
        assert config.public_base_url == "https://example.com"

    def test_bind_validation_valid(self):
        """Valid bind addresses should be accepted."""
        valid_binds = [":8080", "0.0.0.0:8080", "127.0.0.1:3000", "localhost:80"]
        for bind in valid_binds:
            config = ServerConfig(bind=bind)
            assert config.bind == bind

    def test_bind_validation_empty(self):
        """Empty bind address should produce clear error."""
        with pytest.raises(ValidationError) as exc_info:
            ServerConfig(bind="")
        assert "bind address cannot be empty" in str(exc_info.value)

    def test_bind_validation_no_port(self):
        """Bind address without port should produce clear error."""
        with pytest.raises(ValidationError) as exc_info:
            ServerConfig(bind="localhost")
        assert "must be in format 'host:port'" in str(exc_info.value)

    def test_bind_validation_invalid_port(self):
        """Invalid port number should produce clear error."""
        with pytest.raises(ValidationError) as exc_info:
            ServerConfig(bind=":abc")
        assert "must be a number" in str(exc_info.value)

    def test_bind_validation_port_out_of_range(self):
        """Port out of range should produce clear error."""
        with pytest.raises(ValidationError) as exc_info:
            ServerConfig(bind=":70000")
        assert "must be between 1 and 65535" in str(exc_info.value)

    def test_public_base_url_validation_empty(self):
        """Empty public_base_url should produce clear error."""
        with pytest.raises(ValidationError) as exc_info:
            ServerConfig(public_base_url="")
        assert "public_base_url cannot be empty" in str(exc_info.value)

    def test_public_base_url_validation_no_protocol(self):
        """URL without protocol should produce clear error."""
        with pytest.raises(ValidationError) as exc_info:
            ServerConfig(public_base_url="localhost:8080")
        assert "must start with http:// or https://" in str(exc_info.value)

    def test_public_base_url_trailing_slash_removed(self):
        """Trailing slash should be removed from public_base_url."""
        config = ServerConfig(public_base_url="http://localhost:8080/")
        assert config.public_base_url == "http://localhost:8080"


class TestSessionConfig:
    """Tests for SessionConfig."""

    def test_default_values(self):
        """Session config should have sensible defaults."""
        config = SessionConfig()
        assert config.cookie_name == "session"
        assert config.ttl == "24h"

    def test_custom_values(self):
        """Session config should accept custom values."""
        config = SessionConfig(cookie_name="my_session", ttl="7d")
        assert config.cookie_name == "my_session"
        assert config.ttl == "7d"

    def test_ttl_validation_valid(self):
        """Valid TTL formats should be accepted."""
        valid_ttls = ["30s", "30m", "24h", "7d"]
        for ttl in valid_ttls:
            config = SessionConfig(ttl=ttl)
            assert config.ttl == ttl

    def test_ttl_validation_invalid(self):
        """Invalid TTL format should produce clear error."""
        with pytest.raises(ValidationError) as exc_info:
            SessionConfig(ttl="24hours")
        assert "must be a number followed by s" in str(exc_info.value)

    def test_ttl_seconds_conversion(self):
        """TTL should be correctly converted to seconds."""
        test_cases = [
            ("30s", 30),
            ("30m", 1800),
            ("24h", 86400),
            ("7d", 604800),
        ]
        for ttl, expected_seconds in test_cases:
            config = SessionConfig(ttl=ttl)
            assert config.ttl_seconds() == expected_seconds


class TestHealthcheckConfig:
    """Tests for HealthcheckConfig."""

    def test_default_values(self):
        """Healthcheck config should have sensible defaults."""
        config = HealthcheckConfig()
        assert config.type == "http"
        assert config.path == "/healthz"
        assert config.interval == "2s"
        assert config.timeout == "60s"

    def test_custom_values(self):
        """Healthcheck config should accept custom values."""
        config = HealthcheckConfig(
            type="tcp", path="/health", interval="5s", timeout="2m"
        )
        assert config.type == "tcp"
        assert config.path == "/health"
        assert config.interval == "5s"
        assert config.timeout == "2m"

    def test_duration_validation_valid(self):
        """Valid duration formats should be accepted."""
        valid_durations = ["500ms", "2s", "1m"]
        for duration in valid_durations:
            config = HealthcheckConfig(interval=duration)
            assert config.interval == duration

    def test_duration_validation_invalid(self):
        """Invalid duration format should produce clear error."""
        with pytest.raises(ValidationError) as exc_info:
            HealthcheckConfig(interval="2sec")
        assert "must be a number followed by ms" in str(exc_info.value)

    def test_duration_seconds_conversion(self):
        """Duration should be correctly converted to seconds."""
        config = HealthcheckConfig(interval="500ms", timeout="2m")
        assert config.interval_seconds() == 0.5
        assert config.timeout_seconds() == 120


class TestHomeStoreConfig:
    """Tests for HomeStoreConfig."""

    def test_default_values(self):
        """HomeStoreConfig should have sensible defaults except workspace_base_dir."""
        config = HomeStoreConfig(workspace_base_dir="/host/var/lib/codehub/homes")
        assert config.backend == "local-dir"
        assert config.control_plane_base_dir == "/var/lib/codehub/homes"
        assert config.workspace_base_dir == "/host/var/lib/codehub/homes"

    def test_custom_values(self):
        """HomeStoreConfig should accept custom values."""
        config = HomeStoreConfig(
            control_plane_base_dir="/custom/path/homes",
            workspace_base_dir="/host/custom/path/homes",
        )
        assert config.control_plane_base_dir == "/custom/path/homes"
        assert config.workspace_base_dir == "/host/custom/path/homes"

    def test_workspace_base_dir_required_for_local_dir(self):
        """HomeStoreConfig requires workspace_base_dir for local-dir backend."""
        with pytest.raises(ValidationError) as exc_info:
            HomeStoreConfig()
        assert "workspace_base_dir is required" in str(exc_info.value)

    def test_control_plane_base_dir_validation_not_absolute(self):
        """Relative control_plane_base_dir should produce clear error."""
        with pytest.raises(ValidationError) as exc_info:
            HomeStoreConfig(
                control_plane_base_dir="var/lib/codehub/homes",
                workspace_base_dir="/host/var/lib/codehub/homes",
            )
        assert "must be an absolute path" in str(exc_info.value)

    def test_control_plane_base_dir_trailing_slash_removed(self):
        """Trailing slash should be removed from control_plane_base_dir."""
        config = HomeStoreConfig(
            control_plane_base_dir="/var/lib/codehub/homes/",
            workspace_base_dir="/host/var/lib/codehub/homes",
        )
        assert config.control_plane_base_dir == "/var/lib/codehub/homes"


class TestWorkspaceConfig:
    """Tests for WorkspaceConfig."""

    def test_default_values(self):
        """Workspace config should have sensible defaults."""
        config = WorkspaceConfig()
        assert config.default_image == "cagojeiger/code-server:4.107.0"
        assert config.healthcheck.type == "http"
        assert config.healthcheck.interval == "2s"

    def test_custom_values(self):
        """Workspace config should accept custom values."""
        config = WorkspaceConfig(
            default_image="custom/image:v1",
            healthcheck=HealthcheckConfig(interval="5s"),
        )
        assert config.default_image == "custom/image:v1"
        assert config.healthcheck.interval == "5s"

    def test_default_image_validation_empty(self):
        """Empty default_image should produce clear error."""
        with pytest.raises(ValidationError) as exc_info:
            WorkspaceConfig(default_image="")
        assert "default_image cannot be empty" in str(exc_info.value)

    def test_default_image_validation_spaces(self):
        """Image name with spaces should produce clear error."""
        with pytest.raises(ValidationError) as exc_info:
            WorkspaceConfig(default_image="invalid image")
        assert "cannot contain spaces" in str(exc_info.value)


class TestAuthConfig:
    """Tests for AuthConfig."""

    def test_default_values(self):
        """Auth config should have sensible defaults."""
        config = AuthConfig()
        assert config.mode == "local"
        assert config.session.cookie_name == "session"
        assert config.session.ttl == "24h"

    def test_custom_session(self):
        """Auth config should accept custom session config."""
        config = AuthConfig(session=SessionConfig(cookie_name="my_session", ttl="7d"))
        assert config.session.cookie_name == "my_session"
        assert config.session.ttl == "7d"


class TestSettings:
    """Tests for main Settings class."""

    def test_default_values_with_required(self):
        """Settings should have sensible defaults when required values provided."""
        settings = Settings(
            home_store=HomeStoreConfig(workspace_base_dir="/host/var/lib/codehub/homes")
        )
        assert settings.server.bind == ":8080"
        assert settings.server.public_base_url == "http://localhost:8080"
        assert settings.auth.mode == "local"
        assert settings.auth.session.cookie_name == "session"
        assert settings.workspace.default_image == "cagojeiger/code-server:4.107.0"
        assert settings.home_store.backend == "local-dir"
        assert settings.home_store.control_plane_base_dir == "/var/lib/codehub/homes"

    def test_custom_values(self):
        """Settings should accept custom values for all configs."""
        settings = Settings(
            server=ServerConfig(bind=":9000", public_base_url="https://example.com"),
            auth=AuthConfig(session=SessionConfig(cookie_name="my_session", ttl="7d")),
            workspace=WorkspaceConfig(
                default_image="custom/image:v1",
                healthcheck=HealthcheckConfig(interval="5s", timeout="2m"),
            ),
            home_store=HomeStoreConfig(
                control_plane_base_dir="/custom/path/homes",
                workspace_base_dir="/host/custom/path/homes",
            ),
        )
        assert settings.server.bind == ":9000"
        assert settings.server.public_base_url == "https://example.com"
        assert settings.auth.session.cookie_name == "my_session"
        assert settings.auth.session.ttl == "7d"
        assert settings.workspace.default_image == "custom/image:v1"
        assert settings.workspace.healthcheck.interval == "5s"
        assert settings.workspace.healthcheck.timeout == "2m"
        assert settings.home_store.control_plane_base_dir == "/custom/path/homes"
        assert settings.home_store.workspace_base_dir == "/host/custom/path/homes"

    def test_validation_error_is_clear(self):
        """Invalid configuration should produce clear error messages.

        Exit Criteria: 잘못된 값은 명확한 에러
        """
        with pytest.raises(ValidationError) as exc_info:
            Settings(
                server=ServerConfig(bind="invalid"),
                home_store=HomeStoreConfig(
                    workspace_base_dir="/host/var/lib/codehub/homes"
                ),
            )
        error_str = str(exc_info.value)
        assert "bind" in error_str.lower() or "host:port" in error_str


class TestEnvVarIntegration:
    """Tests for environment variable integration.

    Exit Criteria: env-only로도 부팅 가능
    """

    def test_env_only_boot(self, monkeypatch):
        """Settings should boot with only required environment variables."""
        monkeypatch.setenv(
            "CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR", "/host/var/lib/codehub/homes"
        )

        settings = Settings()
        assert settings.server.bind == ":8080"
        assert settings.home_store.workspace_base_dir == "/host/var/lib/codehub/homes"

    def test_env_override_defaults(self, monkeypatch):
        """Environment variables should override defaults."""
        monkeypatch.setenv("CODEHUB_SERVER__BIND", ":9000")
        monkeypatch.setenv(
            "CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR", "/host/var/lib/codehub/homes"
        )

        settings = Settings()
        assert settings.server.bind == ":9000"

    def test_nested_env_vars(self, monkeypatch):
        """Nested environment variables should work correctly."""
        monkeypatch.setenv("CODEHUB_AUTH__SESSION__TTL", "7d")
        monkeypatch.setenv(
            "CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR", "/host/var/lib/codehub/homes"
        )

        settings = Settings()
        assert settings.auth.session.ttl == "7d"
