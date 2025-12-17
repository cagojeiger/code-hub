"""Tests for configuration module.

Exit Criteria:
- env-only로도 부팅 가능 (env-only boot possible)
- 잘못된 값은 명확한 에러 (invalid values produce clear errors)
"""

import os
from unittest.mock import patch

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
    validate_settings,
)


class TestServerConfig:
    """Tests for ServerConfig."""

    def test_default_values(self):
        """Server config should have sensible defaults."""
        config = ServerConfig()
        assert config.bind == ":8080"
        assert config.public_base_url == "http://localhost:8080"

    def test_bind_validation_valid(self):
        """Valid bind addresses should be accepted."""
        valid_binds = [":8080", "0.0.0.0:8080", "127.0.0.1:3000", "localhost:80"]
        for bind in valid_binds:
            with patch.dict(os.environ, {"CODEHUB_SERVER__BIND": bind}):
                config = ServerConfig()
                assert config.bind == bind

    def test_bind_validation_empty(self):
        """Empty bind address should produce clear error."""
        with patch.dict(os.environ, {"CODEHUB_SERVER__BIND": ""}):
            with pytest.raises(ValidationError) as exc_info:
                ServerConfig()
            assert "bind address cannot be empty" in str(exc_info.value)

    def test_bind_validation_no_port(self):
        """Bind address without port should produce clear error."""
        with patch.dict(os.environ, {"CODEHUB_SERVER__BIND": "localhost"}):
            with pytest.raises(ValidationError) as exc_info:
                ServerConfig()
            assert "must be in format 'host:port'" in str(exc_info.value)

    def test_bind_validation_invalid_port(self):
        """Invalid port number should produce clear error."""
        with patch.dict(os.environ, {"CODEHUB_SERVER__BIND": ":abc"}):
            with pytest.raises(ValidationError) as exc_info:
                ServerConfig()
            assert "must be a number" in str(exc_info.value)

    def test_bind_validation_port_out_of_range(self):
        """Port out of range should produce clear error."""
        with patch.dict(os.environ, {"CODEHUB_SERVER__BIND": ":70000"}):
            with pytest.raises(ValidationError) as exc_info:
                ServerConfig()
            assert "must be between 1 and 65535" in str(exc_info.value)

    def test_public_base_url_validation_empty(self):
        """Empty public_base_url should produce clear error."""
        with patch.dict(os.environ, {"CODEHUB_SERVER__PUBLIC_BASE_URL": ""}):
            with pytest.raises(ValidationError) as exc_info:
                ServerConfig()
            assert "public_base_url cannot be empty" in str(exc_info.value)

    def test_public_base_url_validation_no_protocol(self):
        """URL without protocol should produce clear error."""
        with patch.dict(
            os.environ, {"CODEHUB_SERVER__PUBLIC_BASE_URL": "localhost:8080"}
        ):
            with pytest.raises(ValidationError) as exc_info:
                ServerConfig()
            assert "must start with http:// or https://" in str(exc_info.value)

    def test_public_base_url_trailing_slash_removed(self):
        """Trailing slash should be removed from public_base_url."""
        with patch.dict(
            os.environ, {"CODEHUB_SERVER__PUBLIC_BASE_URL": "http://localhost:8080/"}
        ):
            config = ServerConfig()
            assert config.public_base_url == "http://localhost:8080"


class TestSessionConfig:
    """Tests for SessionConfig."""

    def test_default_values(self):
        """Session config should have sensible defaults."""
        config = SessionConfig()
        assert config.cookie_name == "session"
        assert config.ttl == "24h"

    def test_ttl_validation_valid(self):
        """Valid TTL formats should be accepted."""
        valid_ttls = ["30s", "30m", "24h", "7d"]
        for ttl in valid_ttls:
            with patch.dict(os.environ, {"CODEHUB_AUTH__SESSION__TTL": ttl}):
                config = SessionConfig()
                assert config.ttl == ttl

    def test_ttl_validation_invalid(self):
        """Invalid TTL format should produce clear error."""
        with patch.dict(os.environ, {"CODEHUB_AUTH__SESSION__TTL": "24hours"}):
            with pytest.raises(ValidationError) as exc_info:
                SessionConfig()
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
            with patch.dict(os.environ, {"CODEHUB_AUTH__SESSION__TTL": ttl}):
                config = SessionConfig()
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

    def test_duration_validation_valid(self):
        """Valid duration formats should be accepted."""
        valid_durations = ["500ms", "2s", "1m"]
        for duration in valid_durations:
            with patch.dict(
                os.environ, {"CODEHUB_WORKSPACE__HEALTHCHECK__INTERVAL": duration}
            ):
                config = HealthcheckConfig()
                assert config.interval == duration

    def test_duration_validation_invalid(self):
        """Invalid duration format should produce clear error."""
        with patch.dict(
            os.environ, {"CODEHUB_WORKSPACE__HEALTHCHECK__INTERVAL": "2sec"}
        ):
            with pytest.raises(ValidationError) as exc_info:
                HealthcheckConfig()
            assert "must be a number followed by ms" in str(exc_info.value)

    def test_duration_seconds_conversion(self):
        """Duration should be correctly converted to seconds."""
        config = HealthcheckConfig()
        with patch.dict(
            os.environ,
            {
                "CODEHUB_WORKSPACE__HEALTHCHECK__INTERVAL": "500ms",
                "CODEHUB_WORKSPACE__HEALTHCHECK__TIMEOUT": "2m",
            },
        ):
            config = HealthcheckConfig()
            assert config.interval_seconds() == 0.5
            assert config.timeout_seconds() == 120


class TestHomeStoreConfig:
    """Tests for HomeStoreConfig."""

    def test_default_values_require_workspace_base_dir(self):
        """HomeStoreConfig requires workspace_base_dir for local-dir backend."""
        with pytest.raises(ValidationError) as exc_info:
            HomeStoreConfig()
        assert "workspace_base_dir is required" in str(exc_info.value)

    def test_with_workspace_base_dir(self):
        """HomeStoreConfig should work with workspace_base_dir set."""
        with patch.dict(
            os.environ, {"CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR": "/host/data/home"}
        ):
            config = HomeStoreConfig()
            assert config.backend == "local-dir"
            assert config.control_plane_base_dir == "/data/home"
            assert config.workspace_base_dir == "/host/data/home"

    def test_control_plane_base_dir_validation_not_absolute(self):
        """Relative control_plane_base_dir should produce clear error."""
        with patch.dict(
            os.environ,
            {
                "CODEHUB_HOME_STORE__CONTROL_PLANE_BASE_DIR": "data/home",
                "CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR": "/host/data/home",
            },
        ):
            with pytest.raises(ValidationError) as exc_info:
                HomeStoreConfig()
            assert "must be an absolute path" in str(exc_info.value)

    def test_control_plane_base_dir_trailing_slash_removed(self):
        """Trailing slash should be removed from control_plane_base_dir."""
        with patch.dict(
            os.environ,
            {
                "CODEHUB_HOME_STORE__CONTROL_PLANE_BASE_DIR": "/data/home/",
                "CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR": "/host/data/home",
            },
        ):
            config = HomeStoreConfig()
            assert config.control_plane_base_dir == "/data/home"


class TestWorkspaceConfig:
    """Tests for WorkspaceConfig."""

    def test_default_values(self):
        """Workspace config should have sensible defaults."""
        config = WorkspaceConfig()
        assert config.default_image == "codercom/code-server:latest"

    def test_default_image_validation_empty(self):
        """Empty default_image should produce clear error."""
        with patch.dict(os.environ, {"CODEHUB_WORKSPACE__DEFAULT_IMAGE": ""}):
            with pytest.raises(ValidationError) as exc_info:
                WorkspaceConfig()
            assert "default_image cannot be empty" in str(exc_info.value)

    def test_default_image_validation_spaces(self):
        """Image name with spaces should produce clear error."""
        with patch.dict(
            os.environ, {"CODEHUB_WORKSPACE__DEFAULT_IMAGE": "invalid image"}
        ):
            with pytest.raises(ValidationError) as exc_info:
                WorkspaceConfig()
            assert "cannot contain spaces" in str(exc_info.value)


class TestSettings:
    """Tests for main Settings class."""

    def test_env_only_boot_with_required_vars(self):
        """Settings should boot with only required environment variables.

        Exit Criteria: env-only로도 부팅 가능
        """
        with patch.dict(
            os.environ,
            {
                "CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR": "/host/data/home",
            },
            clear=False,
        ):
            settings = validate_settings()
            assert settings.server.bind == ":8080"
            assert settings.server.public_base_url == "http://localhost:8080"
            assert settings.auth.mode == "local"
            assert settings.workspace.default_image == "codercom/code-server:latest"
            assert settings.home_store.backend == "local-dir"
            assert settings.home_store.control_plane_base_dir == "/data/home"

    def test_all_settings_from_env(self):
        """All settings should be configurable via environment variables."""
        env_vars = {
            "CODEHUB_SERVER__BIND": ":9000",
            "CODEHUB_SERVER__PUBLIC_BASE_URL": "https://example.com",
            "CODEHUB_AUTH__SESSION__COOKIE_NAME": "my_session",
            "CODEHUB_AUTH__SESSION__TTL": "7d",
            "CODEHUB_WORKSPACE__DEFAULT_IMAGE": "custom/image:v1",
            "CODEHUB_WORKSPACE__HEALTHCHECK__INTERVAL": "5s",
            "CODEHUB_WORKSPACE__HEALTHCHECK__TIMEOUT": "2m",
            "CODEHUB_HOME_STORE__CONTROL_PLANE_BASE_DIR": "/custom/home",
            "CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR": "/host/custom/home",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            settings = validate_settings()
            assert settings.server.bind == ":9000"
            assert settings.server.public_base_url == "https://example.com"
            assert settings.auth.session.cookie_name == "my_session"
            assert settings.auth.session.ttl == "7d"
            assert settings.workspace.default_image == "custom/image:v1"
            assert settings.workspace.healthcheck.interval == "5s"
            assert settings.workspace.healthcheck.timeout == "2m"
            assert settings.home_store.control_plane_base_dir == "/custom/home"
            assert settings.home_store.workspace_base_dir == "/host/custom/home"

    def test_validation_error_is_clear(self):
        """Invalid configuration should produce clear error messages.

        Exit Criteria: 잘못된 값은 명확한 에러
        """
        with patch.dict(
            os.environ,
            {
                "CODEHUB_SERVER__BIND": "invalid",
                "CODEHUB_HOME_STORE__WORKSPACE_BASE_DIR": "/host/data/home",
            },
            clear=False,
        ):
            with pytest.raises(ValidationError) as exc_info:
                validate_settings()
            error_str = str(exc_info.value)
            assert "bind" in error_str.lower() or "host:port" in error_str


class TestAuthConfig:
    """Tests for AuthConfig."""

    def test_default_values(self):
        """Auth config should have sensible defaults."""
        config = AuthConfig()
        assert config.mode == "local"
        assert config.session.cookie_name == "session"
        assert config.session.ttl == "24h"
