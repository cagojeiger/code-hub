"""Storage Provider module for code-hub.

Provides storage backend abstraction for workspace home directories.
MVP supports local-dir backend; object-store planned for cloud.
"""

from app.services.storage.interface import (
    ProvisionResult,
    StorageProvider,
    StorageStatus,
)
from app.services.storage.local_dir import LocalDirStorageProvider

__all__ = [
    "LocalDirStorageProvider",
    "ProvisionResult",
    "StorageProvider",
    "StorageStatus",
]
