"""Storage Job helpers for archive/restore operations.

Provides utilities for Spec-v2 Storage Job implementation:
- sha256 checksum computation
- .meta file parsing/creation
- Helper container configuration
"""

import hashlib

# Helper image with zstd and rsync
HELPER_IMAGE = "alpine:latest"


def parse_meta(content: bytes) -> str | None:
    """Parse .meta file and return sha256 hash.

    Args:
        content: Raw .meta file content

    Returns:
        SHA256 hex string or None if invalid format
    """
    try:
        text = content.decode("utf-8").strip()
        if text.startswith("sha256:"):
            return text[7:]
    except (UnicodeDecodeError, ValueError):
        pass
    return None


def create_meta(sha256_hex: str) -> bytes:
    """Create .meta file content.

    Args:
        sha256_hex: SHA256 hex string

    Returns:
        .meta file content as bytes
    """
    return f"sha256:{sha256_hex}".encode("utf-8")


def compute_sha256(data: bytes) -> str:
    """Compute SHA256 hash of data.

    Args:
        data: Raw bytes to hash

    Returns:
        SHA256 hex string
    """
    return hashlib.sha256(data).hexdigest()
