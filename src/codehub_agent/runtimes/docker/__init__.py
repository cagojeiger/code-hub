"""Docker runtime for Agent."""

from codehub_agent.runtimes.docker.instance import InstanceManager
from codehub_agent.runtimes.docker.job import JobRunner
from codehub_agent.runtimes.docker.volume import VolumeManager


class DockerRuntime:
    """Docker runtime combining instance, volume, and job management."""

    def __init__(self) -> None:
        self.instances = InstanceManager()
        self.volumes = VolumeManager()
        self.jobs = JobRunner()


__all__ = ["DockerRuntime", "InstanceManager", "VolumeManager", "JobRunner"]
