"""Unit tests for security module.

Tests cover:
- Password hashing with Argon2id
- Password verification
"""

from app.core.security import hash_password, verify_password


class TestPasswordHashing:
    """Tests for password hashing utilities."""

    def test_hash_password(self):
        """Test password hashing."""
        password = "mypassword123"
        hashed = hash_password(password)

        assert hashed != password
        assert hashed.startswith("$argon2")

    def test_verify_password_correct(self):
        """Test verifying correct password."""
        password = "correctpassword"
        hashed = hash_password(password)

        assert verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        """Test verifying incorrect password."""
        password = "correctpassword"
        hashed = hash_password(password)

        assert verify_password("wrongpassword", hashed) is False

    def test_hash_password_different_each_time(self):
        """Test that hashing same password produces different hashes (salted)."""
        password = "samepassword"
        hash1 = hash_password(password)
        hash2 = hash_password(password)

        assert hash1 != hash2
