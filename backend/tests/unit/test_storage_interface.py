"""Tests for the storage interface module."""

import pytest
from pydantic import ValidationError

from app.storage import ProvisionResult, StorageProvider, StorageStatus


class TestProvisionResult:
    """Tests for ProvisionResult model."""

    def test_valid_provision_result(self):
        """ProvisionResult should accept valid data."""
        result = ProvisionResult(
            home_mount="/host/path/to/home",
            home_ctx="/internal/path/to/home",
        )
        assert result.home_mount == "/host/path/to/home"
        assert result.home_ctx == "/internal/path/to/home"

    def test_provision_result_requires_home_mount(self):
        """ProvisionResult should require home_mount."""
        with pytest.raises(ValidationError):
            ProvisionResult(home_ctx="ctx")  # type: ignore[call-arg]

    def test_provision_result_requires_home_ctx(self):
        """ProvisionResult should require home_ctx."""
        with pytest.raises(ValidationError):
            ProvisionResult(home_mount="/path")  # type: ignore[call-arg]

    def test_provision_result_serialization(self):
        """ProvisionResult should serialize to dict."""
        result = ProvisionResult(
            home_mount="/mount",
            home_ctx="ctx",
        )
        dumped = result.model_dump()
        assert dumped == {"home_mount": "/mount", "home_ctx": "ctx"}

    def test_provision_result_json(self):
        """ProvisionResult should serialize to JSON."""
        result = ProvisionResult(
            home_mount="/mount",
            home_ctx="ctx",
        )
        json_str = result.model_dump_json()
        assert '"home_mount":"/mount"' in json_str
        assert '"home_ctx":"ctx"' in json_str


class TestStorageStatus:
    """Tests for StorageStatus model."""

    def test_not_provisioned_status(self):
        """StorageStatus for not provisioned state."""
        status = StorageStatus(provisioned=False)
        assert status.provisioned is False
        assert status.home_ctx is None
        assert status.home_mount is None

    def test_provisioned_status(self):
        """StorageStatus for provisioned state."""
        status = StorageStatus(
            provisioned=True,
            home_ctx="ctx",
            home_mount="/mount",
        )
        assert status.provisioned is True
        assert status.home_ctx == "ctx"
        assert status.home_mount == "/mount"

    def test_status_requires_provisioned(self):
        """StorageStatus should require provisioned field."""
        with pytest.raises(ValidationError):
            StorageStatus()  # type: ignore[call-arg]

    def test_status_optional_fields_default_to_none(self):
        """StorageStatus optional fields should default to None."""
        status = StorageStatus(provisioned=True)
        assert status.home_ctx is None
        assert status.home_mount is None

    def test_status_serialization(self):
        """StorageStatus should serialize to dict."""
        status = StorageStatus(
            provisioned=True,
            home_ctx="ctx",
            home_mount="/mount",
        )
        dumped = status.model_dump()
        assert dumped == {
            "provisioned": True,
            "home_ctx": "ctx",
            "home_mount": "/mount",
        }

    def test_status_json(self):
        """StorageStatus should serialize to JSON."""
        status = StorageStatus(provisioned=False)
        json_str = status.model_dump_json()
        assert '"provisioned":false' in json_str


class TestStorageProviderInterface:
    """Tests for StorageProvider ABC."""

    def test_cannot_instantiate_abstract_class(self):
        """StorageProvider should not be directly instantiable."""
        with pytest.raises(TypeError, match="abstract"):
            StorageProvider()  # type: ignore[abstract]

    def test_subclass_must_implement_all_methods(self):
        """Subclass must implement all abstract methods."""

        class IncompleteProvider(StorageProvider):
            pass

        with pytest.raises(TypeError, match="abstract"):
            IncompleteProvider()  # type: ignore[abstract]

    def test_partial_implementation_still_abstract(self):
        """Partial implementation should still be abstract."""

        class PartialProvider(StorageProvider):
            @property
            def backend_name(self):
                return "local-dir"

        with pytest.raises(TypeError, match="abstract"):
            PartialProvider()  # type: ignore[abstract]

    def test_interface_has_all_required_methods(self):
        """StorageProvider should define all spec methods."""
        required_methods = {"provision", "deprovision", "purge", "get_status"}
        required_properties = {"backend_name"}

        # Check abstract methods
        abstract_methods = set(StorageProvider.__abstractmethods__)
        assert required_methods.issubset(abstract_methods)
        assert required_properties.issubset(abstract_methods)
