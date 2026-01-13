"""Docker runtime for Agent."""

from codehub_agent.config import AgentConfig, get_agent_config
from codehub_agent.runtimes.docker.instance import InstanceManager
from codehub_agent.runtimes.docker.job import JobRunner
from codehub_agent.runtimes.docker.naming import ResourceNaming
from codehub_agent.runtimes.docker.storage import StorageManager
from codehub_agent.runtimes.docker.volume import VolumeManager


class DockerRuntime:
    """Docker runtime combining instance, volume, job, and storage management."""

    def __init__(self, config: AgentConfig | None = None) -> None:
        self._config = config or get_agent_config()
        self._naming = ResourceNaming(self._config)

        self.instances = InstanceManager(self._config, self._naming)
        self.volumes = VolumeManager(self._config, self._naming)
        self.jobs = JobRunner(self._config, self._naming)
        self.storage = StorageManager(self._config, self._naming)


__all__ = [
    "DockerRuntime",
    "InstanceManager",
    "VolumeManager",
    "JobRunner",
    "StorageManager",
    "ResourceNaming",
]
