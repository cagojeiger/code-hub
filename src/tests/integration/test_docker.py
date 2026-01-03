"""Integration tests for Docker infrastructure module."""

import pytest

from codehub.infra.docker import (
    ContainerAPI,
    ContainerConfig,
    HostConfig,
    VolumeAPI,
    VolumeConfig,
)


@pytest.mark.integration
class TestVolumeAPI:
    """VolumeAPI integration tests."""

    @pytest.mark.asyncio
    async def test_volume_lifecycle(self, volume_api: VolumeAPI, test_prefix: str):
        """Test volume create, inspect, remove lifecycle."""
        name = f"{test_prefix}vol"

        # Create
        await volume_api.create(VolumeConfig(name=name))

        # Inspect
        data = await volume_api.inspect(name)
        assert data is not None
        assert data.get("Name") == name

        # Remove
        await volume_api.remove(name)

        # Verify removal
        data = await volume_api.inspect(name)
        assert data is None

    @pytest.mark.asyncio
    async def test_volume_list(self, volume_api: VolumeAPI, test_prefix: str):
        """Test volume list with filter."""
        name = f"{test_prefix}list-vol"

        # Create
        await volume_api.create(VolumeConfig(name=name))

        try:
            # List with filter
            volumes = await volume_api.list(filters={"name": [test_prefix]})
            found = any(v.get("Name") == name for v in volumes)
            assert found, f"Volume {name} not found in list"
        finally:
            # Cleanup
            await volume_api.remove(name)

    @pytest.mark.asyncio
    async def test_volume_create_idempotent(
        self, volume_api: VolumeAPI, test_prefix: str
    ):
        """Test that creating an existing volume is idempotent."""
        name = f"{test_prefix}idempotent-vol"

        try:
            # Create twice - should not raise
            await volume_api.create(VolumeConfig(name=name))
            await volume_api.create(VolumeConfig(name=name))

            # Should still exist
            data = await volume_api.inspect(name)
            assert data is not None
        finally:
            await volume_api.remove(name)


# Test image - use python:3.13-slim which is already pulled for Dockerfile.test
TEST_IMAGE = "python:3.13-slim"


@pytest.mark.integration
class TestContainerAPI:
    """ContainerAPI integration tests."""

    @pytest.mark.asyncio
    async def test_container_lifecycle(
        self, container_api: ContainerAPI, test_prefix: str
    ):
        """Test container create, start, stop, remove lifecycle."""
        name = f"{test_prefix}container"

        config = ContainerConfig(
            image=TEST_IMAGE,
            name=name,
            cmd=["python", "-c", "import time; time.sleep(30)"],
        )

        try:
            # Create
            await container_api.create(config)

            # Inspect after create
            data = await container_api.inspect(name)
            assert data is not None

            # Start
            await container_api.start(name)

            # Verify running
            data = await container_api.inspect(name)
            assert data["State"]["Running"] is True

            # Stop
            await container_api.stop(name, timeout=1)

            # Verify stopped
            data = await container_api.inspect(name)
            assert data["State"]["Running"] is False

        finally:
            # Cleanup
            await container_api.remove(name)

        # Verify removal
        data = await container_api.inspect(name)
        assert data is None

    @pytest.mark.asyncio
    async def test_container_with_volume(
        self,
        container_api: ContainerAPI,
        volume_api: VolumeAPI,
        test_prefix: str,
    ):
        """Test container with volume mount."""
        vol_name = f"{test_prefix}home"
        container_name = f"{test_prefix}ws"

        try:
            # Create volume
            await volume_api.create(VolumeConfig(name=vol_name))

            # Create container with volume mount
            config = ContainerConfig(
                image=TEST_IMAGE,
                name=container_name,
                cmd=["python", "-c", "open('/home/test.txt', 'w').write('hello'); import time; time.sleep(5)"],
                user="1000:1000",
                env=["HOME=/home"],
                host_config=HostConfig(
                    binds=[f"{vol_name}:/home"],
                ),
            )
            await container_api.create(config)
            await container_api.start(container_name)

            # Verify volume is mounted
            data = await container_api.inspect(container_name)
            mounts = data.get("Mounts", [])
            assert any(m.get("Name") == vol_name for m in mounts), "Volume not mounted"

        finally:
            # Cleanup
            await container_api.stop(container_name, timeout=1)
            await container_api.remove(container_name)
            await volume_api.remove(vol_name)

    @pytest.mark.asyncio
    async def test_container_list(
        self, container_api: ContainerAPI, test_prefix: str
    ):
        """Test container list with filter."""
        name = f"{test_prefix}list-container"

        config = ContainerConfig(
            image=TEST_IMAGE,
            name=name,
            cmd=["python", "-c", "import time; time.sleep(30)"],
        )

        try:
            await container_api.create(config)

            # List with filter
            containers = await container_api.list(filters={"name": [test_prefix]})
            found = any(name in c.get("Names", [""])[0] for c in containers)
            assert found, f"Container {name} not found in list"

        finally:
            await container_api.remove(name)

    @pytest.mark.asyncio
    async def test_container_create_idempotent(
        self, container_api: ContainerAPI, test_prefix: str
    ):
        """Test that creating an existing container is idempotent."""
        name = f"{test_prefix}idempotent-container"

        config = ContainerConfig(
            image=TEST_IMAGE,
            name=name,
            cmd=["python", "-c", "import time; time.sleep(10)"],
        )

        try:
            # Create twice - should not raise
            await container_api.create(config)
            await container_api.create(config)

            # Should still exist
            data = await container_api.inspect(name)
            assert data is not None
        finally:
            await container_api.remove(name)
