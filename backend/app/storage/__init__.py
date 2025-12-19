"""Storage Provider module for code-hub.

Provides storage backend abstraction for workspace home directories.
MVP supports local-dir backend; object-store planned for cloud.
"""

from app.storage.interface import ProvisionResult, StorageProvider, StorageStatus

__all__ = [
    "ProvisionResult",
    "StorageProvider",
    "StorageStatus",
]
