"""Security utilities for code-hub.

This module provides password hashing and verification using Argon2id,
as recommended in spec.md for secure password storage.
"""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password using Argon2id.

    Args:
        password: Plain text password

    Returns:
        Argon2id hashed password string
    """
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash.

    Args:
        password: Plain text password to verify
        password_hash: Argon2id hashed password

    Returns:
        True if password matches, False otherwise
    """
    try:
        _hasher.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False
