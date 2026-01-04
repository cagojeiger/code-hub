"""Proxy status pages for non-RUNNING workspace states.

Redirects to static HTML pages with URL parameters:
- starting_page: /static/proxy/starting.html?id=...&name=...
- archived_page: /static/proxy/archived.html?name=...
- limit_exceeded_page: /static/proxy/limit.html?max=...&workspaces=...
- error_page: /static/proxy/error.html?phase=...&name=...&error=...
"""

from urllib.parse import quote

from fastapi.responses import RedirectResponse

from codehub.core.models import Workspace


def starting_page(workspace: Workspace) -> RedirectResponse:
    """Redirect to starting page for STANDBY workspace (auto-wake triggered)."""
    params = f"id={workspace.id}&name={quote(workspace.name)}"
    return RedirectResponse(
        url=f"/static/proxy/starting.html?{params}",
        status_code=302,
    )


def archived_page(workspace: Workspace) -> RedirectResponse:
    """Redirect to archived page for ARCHIVED workspace."""
    params = f"name={quote(workspace.name)}"
    # Note: 302 redirect, but the page itself will show 502-like content
    return RedirectResponse(
        url=f"/static/proxy/archived.html?{params}",
        status_code=302,
    )


def limit_exceeded_page(
    running_workspaces: list[Workspace],
    max_running: int,
) -> RedirectResponse:
    """Redirect to limit exceeded page."""
    # Format: id1:name1,id2:name2
    workspaces_param = ",".join(
        f"{ws.id}:{quote(ws.name)}" for ws in running_workspaces
    )
    params = f"max={max_running}&workspaces={workspaces_param}"
    return RedirectResponse(
        url=f"/static/proxy/limit.html?{params}",
        status_code=302,
    )


def error_page(workspace: Workspace) -> RedirectResponse:
    """Redirect to error page for PENDING/ERROR/etc states."""
    error_reason = workspace.error_reason or ""
    params = f"phase={workspace.phase}&name={quote(workspace.name)}"
    if error_reason:
        params += f"&error={quote(error_reason)}"
    return RedirectResponse(
        url=f"/static/proxy/error.html?{params}",
        status_code=302,
    )
