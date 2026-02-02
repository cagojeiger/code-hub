"""Core interfaces for Control Plane."""

from codehub.core.interfaces.leader import LeaderElection
from codehub.core.interfaces.runtime import (
    ArchiveStatus,
    ContainerStatus,
    GCResult,
    UpstreamInfo,
    VolumeStatus,
    WorkspaceRuntime,
    WorkspaceState,
)

__all__ = [
    # Leader Election
    "LeaderElection",
    # WorkspaceRuntime interface
    "WorkspaceRuntime",
    "WorkspaceState",
    "ContainerStatus",
    "VolumeStatus",
    "ArchiveStatus",
    "UpstreamInfo",
    "GCResult",
]
