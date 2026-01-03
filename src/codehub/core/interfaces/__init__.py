"""Abstract base classes for adapters."""

from codehub.core.interfaces.instance import ContainerInfo, InstanceController
from codehub.core.interfaces.storage import (
    ArchiveInfo,
    StorageProvider,
    VolumeInfo,
)

__all__ = [
    "ContainerInfo",
    "InstanceController",
    "ArchiveInfo",
    "StorageProvider",
    "VolumeInfo",
]
