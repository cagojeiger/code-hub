"""Database seeding for development and testing.

This module provides functions to seed the database with test data.
In MVP, this creates a test user for development purposes.
"""

import logging

from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

logger = logging.getLogger(__name__)

# Password hasher using Argon2id (spec.md recommends bcrypt/argon2id)
_hasher = PasswordHasher()

# Default test user credentials (MVP only)
TEST_USER_USERNAME = "testuser"
TEST_USER_PASSWORD = "testpassword"


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
    except Exception:
        return False


async def seed_test_user(session: AsyncSession) -> User | None:
    """Create test user if it doesn't exist.

    This is used for MVP development. The test user has:
    - username: testuser
    - password: testpassword

    Args:
        session: Async database session

    Returns:
        Created or existing User, or None if creation failed
    """
    # Check if test user already exists
    result = await session.execute(
        select(User).where(User.username == TEST_USER_USERNAME)
    )
    existing_user = result.scalar_one_or_none()

    if existing_user:
        logger.info("Test user already exists: %s", TEST_USER_USERNAME)
        return existing_user

    # Create test user
    test_user = User(
        username=TEST_USER_USERNAME,
        password_hash=hash_password(TEST_USER_PASSWORD),
    )
    session.add(test_user)
    await session.commit()
    await session.refresh(test_user)

    logger.info("Test user created: %s (id=%s)", TEST_USER_USERNAME, test_user.id)
    return test_user


async def seed_database(session: AsyncSession) -> None:
    """Run all seed operations.

    Args:
        session: Async database session
    """
    await seed_test_user(session)
    logger.info("Database seeding completed")
