/**
 * CodeHub SSE Module
 * Server-Sent Events for real-time updates with polling fallback
 */

import { API, POLL_INTERVAL, state, STATUS_CONFIG } from './state.js';
import { showToast, updateFooterStats } from './utils.js';
import { renderFilteredWorkspaces } from './cards.js';
import { renderDetailPanel, closeDetailPanel } from './detail-panel.js';

/**
 * Update connection status indicator
 */
export function updateConnectionStatus(status) {
  const statusEl = document.getElementById('connection-status');
  const dot = statusEl.querySelector('span:first-child');
  const text = statusEl.querySelector('span:last-child');

  switch (status) {
    case 'connected':
      dot.className = 'w-2 h-2 rounded-full bg-vscode-success';
      text.className = 'text-vscode-success';
      text.textContent = 'Live';
      break;
    case 'connecting':
      dot.className = 'w-2 h-2 rounded-full bg-vscode-warning';
      text.className = 'text-vscode-warning';
      text.textContent = 'Connecting...';
      break;
    case 'disconnected':
      dot.className = 'w-2 h-2 rounded-full bg-vscode-error';
      text.className = 'text-vscode-error';
      text.textContent = 'Reconnecting...';
      break;
    case 'polling':
      dot.className = 'w-2 h-2 rounded-full bg-vscode-accent';
      text.className = 'text-vscode-accent';
      text.textContent = 'Polling';
      break;
  }
}

/**
 * Handle workspace update event
 */
export function handleWorkspaceUpdate(workspace) {
  // Update cache
  state.cache[workspace.id] = workspace;

  // Update workspaces array
  const index = state.workspaces.findIndex(ws => ws.id === workspace.id);
  if (index >= 0) {
    state.workspaces[index] = workspace;
  } else {
    state.workspaces.unshift(workspace);
  }

  // Re-render
  renderFilteredWorkspaces();
  updateFooterStats(state.workspaces);

  // Update detail panel if this workspace is selected
  if (state.selectedWorkspaceId === workspace.id) {
    renderDetailPanel(workspace);
  }

  // Show toast for status changes
  const config = STATUS_CONFIG[workspace.status];
  if (config && !config.isTransition) {
    showToast(`${workspace.name}: ${config.label}`, workspace.status === 'ERROR' ? 'error' : 'info');
  }
}

/**
 * Handle workspace deleted event
 */
export function handleWorkspaceDeleted(id) {
  // Remove from cache
  delete state.cache[id];

  // Remove from workspaces
  state.workspaces = state.workspaces.filter(ws => ws.id !== id);

  // Re-render
  renderFilteredWorkspaces();
  updateFooterStats(state.workspaces);

  // Close detail panel if this workspace was selected
  if (state.selectedWorkspaceId === id) {
    closeDetailPanel();
  }
}

/**
 * Start polling fallback
 */
export function startPolling(loadWorkspacesCallback) {
  if (state.pollTimer) return; // Already polling
  state.pollTimer = setInterval(() => loadWorkspacesCallback(state.currentPage), POLL_INTERVAL);
}

/**
 * Stop polling
 */
export function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

/**
 * Connect to SSE endpoint
 */
export function connectSSE(loadWorkspacesCallback) {
  if (state.eventSource) {
    state.eventSource.close();
  }

  updateConnectionStatus('connecting');

  try {
    state.eventSource = new EventSource(`${API}/events`);

    state.eventSource.onopen = () => {
      updateConnectionStatus('connected');
      // Stop polling when SSE is connected
      stopPolling();
    };

    state.eventSource.addEventListener('workspace_updated', (event) => {
      const data = JSON.parse(event.data);
      handleWorkspaceUpdate(data);
    });

    state.eventSource.addEventListener('workspace_deleted', (event) => {
      const data = JSON.parse(event.data);
      handleWorkspaceDeleted(data.id);
    });

    state.eventSource.addEventListener('heartbeat', () => {
      // Keep-alive, no action needed
    });

    state.eventSource.onerror = () => {
      updateConnectionStatus('disconnected');
      // Start polling as fallback
      startPolling(loadWorkspacesCallback);
    };
  } catch (e) {
    // SSE not supported or failed, use polling
    console.warn('SSE not available, falling back to polling');
    updateConnectionStatus('polling');
    startPolling(loadWorkspacesCallback);
  }
}

/**
 * Setup visibility change handler for SSE/polling
 */
export function setupVisibilityHandler(loadWorkspacesCallback) {
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopPolling();
      if (state.eventSource) {
        state.eventSource.close();
        state.eventSource = null;
      }
    } else {
      loadWorkspacesCallback(state.currentPage);
      connectSSE(loadWorkspacesCallback);
    }
  });
}
