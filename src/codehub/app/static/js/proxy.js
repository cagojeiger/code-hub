/**
 * Proxy status page utilities
 * Uses polling to check workspace state
 */

const ProxyPage = {
  /**
   * Poll workspace status and redirect when RUNNING
   * @param {string} workspaceId - Workspace ID to monitor
   * @param {string} statusElementId - Element ID to update status text
   * @param {number} intervalMs - Polling interval in milliseconds (default: 2000)
   */
  pollStatus(workspaceId, statusElementId, intervalMs = 2000) {
    const statusEl = document.getElementById(statusElementId);
    if (!statusEl) return;

    statusEl.textContent = 'Waiting for workspace to start...';

    const poll = async () => {
      try {
        const res = await fetch(`/api/v1/workspaces/${workspaceId}`, {
          credentials: 'include'
        });

        if (!res.ok) {
          statusEl.textContent = 'Error checking status. Retrying...';
          setTimeout(poll, intervalMs);
          return;
        }

        const ws = await res.json();

        if (ws.phase === 'RUNNING') {
          statusEl.textContent = 'Workspace ready! Redirecting...';
          setTimeout(() => {
            window.location.href = `/w/${workspaceId}/`;
          }, 500);
        } else if (ws.phase === 'ERROR') {
          statusEl.textContent = `Error: ${ws.error_reason || 'Unknown error'}`;
        } else {
          // Still starting, continue polling
          setTimeout(poll, intervalMs);
        }
      } catch (err) {
        console.error('Poll error:', err);
        statusEl.textContent = 'Connection error. Retrying...';
        setTimeout(poll, intervalMs);
      }
    };

    // Start polling
    poll();
  }
};
