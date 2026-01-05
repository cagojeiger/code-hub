"""Abstract base classes for adapters."""

from codehub.core.interfaces.instance import ContainerInfo, InstanceController
from codehub.core.interfaces.job import JobResult, JobRunner
from codehub.core.interfaces.leader import LeaderElection
from codehub.core.interfaces.storage import (
    ArchiveInfo,
    StorageProvider,
    VolumeInfo,
)
from codehub.core.interfaces.volume import VolumeProvider

__all__ = [
    "ContainerInfo",
    "InstanceController",
    "ArchiveInfo",
    "LeaderElection",
    "StorageProvider",
    "VolumeInfo",
    "VolumeProvider",
    "JobRunner",
    "JobResult",
]
