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

    # Get session with cookie
    session_cookie = login_response.cookies.get("session")
    response = await async_client.get(
        "/api/v1/session",
        cookies={"session": session_cookie},
    )

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
    response = await async_client.get(
        "/api/v1/session",
        cookies={"session": "invalid-session-id"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_authenticated(async_client):
    # Login first
    login_response = await async_client.post(
        "/api/v1/login",
        json={"username": "admin", "password": "admin"},
    )
    session_cookie = login_response.cookies.get("session")

    # Logout
    response = await async_client.post(
        "/api/v1/logout",
        cookies={"session": session_cookie},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Logged out"

    # Session should be revoked - trying to use it should fail
    session_response = await async_client.get(
        "/api/v1/session",
        cookies={"session": session_cookie},
    )
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

    # Access workspace API
    response = await async_client.get(
        "/api/v1/workspaces",
        cookies={"session": session_cookie},
    )

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "pagination" in data
