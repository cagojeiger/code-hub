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
