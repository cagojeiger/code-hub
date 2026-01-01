"""Security utilities for code-hub.

This module provides password hashing and verification using Argon2id,
as recommended in spec.md for secure password storage.
"""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password using Argon2id."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash."""
    try:
        _hasher.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False


# Login rate limiting constants
LOGIN_LOCKOUT_THRESHOLD = 5  # Start lockout after this many failures
LOGIN_LOCKOUT_BASE_SECONDS = 30  # Base lockout duration in seconds
LOGIN_LOCKOUT_MAX_SECONDS = 1800  # Maximum lockout duration (30 minutes)


def calculate_lockout_duration(failed_attempts: int) -> int:
    """Calculate lockout duration in seconds based on failed attempt count.

    Uses exponential backoff starting after LOGIN_LOCKOUT_THRESHOLD failures:
    - 5 failures: 30 seconds
    - 6 failures: 60 seconds
    - 7 failures: 120 seconds
    - 8 failures: 300 seconds (5 minutes)
    - 9 failures: 600 seconds (10 minutes)
    - 10+ failures: 1800 seconds (30 minutes, max)

    Args:
        failed_attempts: Number of consecutive failed login attempts

    Returns:
        Lockout duration in seconds (0 if below threshold)
    """
    if failed_attempts < LOGIN_LOCKOUT_THRESHOLD:
        return 0

    exponent = failed_attempts - LOGIN_LOCKOUT_THRESHOLD
    duration = LOGIN_LOCKOUT_BASE_SECONDS * (2**exponent)
    return int(min(duration, LOGIN_LOCKOUT_MAX_SECONDS))
