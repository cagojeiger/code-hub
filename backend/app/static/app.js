const API = '/api/v1';
const POLL_INTERVAL = 5000;

let pollTimer = null;

// Status configuration
const STATUS_CONFIG = {
  CREATED: { color: 'text-vscode-text', icon: '○', label: 'Created', canStart: true, canDelete: true },
  PROVISIONING: { color: 'text-vscode-warning', icon: '◐', label: 'Starting...', isTransition: true },
  RUNNING: { color: 'text-vscode-success', icon: '●', label: 'Running', canStop: true, canDelete: true, canOpen: true },
  STOPPING: { color: 'text-vscode-warning', icon: '◐', label: 'Stopping...', isTransition: true },
  STOPPED: { color: 'text-vscode-text', icon: '○', label: 'Stopped', canStart: true, canDelete: true },
  DELETING: { color: 'text-vscode-warning', icon: '◐', label: 'Deleting...', isTransition: true },
  ERROR: { color: 'text-vscode-error', icon: '✕', label: 'Error', canStart: true, canDelete: true },
};

// API Functions
async function fetchWorkspaces() {
  const response = await fetch(`${API}/workspaces`);
  if (!response.ok) throw new Error('Failed to fetch workspaces');
  return response.json();
}

async function createWorkspace(name, description) {
  const response = await fetch(`${API}/workspaces`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description: description || null }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to create workspace');
  }
  return response.json();
}

async function startWorkspace(id) {
  const response = await fetch(`${API}/workspaces/${id}:start`, { method: 'POST' });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to start workspace');
  }
  return response.json();
}

async function stopWorkspace(id) {
  const response = await fetch(`${API}/workspaces/${id}:stop`, { method: 'POST' });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to stop workspace');
  }
  return response.json();
}

async function deleteWorkspace(id) {
  const response = await fetch(`${API}/workspaces/${id}`, { method: 'DELETE' });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to delete workspace');
  }
}

// UI Functions
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');

  const bgColor = type === 'error' ? 'bg-vscode-error' :
                  type === 'success' ? 'bg-vscode-success' : 'bg-vscode-accent';

  toast.className = `${bgColor} text-white px-4 py-2 rounded shadow-lg transform transition-all duration-300`;
  toast.textContent = message;

  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('opacity-0', 'translate-x-full');
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

function renderWorkspaceCard(workspace) {
  const config = STATUS_CONFIG[workspace.status] || STATUS_CONFIG.ERROR;

  const buttons = [];

  if (config.canStart) {
    buttons.push(`
      <button onclick="handleStart('${workspace.id}')"
              class="px-3 py-1 bg-vscode-accent hover:bg-blue-600 text-white text-sm rounded transition-colors">
        Start
      </button>
    `);
  }

  if (config.canStop) {
    buttons.push(`
      <button onclick="handleStop('${workspace.id}')"
              class="px-3 py-1 bg-vscode-hover border border-vscode-border hover:border-vscode-text text-white text-sm rounded transition-colors">
        Stop
      </button>
    `);
  }

  if (config.canDelete) {
    buttons.push(`
      <button onclick="handleDelete('${workspace.id}', '${workspace.name}')"
              class="px-3 py-1 bg-vscode-hover border border-vscode-border hover:border-vscode-error hover:text-vscode-error text-sm rounded transition-colors">
        Delete
      </button>
    `);
  }

  if (config.canOpen) {
    buttons.push(`
      <a href="${workspace.url}" target="_blank"
         class="px-3 py-1 bg-vscode-success hover:bg-green-600 text-white text-sm rounded transition-colors inline-flex items-center gap-1">
        Open
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path>
        </svg>
      </a>
    `);
  }

  const spinnerHtml = config.isTransition ?
    '<span class="inline-block w-4 h-4 border-2 border-vscode-warning border-t-transparent rounded-full spinner ml-2"></span>' : '';

  return `
    <div class="bg-vscode-sidebar border border-vscode-border rounded-lg p-4 hover:border-vscode-hover transition-colors">
      <div class="flex justify-between items-start mb-2">
        <h3 class="text-white font-medium">${escapeHtml(workspace.name)}</h3>
        <span class="${config.color} text-sm flex items-center">
          <span class="mr-1">${config.icon}</span>
          ${config.label}
          ${spinnerHtml}
        </span>
      </div>
      ${workspace.description ? `<p class="text-sm text-vscode-text mb-3">${escapeHtml(workspace.description)}</p>` : ''}
      ${workspace.status === 'ERROR' ? `<p class="text-sm text-vscode-error mb-3">Error occurred during operation</p>` : ''}
      <div class="flex gap-2 flex-wrap">
        ${buttons.join('')}
      </div>
    </div>
  `;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

async function loadAndRenderWorkspaces() {
  const listEl = document.getElementById('workspace-list');
  const emptyEl = document.getElementById('empty-state');
  const loadingEl = document.getElementById('loading-state');

  try {
    const workspaces = await fetchWorkspaces();

    loadingEl.classList.add('hidden');

    if (workspaces.length === 0) {
      listEl.innerHTML = '';
      emptyEl.classList.remove('hidden');
    } else {
      emptyEl.classList.add('hidden');
      listEl.innerHTML = workspaces.map(renderWorkspaceCard).join('');
    }
  } catch (error) {
    loadingEl.classList.add('hidden');
    showToast(error.message, 'error');
  }
}

// Event Handlers
async function handleStart(id) {
  try {
    await startWorkspace(id);
    showToast('Workspace starting...', 'info');
    await loadAndRenderWorkspaces();
  } catch (error) {
    showToast(error.message, 'error');
  }
}

async function handleStop(id) {
  try {
    await stopWorkspace(id);
    showToast('Workspace stopping...', 'info');
    await loadAndRenderWorkspaces();
  } catch (error) {
    showToast(error.message, 'error');
  }
}

async function handleDelete(id, name) {
  if (!confirm(`Are you sure you want to delete "${name}"?`)) return;

  try {
    await deleteWorkspace(id);
    showToast('Workspace deleted', 'success');
    await loadAndRenderWorkspaces();
  } catch (error) {
    showToast(error.message, 'error');
  }
}

// Modal Functions
function openCreateModal() {
  document.getElementById('create-modal').classList.remove('hidden');
  document.getElementById('workspace-name').focus();
}

function closeCreateModal() {
  document.getElementById('create-modal').classList.add('hidden');
  document.getElementById('create-form').reset();
}

async function handleCreateSubmit(e) {
  e.preventDefault();

  const name = document.getElementById('workspace-name').value.trim();
  const description = document.getElementById('workspace-description').value.trim();

  try {
    await createWorkspace(name, description);
    showToast('Workspace created', 'success');
    closeCreateModal();
    await loadAndRenderWorkspaces();
  } catch (error) {
    showToast(error.message, 'error');
  }
}

// Polling
function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(loadAndRenderWorkspaces, POLL_INTERVAL);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  // Load initial data
  loadAndRenderWorkspaces();

  // Start polling
  startPolling();

  // Modal event listeners
  document.getElementById('new-workspace-btn').addEventListener('click', openCreateModal);
  document.getElementById('close-modal-btn').addEventListener('click', closeCreateModal);
  document.getElementById('cancel-create-btn').addEventListener('click', closeCreateModal);
  document.getElementById('create-form').addEventListener('submit', handleCreateSubmit);

  // Close modal on backdrop click
  document.getElementById('create-modal').addEventListener('click', (e) => {
    if (e.target.id === 'create-modal') closeCreateModal();
  });

  // Close modal on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeCreateModal();
  });

  // Pause polling when tab is hidden
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopPolling();
    } else {
      loadAndRenderWorkspaces();
      startPolling();
    }
  });
});
