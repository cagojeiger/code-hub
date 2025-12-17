"""Tests for Config module.

Exit Criteria verification:
1. env-only로도 부팅 가능 (can boot with environment variables only)
2. 잘못된 값은 명확한 에러 (invalid values produce clear errors)
"""

import os
from unittest import mock

import pytest
from pydantic import ValidationError

from app.core.config import (
    AuthSettings,
    HealthcheckSettings,
    HomeStoreSettings,
    ServerSettings,
    SessionSettings,
    Settings,
    WorkspaceSettings,
    get_settings,
)


class TestDefaultValues:
    """Test that all settings have sensible defaults for development."""

    def test_settings_load_with_defaults(self):
        """Settings should load with default values (Exit Criteria 1)."""
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = Settings()

        assert settings.env == "development"
        assert settings.server.bind == ":8080"
        assert settings.server.public_base_url == "http://localhost:8080"
        assert settings.auth.mode == "local"
        assert settings.auth.session.cookie_name == "session"
        assert settings.auth.session.ttl_seconds == 86400
        assert settings.workspace.default_image == "codercom/code-server:latest"
        assert settings.workspace.healthcheck.type == "http"
        assert settings.workspace.healthcheck.path == "/healthz"
        assert settings.workspace.healthcheck.interval_seconds == 2.0
        assert settings.workspace.healthcheck.timeout_seconds == 60.0
        assert settings.home_store.backend == "local-dir"
        assert settings.home_store.base_dir == "/data/home"

    def test_server_settings_defaults(self):
        """ServerSettings should have correct defaults."""
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = ServerSettings()

        assert settings.bind == ":8080"
        assert settings.public_base_url == "http://localhost:8080"

    def test_auth_settings_defaults(self):
        """AuthSettings should have correct defaults."""
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = AuthSettings()

        assert settings.mode == "local"
        assert settings.session.cookie_name == "session"
        assert settings.session.ttl_seconds == 86400

    def test_workspace_settings_defaults(self):
        """WorkspaceSettings should have correct defaults."""
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = WorkspaceSettings()

        assert settings.default_image == "codercom/code-server:latest"
        assert settings.healthcheck.type == "http"
        assert settings.healthcheck.path == "/healthz"

    def test_home_store_settings_defaults(self):
        """HomeStoreSettings should have correct defaults."""
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = HomeStoreSettings()

        assert settings.backend == "local-dir"
        assert settings.base_dir == "/data/home"
        assert settings.host_path is None


class TestEnvironmentVariableOverrides:
    """Test that environment variables correctly override defaults."""

    def test_server_settings_from_env(self):
        """ServerSettings should load from environment variables."""
        env = {
            "CODEHUB_SERVER__BIND": "0.0.0.0:9000",
            "CODEHUB_SERVER__PUBLIC_BASE_URL": "https://example.com",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            settings = ServerSettings()

        assert settings.bind == "0.0.0.0:9000"
        assert settings.public_base_url == "https://example.com"

    def test_auth_settings_from_env(self):
        """AuthSettings should load from environment variables."""
        env = {
            "CODEHUB_AUTH__MODE": "local",
            "CODEHUB_AUTH__SESSION_COOKIE_NAME": "codehub_session",
            "CODEHUB_AUTH__SESSION_TTL_SECONDS": "3600",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            settings = AuthSettings()

        assert settings.mode == "local"
        assert settings.session.cookie_name == "codehub_session"
        assert settings.session.ttl_seconds == 3600

    def test_workspace_settings_from_env(self):
        """WorkspaceSettings should load from environment variables."""
        env = {
            "CODEHUB_WORKSPACE__DEFAULT_IMAGE": "custom/code-server:v1",
            "CODEHUB_WORKSPACE__HEALTHCHECK_TYPE": "tcp",
            "CODEHUB_WORKSPACE__HEALTHCHECK_PATH": "/health",
            "CODEHUB_WORKSPACE__HEALTHCHECK_INTERVAL_SECONDS": "5",
            "CODEHUB_WORKSPACE__HEALTHCHECK_TIMEOUT_SECONDS": "120",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            settings = WorkspaceSettings()

        assert settings.default_image == "custom/code-server:v1"
        assert settings.healthcheck.type == "tcp"
        assert settings.healthcheck.path == "/health"
        assert settings.healthcheck.interval_seconds == 5.0
        assert settings.healthcheck.timeout_seconds == 120.0

    def test_home_store_settings_from_env(self):
        """HomeStoreSettings should load from environment variables."""
        env = {
            "CODEHUB_HOME_STORE__BACKEND": "local-dir",
            "CODEHUB_HOME_STORE__BASE_DIR": "/custom/data",
            "CODEHUB_HOME_STORE__HOST_PATH": "/host/data",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            settings = HomeStoreSettings()

        assert settings.backend == "local-dir"
        assert settings.base_dir == "/custom/data"
        assert settings.host_path == "/host/data"

    def test_full_settings_from_env(self):
        """Full Settings should load from environment variables (Exit Criteria 1)."""
        env = {
            "CODEHUB_ENV": "production",
            "CODEHUB_SERVER__BIND": ":9000",
            "CODEHUB_SERVER__PUBLIC_BASE_URL": "https://prod.example.com",
            "CODEHUB_HOME_STORE__BASE_DIR": "/prod/data",
            "CODEHUB_HOME_STORE__HOST_PATH": "/host/prod/data",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            settings = Settings()

        assert settings.env == "production"
        assert settings.server.bind == ":9000"
        assert settings.server.public_base_url == "https://prod.example.com"
        assert settings.home_store.base_dir == "/prod/data"
        assert settings.home_store.host_path == "/host/prod/data"


class TestValidationErrors:
    """Test that invalid values produce clear error messages (Exit Criteria 2)."""

    def test_invalid_auth_mode(self):
        """Invalid auth mode should produce clear error."""
        env = {"CODEHUB_AUTH__MODE": "oauth"}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                AuthSettings()

        error = exc_info.value
        assert "mode" in str(error)
        assert "oauth" in str(error) or "literal" in str(error).lower()

    def test_invalid_session_ttl(self):
        """Invalid session TTL should produce clear error."""
        env = {"CODEHUB_AUTH__SESSION_TTL_SECONDS": "-100"}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                SessionSettings()

        error = exc_info.value
        assert "ttl_seconds" in str(error)

    def test_invalid_healthcheck_type(self):
        """Invalid healthcheck type should produce clear error."""
        env = {"CODEHUB_WORKSPACE__HEALTHCHECK_TYPE": "grpc"}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                HealthcheckSettings()

        error = exc_info.value
        assert "type" in str(error)

    def test_invalid_home_store_backend(self):
        """Invalid home store backend should produce clear error."""
        env = {"CODEHUB_HOME_STORE__BACKEND": "s3"}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                HomeStoreSettings()

        error = exc_info.value
        assert "backend" in str(error)

    def test_relative_base_dir_error(self):
        """Relative base_dir should produce clear error."""
        env = {"CODEHUB_HOME_STORE__BASE_DIR": "data/home"}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                HomeStoreSettings()

        error = exc_info.value
        assert "base_dir" in str(error)
        assert "absolute" in str(error).lower()

    def test_relative_host_path_error(self):
        """Relative host_path should produce clear error."""
        env = {"CODEHUB_HOME_STORE__HOST_PATH": "host/data"}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                HomeStoreSettings()

        error = exc_info.value
        assert "host_path" in str(error)
        assert "absolute" in str(error).lower()

    def test_empty_default_image_error(self):
        """Empty default_image should produce clear error."""
        env = {"CODEHUB_WORKSPACE__DEFAULT_IMAGE": ""}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                WorkspaceSettings()

        error = exc_info.value
        assert "default_image" in str(error)
        assert "empty" in str(error).lower()

    def test_invalid_env_value(self):
        """Invalid env value should produce clear error."""
        env = {"CODEHUB_ENV": "staging"}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                Settings()

        error = exc_info.value
        assert "env" in str(error)

    def test_healthcheck_timeout_less_than_interval(self):
        """Timeout less than interval should produce clear error."""
        env = {
            "CODEHUB_WORKSPACE__HEALTHCHECK_INTERVAL_SECONDS": "10",
            "CODEHUB_WORKSPACE__HEALTHCHECK_TIMEOUT_SECONDS": "5",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                HealthcheckSettings()

        error = exc_info.value
        assert "timeout" in str(error).lower()
        assert "interval" in str(error).lower()


class TestProductionValidation:
    """Test production-specific validation rules."""

    def test_production_requires_host_path(self):
        """Production env should require home_store.host_path."""
        env = {
            "CODEHUB_ENV": "production",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                Settings()

        error = exc_info.value
        assert "host_path" in str(error)
        assert "production" in str(error).lower()

    def test_production_with_host_path_succeeds(self):
        """Production env with host_path should succeed."""
        env = {
            "CODEHUB_ENV": "production",
            "CODEHUB_HOME_STORE__HOST_PATH": "/host/data",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            settings = Settings()

        assert settings.env == "production"
        assert settings.home_store.host_path == "/host/data"

    def test_development_without_host_path_succeeds(self):
        """Development env without host_path should succeed."""
        env = {"CODEHUB_ENV": "development"}
        with mock.patch.dict(os.environ, env, clear=True):
            settings = Settings()

        assert settings.env == "development"
        assert settings.home_store.host_path is None


class TestValueNormalization:
    """Test that values are properly normalized."""

    def test_public_base_url_trailing_slash_removed(self):
        """public_base_url trailing slash should be removed."""
        env = {"CODEHUB_SERVER__PUBLIC_BASE_URL": "http://example.com/"}
        with mock.patch.dict(os.environ, env, clear=True):
            settings = ServerSettings()

        assert settings.public_base_url == "http://example.com"

    def test_base_dir_trailing_slash_removed(self):
        """base_dir trailing slash should be removed."""
        env = {"CODEHUB_HOME_STORE__BASE_DIR": "/data/home/"}
        with mock.patch.dict(os.environ, env, clear=True):
            settings = HomeStoreSettings()

        assert settings.base_dir == "/data/home"

    def test_host_path_trailing_slash_removed(self):
        """host_path trailing slash should be removed."""
        env = {"CODEHUB_HOME_STORE__HOST_PATH": "/host/data/"}
        with mock.patch.dict(os.environ, env, clear=True):
            settings = HomeStoreSettings()

        assert settings.host_path == "/host/data"

    def test_default_image_whitespace_trimmed(self):
        """default_image whitespace should be trimmed."""
        env = {"CODEHUB_WORKSPACE__DEFAULT_IMAGE": "  codercom/code-server  "}
        with mock.patch.dict(os.environ, env, clear=True):
            settings = WorkspaceSettings()

        assert settings.default_image == "codercom/code-server"


class TestGetSettings:
    """Test the get_settings function."""

    def test_get_settings_returns_settings(self):
        """get_settings should return a Settings instance."""
        # Clear the cache first
        get_settings.cache_clear()

        with mock.patch.dict(os.environ, {}, clear=True):
            settings = get_settings()

        assert isinstance(settings, Settings)

    def test_get_settings_is_cached(self):
        """get_settings should return cached instance."""
        get_settings.cache_clear()

        with mock.patch.dict(os.environ, {}, clear=True):
            settings1 = get_settings()
            settings2 = get_settings()

        assert settings1 is settings2
