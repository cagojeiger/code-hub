"""Unit tests for LocalDirStorageProvider."""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from app.services.storage import LocalDirStorageProvider


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
    """Create a LocalDirStorageProvider with same paths (test environment)."""
    return LocalDirStorageProvider(
        control_plane_base_dir=temp_base_dir,
        workspace_base_dir=temp_base_dir,
    )


class TestBackendName:

    def test_returns_local_dir(self, provider: LocalDirStorageProvider) -> None:
        assert provider.backend_name == "local-dir"


class TestProvision:

    def test_creates_directory(
        self, provider: LocalDirStorageProvider, temp_base_dir: str
    ) -> None:
        key = "users/alice/workspaces/ws1/home"

        result = run_async(provider.provision(key))

        expected_path = f"{temp_base_dir}/{key}"
        assert os.path.isdir(expected_path)
        assert result.home_mount == expected_path
        assert result.home_ctx == expected_path

    def test_idempotency(self, provider: LocalDirStorageProvider) -> None:
        key = "users/bob/workspaces/ws2/home"

        result1 = run_async(provider.provision(key))
        result2 = run_async(provider.provision(key))

        assert result1.home_mount == result2.home_mount
        assert result1.home_ctx == result2.home_ctx

    def test_idempotency_with_file_inside(
        self, temp_base_dir: str
    ) -> None:
        """provision should preserve existing files (idempotency)."""
        provider = LocalDirStorageProvider(
            control_plane_base_dir=temp_base_dir,
            workspace_base_dir=temp_base_dir,
        )
        key = "users/charlie/workspaces/ws3/home"

        result1 = run_async(provider.provision(key))

        # Use internal path for file operations (same as control_plane_base_dir in test)
        internal_path = f"{temp_base_dir}/{key}"
        test_file = Path(internal_path) / "test.txt"
        test_file.write_text("hello")

        result2 = run_async(provider.provision(key))

        assert result1.home_mount == result2.home_mount
        assert test_file.exists()
        assert test_file.read_text() == "hello"

    def test_with_existing_ctx(self, provider: LocalDirStorageProvider) -> None:
        key = "users/dave/workspaces/ws4/home"

        result1 = run_async(provider.provision(key))
        result2 = run_async(provider.provision(key, existing_ctx=result1.home_ctx))

        assert result1.home_mount == result2.home_mount

    def test_deterministic_path(self, temp_base_dir: str) -> None:
        provider1 = LocalDirStorageProvider(
            control_plane_base_dir=temp_base_dir,
            workspace_base_dir=temp_base_dir,
        )
        provider2 = LocalDirStorageProvider(
            control_plane_base_dir=temp_base_dir,
            workspace_base_dir=temp_base_dir,
        )
        key = "users/eve/workspaces/ws5/home"

        result1 = run_async(provider1.provision(key))
        result2 = run_async(provider2.provision(key))

        assert result1.home_mount == result2.home_mount

    def test_trailing_slash_normalization(self, temp_base_dir: str) -> None:
        provider_with_slash = LocalDirStorageProvider(
            control_plane_base_dir=f"{temp_base_dir}/",
            workspace_base_dir=f"{temp_base_dir}/",
        )
        provider_without_slash = LocalDirStorageProvider(
            control_plane_base_dir=temp_base_dir,
            workspace_base_dir=temp_base_dir,
        )
        key = "users/frank/workspaces/ws6/home"

        result1 = run_async(provider_with_slash.provision(key))
        result2 = run_async(provider_without_slash.provision(key))

        assert result1.home_mount == result2.home_mount

    def test_different_internal_external_paths(self) -> None:
        """Test with different control_plane and workspace paths (Docker scenario)."""
        with tempfile.TemporaryDirectory() as internal_dir:
            external_dir = "/host/fake/path"  # Simulated host path
            provider = LocalDirStorageProvider(
                control_plane_base_dir=internal_dir,
                workspace_base_dir=external_dir,
            )
            key = "users/test/workspaces/ws/home"

            result = run_async(provider.provision(key))

            # Directory created at internal path
            assert os.path.isdir(f"{internal_dir}/{key}")
            # home_mount returns external path
            assert result.home_mount == f"{external_dir}/{key}"


class TestDeprovision:

    def test_is_noop(
        self, provider: LocalDirStorageProvider, temp_base_dir: str
    ) -> None:
        key = "users/grace/workspaces/ws7/home"
        run_async(provider.provision(key))

        run_async(provider.deprovision(f"{temp_base_dir}/{key}"))

        assert os.path.isdir(f"{temp_base_dir}/{key}")

    def test_with_none_ctx(self, provider: LocalDirStorageProvider) -> None:
        run_async(provider.deprovision(None))


class TestPurge:

    def test_removes_directory(
        self, provider: LocalDirStorageProvider, temp_base_dir: str
    ) -> None:
        key = "users/henry/workspaces/ws8/home"
        run_async(provider.provision(key))
        internal_path = f"{temp_base_dir}/{key}"
        assert os.path.isdir(internal_path)

        run_async(provider.purge(key))

        assert not os.path.exists(internal_path)

    def test_removes_directory_with_contents(
        self, provider: LocalDirStorageProvider, temp_base_dir: str
    ) -> None:
        key = "users/iris/workspaces/ws9/home"
        run_async(provider.provision(key))
        internal_path = f"{temp_base_dir}/{key}"

        test_file = Path(internal_path) / "data.txt"
        test_file.write_text("important data")
        subdir = Path(internal_path) / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("nested")

        run_async(provider.purge(key))

        assert not os.path.exists(internal_path)

    def test_nonexistent_is_noop(self, provider: LocalDirStorageProvider) -> None:
        key = "users/nonexistent/workspaces/ws/home"
        run_async(provider.purge(key))


class TestGetStatus:

    def test_provisioned(self, provider: LocalDirStorageProvider) -> None:
        key = "users/jack/workspaces/ws10/home"
        result = run_async(provider.provision(key))

        status = run_async(provider.get_status(key))

        assert status.provisioned is True
        assert status.home_mount == result.home_mount
        assert status.home_ctx == result.home_ctx

    def test_not_provisioned(self, provider: LocalDirStorageProvider) -> None:
        key = "users/kate/workspaces/ws11/home"

        status = run_async(provider.get_status(key))

        assert status.provisioned is False
        assert status.home_mount is None
        assert status.home_ctx is None

    def test_after_purge(self, provider: LocalDirStorageProvider) -> None:
        key = "users/leo/workspaces/ws12/home"
        run_async(provider.provision(key))
        run_async(provider.purge(key))

        status = run_async(provider.get_status(key))

        assert status.provisioned is False
