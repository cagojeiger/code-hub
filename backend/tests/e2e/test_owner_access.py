"""E2E tests for MVP Criteria 1 & 2: Owner access control.

Criteria 1: Owner can access /w/{workspace_id}/ successfully
Criteria 2: Non-owner accessing /w/{workspace_id}/ gets 403
"""

import pytest
from httpx import AsyncClient


@pytest.mark.e2e
class TestOwnerAccess:
    """MVP Criteria 1: Owner can access /w/{workspace_id}/ successfully."""

    @pytest.mark.asyncio
    async def test_owner_can_access_running_workspace(
        self,
        e2e_client: AsyncClient,
        running_workspace: dict,
    ):
        """Verify owner gets 200 when accessing their running workspace."""
        workspace_id = running_workspace["id"]

        # code-server may redirect (302/307) before returning content
        response = await e2e_client.get(f"/w/{workspace_id}/", follow_redirects=True)

        assert response.status_code == 200
        # code-server returns HTML content
        assert "text/html" in response.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_owner_access_returns_code_server_content(
        self,
        e2e_client: AsyncClient,
        running_workspace: dict,
    ):
        """Verify response contains code-server specific content."""
        workspace_id = running_workspace["id"]

        # code-server may redirect (302/307) before returning content
        response = await e2e_client.get(f"/w/{workspace_id}/", follow_redirects=True)

        assert response.status_code == 200
        content = response.text
        # code-server pages typically contain these identifiers
        assert "code-server" in content.lower() or "vscode" in content.lower()

    @pytest.mark.asyncio
    async def test_trailing_slash_redirect(
        self,
        e2e_client: AsyncClient,
        running_workspace: dict,
    ):
        """Verify /w/{id} redirects to /w/{id}/ with 308."""
        workspace_id = running_workspace["id"]

        # Don't follow redirects
        response = await e2e_client.get(
            f"/w/{workspace_id}",
            follow_redirects=False,
        )

        assert response.status_code == 308
        assert response.headers.get("location") == f"/w/{workspace_id}/"


@pytest.mark.e2e
class TestNonOwnerAccess:
    """MVP Criteria 2: Non-owner accessing /w/{workspace_id}/ gets 403."""

    @pytest.mark.asyncio
    async def test_non_owner_gets_403(
        self,
        second_user_client: AsyncClient,
        running_workspace: dict,
    ):
        """Verify non-owner receives 403 Forbidden."""
        workspace_id = running_workspace["id"]

        response = await second_user_client.get(f"/w/{workspace_id}/")

        assert response.status_code == 403
        data = response.json()
        assert data["error"]["code"] == "FORBIDDEN"

    @pytest.mark.asyncio
    async def test_unauthenticated_gets_401(
        self,
        unauthenticated_client: AsyncClient,
        running_workspace: dict,
    ):
        """Verify unauthenticated user receives 401 Unauthorized."""
        workspace_id = running_workspace["id"]

        response = await unauthenticated_client.get(f"/w/{workspace_id}/")

        assert response.status_code == 401
        data = response.json()
        assert data["error"]["code"] == "UNAUTHORIZED"
