"""Tests for the instance controller interface module."""

import pytest
from pydantic import ValidationError

from app.instance import InstanceController, InstanceStatus, UpstreamInfo


class TestUpstreamInfo:
    """Tests for UpstreamInfo model."""

    def test_valid_upstream_info(self):
        """UpstreamInfo should accept valid data."""
        info = UpstreamInfo(host="127.0.0.1", port=8080)
        assert info.host == "127.0.0.1"
        assert info.port == 8080

    def test_upstream_info_requires_host(self):
        """UpstreamInfo should require host."""
        with pytest.raises(ValidationError):
            UpstreamInfo(port=8080)  # type: ignore[call-arg]

    def test_upstream_info_requires_port(self):
        """UpstreamInfo should require port."""
        with pytest.raises(ValidationError):
            UpstreamInfo(host="127.0.0.1")  # type: ignore[call-arg]

    def test_upstream_info_serialization(self):
        """UpstreamInfo should serialize to dict."""
        info = UpstreamInfo(host="localhost", port=3000)
        dumped = info.model_dump()
        assert dumped == {"host": "localhost", "port": 3000}

    def test_upstream_info_json(self):
        """UpstreamInfo should serialize to JSON."""
        info = UpstreamInfo(host="127.0.0.1", port=8443)
        json_str = info.model_dump_json()
        assert '"host":"127.0.0.1"' in json_str
        assert '"port":8443' in json_str


class TestInstanceStatus:
    """Tests for InstanceStatus model."""

    def test_container_not_exists(self):
        """InstanceStatus for non-existent container."""
        status = InstanceStatus(exists=False, running=False, healthy=False)
        assert status.exists is False
        assert status.running is False
        assert status.healthy is False
        assert status.port is None

    def test_container_exists_but_stopped(self):
        """InstanceStatus for stopped container."""
        status = InstanceStatus(exists=True, running=False, healthy=False)
        assert status.exists is True
        assert status.running is False
        assert status.healthy is False
        assert status.port is None

    def test_container_running_but_unhealthy(self):
        """InstanceStatus for running but unhealthy container."""
        status = InstanceStatus(
            exists=True,
            running=True,
            healthy=False,
            port=8080,
        )
        assert status.exists is True
        assert status.running is True
        assert status.healthy is False
        assert status.port == 8080

    def test_container_running_and_healthy(self):
        """InstanceStatus for healthy running container."""
        status = InstanceStatus(
            exists=True,
            running=True,
            healthy=True,
            port=8080,
        )
        assert status.exists is True
        assert status.running is True
        assert status.healthy is True
        assert status.port == 8080

    def test_status_requires_exists(self):
        """InstanceStatus should require exists field."""
        with pytest.raises(ValidationError):
            InstanceStatus(running=False, healthy=False)  # type: ignore[call-arg]

    def test_status_requires_running(self):
        """InstanceStatus should require running field."""
        with pytest.raises(ValidationError):
            InstanceStatus(exists=True, healthy=False)  # type: ignore[call-arg]

    def test_status_requires_healthy(self):
        """InstanceStatus should require healthy field."""
        with pytest.raises(ValidationError):
            InstanceStatus(exists=True, running=True)  # type: ignore[call-arg]

    def test_status_port_optional(self):
        """InstanceStatus port should be optional."""
        status = InstanceStatus(exists=True, running=True, healthy=True)
        assert status.port is None

    def test_status_serialization(self):
        """InstanceStatus should serialize to dict."""
        status = InstanceStatus(
            exists=True,
            running=True,
            healthy=True,
            port=8080,
        )
        dumped = status.model_dump()
        assert dumped == {
            "exists": True,
            "running": True,
            "healthy": True,
            "port": 8080,
        }

    def test_status_json(self):
        """InstanceStatus should serialize to JSON."""
        status = InstanceStatus(exists=False, running=False, healthy=False)
        json_str = status.model_dump_json()
        assert '"exists":false' in json_str
        assert '"running":false' in json_str
        assert '"healthy":false' in json_str


class TestInstanceControllerInterface:
    """Tests for InstanceController ABC."""

    def test_cannot_instantiate_abstract_class(self):
        """InstanceController should not be directly instantiable."""
        with pytest.raises(TypeError, match="abstract"):
            InstanceController()  # type: ignore[abstract]

    def test_subclass_must_implement_all_methods(self):
        """Subclass must implement all abstract methods."""

        class IncompleteController(InstanceController):
            pass

        with pytest.raises(TypeError, match="abstract"):
            IncompleteController()  # type: ignore[abstract]

    def test_partial_implementation_still_abstract(self):
        """Partial implementation should still be abstract."""

        class PartialController(InstanceController):
            @property
            def backend_name(self):
                return "local-docker"

        with pytest.raises(TypeError, match="abstract"):
            PartialController()  # type: ignore[abstract]

    def test_interface_has_all_required_methods(self):
        """InstanceController should define all spec methods."""
        required_methods = {
            "start_workspace",
            "stop_workspace",
            "delete_workspace",
            "resolve_upstream",
            "get_status",
        }
        required_properties = {"backend_name"}

        # Check abstract methods
        abstract_methods = set(InstanceController.__abstractmethods__)
        assert required_methods.issubset(abstract_methods)
        assert required_properties.issubset(abstract_methods)
