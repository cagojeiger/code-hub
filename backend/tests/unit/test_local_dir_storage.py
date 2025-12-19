"""Unit tests for LocalDirStorageProvider."""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from app.storage import LocalDirStorageProvider


def run_async(coro):
    """Helper to run async functions in sync tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def temp_base_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def provider(temp_base_dir: str) -> LocalDirStorageProvider:
    """Create a LocalDirStorageProvider with temp directory."""
    return LocalDirStorageProvider(workspace_base_dir=temp_base_dir)


class TestBackendName:
    """Tests for backend_name property."""

    def test_returns_local_dir(self, provider: LocalDirStorageProvider) -> None:
        """backend_name should return 'local-dir'."""
        assert provider.backend_name == "local-dir"


class TestProvision:
    """Tests for provision method."""

    def test_creates_directory(
        self, provider: LocalDirStorageProvider, temp_base_dir: str
    ) -> None:
        """provision should create directory."""
        key = "users/alice/workspaces/ws1/home"

        result = run_async(provider.provision(key))

        expected_path = f"{temp_base_dir}/{key}"
        assert os.path.isdir(expected_path)
        assert result.home_mount == expected_path
        assert result.home_ctx == expected_path

    def test_idempotency(self, provider: LocalDirStorageProvider) -> None:
        """provision should return same result for same key (idempotency)."""
        key = "users/bob/workspaces/ws2/home"

        result1 = run_async(provider.provision(key))
        result2 = run_async(provider.provision(key))

        assert result1.home_mount == result2.home_mount
        assert result1.home_ctx == result2.home_ctx

    def test_idempotency_with_file_inside(
        self, provider: LocalDirStorageProvider
    ) -> None:
        """provision should preserve existing files (idempotency)."""
        key = "users/charlie/workspaces/ws3/home"

        result1 = run_async(provider.provision(key))

        test_file = Path(result1.home_mount) / "test.txt"
        test_file.write_text("hello")

        result2 = run_async(provider.provision(key))

        assert result1.home_mount == result2.home_mount
        assert test_file.exists()
        assert test_file.read_text() == "hello"

    def test_with_existing_ctx(self, provider: LocalDirStorageProvider) -> None:
        """provision with existing_ctx should work correctly."""
        key = "users/dave/workspaces/ws4/home"

        result1 = run_async(provider.provision(key))
        result2 = run_async(provider.provision(key, existing_ctx=result1.home_ctx))

        assert result1.home_mount == result2.home_mount

    def test_deterministic_path(self, temp_base_dir: str) -> None:
        """provision should compute deterministic path."""
        provider1 = LocalDirStorageProvider(workspace_base_dir=temp_base_dir)
        provider2 = LocalDirStorageProvider(workspace_base_dir=temp_base_dir)
        key = "users/eve/workspaces/ws5/home"

        result1 = run_async(provider1.provision(key))
        result2 = run_async(provider2.provision(key))

        assert result1.home_mount == result2.home_mount

    def test_trailing_slash_normalization(self, temp_base_dir: str) -> None:
        """Provider should normalize trailing slash in base dir."""
        provider_with_slash = LocalDirStorageProvider(
            workspace_base_dir=f"{temp_base_dir}/"
        )
        provider_without_slash = LocalDirStorageProvider(
            workspace_base_dir=temp_base_dir
        )
        key = "users/frank/workspaces/ws6/home"

        result1 = run_async(provider_with_slash.provision(key))
        result2 = run_async(provider_without_slash.provision(key))

        assert result1.home_mount == result2.home_mount


class TestDeprovision:
    """Tests for deprovision method."""

    def test_is_noop(self, provider: LocalDirStorageProvider) -> None:
        """deprovision should be a no-op for local-dir."""
        key = "users/grace/workspaces/ws7/home"
        result = run_async(provider.provision(key))

        run_async(provider.deprovision(result.home_ctx))

        assert os.path.isdir(result.home_mount)

    def test_with_none_ctx(self, provider: LocalDirStorageProvider) -> None:
        """deprovision with None ctx should succeed."""
        run_async(provider.deprovision(None))


class TestPurge:
    """Tests for purge method."""

    def test_removes_directory(self, provider: LocalDirStorageProvider) -> None:
        """purge should remove the directory."""
        key = "users/henry/workspaces/ws8/home"
        result = run_async(provider.provision(key))
        assert os.path.isdir(result.home_mount)

        run_async(provider.purge(key))

        assert not os.path.exists(result.home_mount)

    def test_removes_directory_with_contents(
        self, provider: LocalDirStorageProvider
    ) -> None:
        """purge should remove directory including all contents."""
        key = "users/iris/workspaces/ws9/home"
        result = run_async(provider.provision(key))

        test_file = Path(result.home_mount) / "data.txt"
        test_file.write_text("important data")
        subdir = Path(result.home_mount) / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("nested")

        run_async(provider.purge(key))

        assert not os.path.exists(result.home_mount)

    def test_nonexistent_is_noop(self, provider: LocalDirStorageProvider) -> None:
        """purge on non-existent directory should succeed (idempotent)."""
        key = "users/nonexistent/workspaces/ws/home"

        run_async(provider.purge(key))


class TestGetStatus:
    """Tests for get_status method."""

    def test_provisioned(self, provider: LocalDirStorageProvider) -> None:
        """get_status should return provisioned=True for existing directory."""
        key = "users/jack/workspaces/ws10/home"
        result = run_async(provider.provision(key))

        status = run_async(provider.get_status(key))

        assert status.provisioned is True
        assert status.home_mount == result.home_mount
        assert status.home_ctx == result.home_ctx

    def test_not_provisioned(self, provider: LocalDirStorageProvider) -> None:
        """get_status should return provisioned=False for non-existing directory."""
        key = "users/kate/workspaces/ws11/home"

        status = run_async(provider.get_status(key))

        assert status.provisioned is False
        assert status.home_mount is None
        assert status.home_ctx is None

    def test_after_purge(self, provider: LocalDirStorageProvider) -> None:
        """get_status should return provisioned=False after purge."""
        key = "users/leo/workspaces/ws12/home"
        run_async(provider.provision(key))
        run_async(provider.purge(key))

        status = run_async(provider.get_status(key))

        assert status.provisioned is False
