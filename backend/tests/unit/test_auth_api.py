"""Tests for Auth API endpoints."""

import pytest


@pytest.mark.asyncio
async def test_login_success(async_client):
    response = await async_client.post(
        "/api/v1/login",
        json={"username": "admin", "password": "admin"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "admin"
    assert "user_id" in data

    # Check session cookie is set
    assert "session" in response.cookies


@pytest.mark.asyncio
async def test_login_invalid_username(async_client):
    response = await async_client.post(
        "/api/v1/login",
        json={"username": "nonexistent", "password": "admin"},
    )

    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_login_invalid_password(async_client):
    response = await async_client.post(
        "/api/v1/login",
        json={"username": "admin", "password": "wrongpassword"},
    )

    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_get_session_authenticated(async_client):
    # Login first
    login_response = await async_client.post(
        "/api/v1/login",
        json={"username": "admin", "password": "admin"},
    )
    assert login_response.status_code == 200

    # Set session cookie on client
    session_cookie = login_response.cookies.get("session")
    async_client.cookies.set("session", session_cookie)

    # Get session with cookie
    response = await async_client.get("/api/v1/session")

    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "admin"


@pytest.mark.asyncio
async def test_get_session_unauthenticated(unauthenticated_client):
    response = await unauthenticated_client.get("/api/v1/session")

    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_get_session_invalid_cookie(async_client):
    # Set invalid session cookie on client
    async_client.cookies.set("session", "invalid-session-id")

    response = await async_client.get("/api/v1/session")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_authenticated(async_client):
    # Login first
    login_response = await async_client.post(
        "/api/v1/login",
        json={"username": "admin", "password": "admin"},
    )
    session_cookie = login_response.cookies.get("session")

    # Set session cookie on client
    async_client.cookies.set("session", session_cookie)

    # Logout
    response = await async_client.post("/api/v1/logout")

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Logged out"

    # Session should be revoked - trying to use it should fail
    session_response = await async_client.get("/api/v1/session")
    assert session_response.status_code == 401


@pytest.mark.asyncio
async def test_logout_unauthenticated(async_client):
    response = await async_client.post("/api/v1/logout")

    # Should succeed (idempotent)
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Logged out"


@pytest.mark.asyncio
async def test_workspace_api_requires_auth(unauthenticated_client):
    response = await unauthenticated_client.get("/api/v1/workspaces")

    assert response.status_code == 401
    data = response.json()
    assert data["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_workspace_api_with_auth(async_client):
    # Login first
    login_response = await async_client.post(
        "/api/v1/login",
        json={"username": "admin", "password": "admin"},
    )
    session_cookie = login_response.cookies.get("session")

    # Set session cookie on client
    async_client.cookies.set("session", session_cookie)

    # Access workspace API
    response = await async_client.get("/api/v1/workspaces")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "pagination" in data


# Login Rate Limiting Tests


@pytest.mark.asyncio
async def test_login_rate_limit_not_triggered_below_threshold(async_client):
    """Test that rate limiting is not triggered for fewer than 5 failed attempts."""
    # 4 failed attempts should not trigger rate limiting
    for _ in range(4):
        response = await async_client.post(
            "/api/v1/login",
            json={"username": "admin", "password": "wrongpassword"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    # 5th attempt should still work (before lockout is applied on this attempt)
    response = await async_client.post(
        "/api/v1/login",
        json={"username": "admin", "password": "admin"},
    )
    # Should succeed - lockout is set after failed attempt, not before
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_login_rate_limit_triggered_at_threshold(async_client):
    """Test that rate limiting is triggered after 5 failed attempts."""
    # 5 failed attempts to trigger lockout
    for _ in range(5):
        response = await async_client.post(
            "/api/v1/login",
            json={"username": "admin", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    # 6th attempt should be blocked
    response = await async_client.post(
        "/api/v1/login",
        json={"username": "admin", "password": "admin"},
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "TOO_MANY_REQUESTS"
    assert "Retry-After" in response.headers


@pytest.mark.asyncio
async def test_login_rate_limit_retry_after_header(async_client):
    """Test that Retry-After header is present and has correct format."""
    # Trigger lockout
    for _ in range(5):
        await async_client.post(
            "/api/v1/login",
            json={"username": "admin", "password": "wrongpassword"},
        )

    # Check Retry-After header
    response = await async_client.post(
        "/api/v1/login",
        json={"username": "admin", "password": "admin"},
    )
    assert response.status_code == 429
    retry_after = response.headers.get("Retry-After")
    assert retry_after is not None
    assert int(retry_after) > 0


@pytest.mark.asyncio
async def test_login_success_resets_rate_limit(async_client):
    """Test that successful login resets the failed attempt counter."""
    # 4 failed attempts (below threshold)
    for _ in range(4):
        await async_client.post(
            "/api/v1/login",
            json={"username": "admin", "password": "wrongpassword"},
        )

    # Successful login
    response = await async_client.post(
        "/api/v1/login",
        json={"username": "admin", "password": "admin"},
    )
    assert response.status_code == 200

    # 4 more failed attempts should not trigger lockout (counter was reset)
    for _ in range(4):
        response = await async_client.post(
            "/api/v1/login",
            json={"username": "admin", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    # Should still be able to login
    response = await async_client.post(
        "/api/v1/login",
        json={"username": "admin", "password": "admin"},
    )
    assert response.status_code == 200
