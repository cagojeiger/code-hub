"""API dependencies for dependency injection."""

from codehub_agent.runtimes import DockerRuntime

# Singleton runtime instance
_runtime: DockerRuntime | None = None


def get_runtime() -> DockerRuntime:
    """Get runtime singleton.

    Returns:
        DockerRuntime instance shared across all API endpoints.
    """
    global _runtime
    if _runtime is None:
        _runtime = DockerRuntime()
    return _runtime


def reset_runtime() -> None:
    """Reset runtime singleton (for testing)."""
    global _runtime
    _runtime = None
