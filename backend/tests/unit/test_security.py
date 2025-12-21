"""Unit tests for security module.

Tests cover:
- Password hashing with Argon2id
- Password verification
- Login rate limiting (lockout duration calculation)
"""

from app.core.security import (
    LOGIN_LOCKOUT_BASE_SECONDS,
    LOGIN_LOCKOUT_MAX_SECONDS,
    LOGIN_LOCKOUT_THRESHOLD,
    calculate_lockout_duration,
    hash_password,
    verify_password,
)


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


class TestLockoutDuration:
    """Tests for login rate limiting lockout duration calculation."""

    def test_no_lockout_below_threshold(self):
        """Test no lockout when failures are below threshold."""
        for attempts in range(LOGIN_LOCKOUT_THRESHOLD):
            assert calculate_lockout_duration(attempts) == 0

    def test_lockout_starts_at_threshold(self):
        """Test lockout starts exactly at threshold."""
        assert calculate_lockout_duration(LOGIN_LOCKOUT_THRESHOLD) == LOGIN_LOCKOUT_BASE_SECONDS

    def test_exponential_backoff(self):
        """Test lockout duration doubles with each additional failure."""
        # 5 failures: 30s
        assert calculate_lockout_duration(5) == 30
        # 6 failures: 60s
        assert calculate_lockout_duration(6) == 60
        # 7 failures: 120s
        assert calculate_lockout_duration(7) == 120
        # 8 failures: 240s
        assert calculate_lockout_duration(8) == 240
        # 9 failures: 480s
        assert calculate_lockout_duration(9) == 480
        # 10 failures: 960s
        assert calculate_lockout_duration(10) == 960

    def test_max_lockout_duration(self):
        """Test lockout duration is capped at maximum."""
        # Very high attempt count should be capped
        assert calculate_lockout_duration(20) == LOGIN_LOCKOUT_MAX_SECONDS
        assert calculate_lockout_duration(100) == LOGIN_LOCKOUT_MAX_SECONDS

    def test_lockout_constants(self):
        """Test lockout constants have expected values."""
        assert LOGIN_LOCKOUT_THRESHOLD == 5
        assert LOGIN_LOCKOUT_BASE_SECONDS == 30
        assert LOGIN_LOCKOUT_MAX_SECONDS == 1800  # 30 minutes
