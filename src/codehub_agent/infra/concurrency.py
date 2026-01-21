"""Concurrency control for Agent."""

import asyncio
from functools import cache

DOCKER_READ_LIMIT = 50
DOCKER_WRITE_LIMIT = 10
JOB_LIMIT = 10


@cache
def get_docker_read_semaphore() -> asyncio.Semaphore:
    """Semaphore for Docker read operations (list, inspect)."""
    return asyncio.Semaphore(DOCKER_READ_LIMIT)


@cache
def get_docker_write_semaphore() -> asyncio.Semaphore:
    """Semaphore for Docker write operations (create, start, stop, remove)."""
    return asyncio.Semaphore(DOCKER_WRITE_LIMIT)


@cache
def get_job_semaphore() -> asyncio.Semaphore:
    """Semaphore for archive/restore jobs."""
    return asyncio.Semaphore(JOB_LIMIT)


def reset_semaphores() -> None:
    """Reset all semaphores. For testing."""
    get_docker_read_semaphore.cache_clear()
    get_docker_write_semaphore.cache_clear()
    get_job_semaphore.cache_clear()
