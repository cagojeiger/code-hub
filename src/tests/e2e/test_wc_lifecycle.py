"""Full workspace lifecycle E2E tests."""

from uuid import uuid4

import httpx
import pytest

from conftest import wait_for_phase


def test_workspace_full_lifecycle(api_client: httpx.Client):
    """Full lifecycle: Create -> RUNNING -> STANDBY -> ARCHIVED -> RUNNING -> DELETED."""
    workspace_id = None
    try:
        # 1. Create workspace
        resp = api_client.post(
            "/api/v1/workspaces", json={"name": f"e2e-test-{uuid4().hex[:8]}"}
        )
        assert resp.status_code == 201, f"Create failed: {resp.text}"
        workspace_id = resp.json()["id"]
        print(f"\nCreated workspace: {workspace_id}")

        # 2. Wait for RUNNING
        ws = wait_for_phase(api_client, workspace_id, "RUNNING", timeout=120)
        assert ws["phase"] == "RUNNING"
        assert ws["operation"] == "NONE"
        print("-> RUNNING")

        # 3. Request STANDBY
        resp = api_client.patch(
            f"/api/v1/workspaces/{workspace_id}", json={"desired_state": "STANDBY"}
        )
        assert resp.status_code == 200, f"STANDBY request failed: {resp.text}"

        # 4. Wait for STANDBY
        ws = wait_for_phase(api_client, workspace_id, "STANDBY", timeout=60)
        assert ws["phase"] == "STANDBY"
        assert ws["operation"] == "NONE"
        print("-> STANDBY")

        # 5. Request ARCHIVED
        resp = api_client.patch(
            f"/api/v1/workspaces/{workspace_id}", json={"desired_state": "ARCHIVED"}
        )
        assert resp.status_code == 200, f"ARCHIVED request failed: {resp.text}"

        # 6. Wait for ARCHIVED
        ws = wait_for_phase(api_client, workspace_id, "ARCHIVED", timeout=300)
        assert ws["phase"] == "ARCHIVED"
        assert ws["operation"] == "NONE"
        assert ws.get("archive_key") is not None, "archive_key should be set"
        print(f"-> ARCHIVED (archive_key: {ws['archive_key']})")

        # 7. Request RUNNING (restore)
        resp = api_client.patch(
            f"/api/v1/workspaces/{workspace_id}", json={"desired_state": "RUNNING"}
        )
        assert resp.status_code == 200, f"RUNNING (restore) request failed: {resp.text}"

        # 8. Wait for RUNNING (after restore)
        ws = wait_for_phase(api_client, workspace_id, "RUNNING", timeout=300)
        assert ws["phase"] == "RUNNING"
        assert ws["operation"] == "NONE"
        print("-> RUNNING (after restore)")

        # 9. Delete
        resp = api_client.delete(f"/api/v1/workspaces/{workspace_id}")
        assert resp.status_code in (200, 204), f"Delete failed: {resp.text}"

        # 10. Wait for DELETED
        ws = wait_for_phase(api_client, workspace_id, "DELETED", timeout=60)
        assert ws["phase"] == "DELETED"
        print("-> DELETED")
        workspace_id = None

    finally:
        if workspace_id:
            try:
                print(f"\nCleaning up workspace: {workspace_id}")
                api_client.delete(f"/api/v1/workspaces/{workspace_id}")
                wait_for_phase(api_client, workspace_id, "DELETED", timeout=60)
            except Exception as e:
                print(f"Cleanup failed: {e}")


def test_idempotent_desired_state_change(api_client: httpx.Client):
    """Same desired_state request twice should both succeed (idempotent)."""
    workspace_id = None
    try:
        # Create and wait RUNNING
        resp = api_client.post(
            "/api/v1/workspaces", json={"name": f"e2e-idempotent-{uuid4().hex[:8]}"}
        )
        assert resp.status_code == 201, f"Create failed: {resp.text}"
        workspace_id = resp.json()["id"]
        print(f"\nCreated workspace: {workspace_id}")

        wait_for_phase(api_client, workspace_id, "RUNNING", timeout=120)
        print("-> RUNNING")

        # Request STANDBY twice
        resp1 = api_client.patch(
            f"/api/v1/workspaces/{workspace_id}", json={"desired_state": "STANDBY"}
        )
        resp2 = api_client.patch(
            f"/api/v1/workspaces/{workspace_id}", json={"desired_state": "STANDBY"}
        )

        # Both should succeed (idempotent)
        assert resp1.status_code == 200, f"First STANDBY request failed: {resp1.text}"
        assert resp2.status_code == 200, f"Second STANDBY request failed: {resp2.text}"

        # Should still reach STANDBY
        ws = wait_for_phase(api_client, workspace_id, "STANDBY", timeout=60)
        assert ws["phase"] == "STANDBY"
        print("-> Idempotent STANDBY requests: PASSED")

    finally:
        if workspace_id:
            try:
                print(f"\nCleaning up workspace: {workspace_id}")
                api_client.delete(f"/api/v1/workspaces/{workspace_id}")
                wait_for_phase(api_client, workspace_id, "DELETED", timeout=60)
            except Exception as e:
                print(f"Cleanup failed: {e}")


def test_agent_409_container_running(api_client: httpx.Client):
    """Agent returns 409 when trying to archive a running container."""
    workspace_id = None
    try:
        # Create and wait RUNNING
        resp = api_client.post(
            "/api/v1/workspaces", json={"name": f"e2e-409-{uuid4().hex[:8]}"}
        )
        assert resp.status_code == 201, f"Create failed: {resp.text}"
        workspace_id = resp.json()["id"]
        print(f"\nCreated workspace: {workspace_id}")

        wait_for_phase(api_client, workspace_id, "RUNNING", timeout=120)
        print("-> RUNNING")

        # Call Agent directly (bypass Control Plane)
        with httpx.Client(
            base_url="http://localhost:18081", timeout=30.0
        ) as agent_client:
            resp = agent_client.post(
                f"/api/v1/workspaces/{workspace_id}/archive",
                json={"archive_op_id": "test-op-id"},
            )

        # Should get 409 CONTAINER_RUNNING
        assert (
            resp.status_code == 409
        ), f"Expected 409, got {resp.status_code}: {resp.text}"
        response_text = resp.text.lower()
        assert (
            "container" in response_text and "running" in response_text
        ), f"Expected 'CONTAINER_RUNNING' in response: {resp.text}"
        print(f"-> Agent 409 response: {resp.json()}")

    finally:
        if workspace_id:
            try:
                print(f"\nCleaning up workspace: {workspace_id}")
                api_client.delete(f"/api/v1/workspaces/{workspace_id}")
                wait_for_phase(api_client, workspace_id, "DELETED", timeout=60)
            except Exception as e:
                print(f"Cleanup failed: {e}")
