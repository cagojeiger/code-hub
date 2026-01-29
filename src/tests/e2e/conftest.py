"""E2E test configuration for Playwright."""

import asyncio
import time
from typing import AsyncGenerator

import httpx
import pytest
import requests


@pytest.fixture(scope="session", autouse=True)
def check_infrastructure():
    """Verify docker-compose services are healthy before running E2E tests."""
    services = [
        ("Control Plane", "http://localhost:18000/health"),
        ("Agent", "http://localhost:18081/health"),
    ]
    for name, url in services:
        try:
            resp = requests.get(url, timeout=5)
            if not resp.ok:
                pytest.skip(f"{name} not healthy at {url}. Please run: docker-compose up -d")
        except requests.RequestException as e:
            pytest.skip(f"{name} not available at {url}. Error: {e}. Please run: docker-compose up -d")


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Configure browser context with sensible defaults."""
    return {
        **browser_context_args,
        "viewport": {"width": 1920, "height": 1080},
        "locale": "en-US",
    }


BASE_URL = "http://localhost:18000"


@pytest.fixture
def api_client():
    """Authenticated API client for E2E tests."""
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        resp = client.post("/api/v1/login", json={
            "username": "admin",
            "password": "qwer1234"
        })
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        yield client


def wait_for_phase(
    client: httpx.Client,
    workspace_id: str,
    target_phase: str,
    timeout: float = 60.0,
    poll_interval: float = 2.0
) -> dict:
    """Poll workspace state until target phase is reached."""
    start = time.time()
    while time.time() - start < timeout:
        resp = client.get(f"/api/v1/workspaces/{workspace_id}")
        if resp.status_code == 404:
            if target_phase == "DELETED":
                return {"phase": "DELETED"}
            raise ValueError(f"Workspace {workspace_id} not found")
        
        data = resp.json()
        if data["phase"] == target_phase and data["operation"] == "NONE":
            return data
        
        time.sleep(poll_interval)
    
    raise TimeoutError(
        f"Workspace {workspace_id} did not reach {target_phase} in {timeout}s"
    )
