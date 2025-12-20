/**
 * CodeHub Dashboard Application
 * Master-Detail layout with session authentication and pagination
 */

const API = '/api/v1';
const POLL_INTERVAL = 5000;
const PER_PAGE = 20;

let pollTimer = null;
let currentPage = 1;
let selectedWorkspaceId = null;
let workspacesCache = {};

// Status configuration
const STATUS_CONFIG = {
  CREATED: { color: 'text-vscode-text', bgColor: 'bg-gray-600', icon: '○', label: 'Created', canStart: true, canDelete: true },
  PROVISIONING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Starting...', isTransition: true },
  RUNNING: { color: 'text-vscode-success', bgColor: 'bg-green-600', icon: '●', label: 'Running', canStop: true, canOpen: true },
  STOPPING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Stopping...', isTransition: true },
  STOPPED: { color: 'text-vscode-text', bgColor: 'bg-gray-600', icon: '○', label: 'Stopped', canStart: true, canDelete: true },
  DELETING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Deleting...', isTransition: true },
  ERROR: { color: 'text-vscode-error', bgColor: 'bg-red-600', icon: '✕', label: 'Error', canStart: true, canStop: true, canDelete: true },
};

// =============================================================================
// Auth Functions
// =============================================================================

async function checkSession() {
  try {
    const response = await fetch(`${API}/session`);
    if (response.ok) {
      const data = await response.json();
      document.getElementById('username-display').textContent = data.username;
      return true;
    }
    return false;
  } catch (error) {
    return false;
  }
}

async function logout() {
  try {
    await fetch(`${API}/logout`, { method: 'POST' });
  } catch (error) {
    console.error('Logout error:', error);
  }
  window.location.href = '/login';
}

function redirectToLogin() {
  window.location.href = '/login';
}

// =============================================================================
// API Functions
// =============================================================================

async function fetchWithAuth(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    redirectToLogin();
    throw new Error('Session expired');
  }
  return response;
}

async function fetchWorkspaces(page = 1) {
  const response = await fetchWithAuth(`${API}/workspaces?page=${page}&per_page=${PER_PAGE}`);
  if (!response.ok) throw new Error('Failed to fetch workspaces');
  return response.json();
}

async function fetchWorkspace(id) {
  const response = await fetchWithAuth(`${API}/workspaces/${id}`);
  if (!response.ok) throw new Error('Failed to fetch workspace');
  return response.json();
}

async function createWorkspace(name, description, memo) {
  const response = await fetchWithAuth(`${API}/workspaces`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name,
      description: description || null,
      memo: memo || null,
    }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to create workspace');
  }
  return response.json();
}

async function updateWorkspace(id, data) {
  const response = await fetchWithAuth(`${API}/workspaces/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to update workspace');
  }
  return response.json();
}

async function startWorkspace(id) {
  const response = await fetchWithAuth(`${API}/workspaces/${id}:start`, { method: 'POST' });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to start workspace');
  }
  return response.json();
}

async function stopWorkspace(id) {
  const response = await fetchWithAuth(`${API}/workspaces/${id}:stop`, { method: 'POST' });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to stop workspace');
  }
  return response.json();
}

async function deleteWorkspace(id) {
  const response = await fetchWithAuth(`${API}/workspaces/${id}`, { method: 'DELETE' });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to delete workspace');
  }
}

// =============================================================================
// UI Utility Functions
// =============================================================================

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

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function formatDate(dateString) {
  const date = new Date(dateString);
  return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// =============================================================================
// Sidebar Rendering
// =============================================================================

function renderSidebarItem(workspace, isSelected) {
  const config = STATUS_CONFIG[workspace.status] || STATUS_CONFIG.ERROR;
  const selectedClass = isSelected ? 'bg-vscode-hover border-l-2 border-vscode-accent' : 'border-l-2 border-transparent hover:bg-vscode-hover';

  const spinnerHtml = config.isTransition
    ? '<span class="inline-block w-3 h-3 border-2 border-vscode-warning border-t-transparent rounded-full spinner"></span>'
    : '';

  // Build action buttons based on status
  let buttonsHtml = '<div class="flex gap-1 mt-2">';

  if (config.canOpen) {
    buttonsHtml += `
      <button onclick="event.stopPropagation(); openWorkspace('${workspace.id}')"
              class="px-2 py-1 bg-vscode-success hover:bg-green-600 text-white text-xs rounded transition-colors">
        Open
      </button>`;
  }

  if (config.canStart) {
    buttonsHtml += `
      <button onclick="event.stopPropagation(); handleStart('${workspace.id}')"
              class="px-2 py-1 bg-vscode-accent hover:bg-blue-600 text-white text-xs rounded transition-colors">
        Start
      </button>`;
  }

  if (config.canStop) {
    buttonsHtml += `
      <button onclick="event.stopPropagation(); handleStop('${workspace.id}')"
              class="px-2 py-1 bg-vscode-hover border border-vscode-border text-white text-xs rounded transition-colors">
        Stop
      </button>`;
  }

  if (config.canDelete) {
    buttonsHtml += `
      <button onclick="event.stopPropagation(); handleDelete('${workspace.id}', '${escapeHtml(workspace.name)}')"
              class="px-2 py-1 bg-vscode-hover border border-vscode-border hover:border-vscode-error hover:text-vscode-error text-xs rounded transition-colors">
        Del
      </button>`;
  }

  buttonsHtml += '</div>';

  return `
    <div onclick="selectWorkspace('${workspace.id}')"
         class="px-4 py-3 cursor-pointer ${selectedClass} transition-colors">
      <div class="flex items-center justify-between">
        <span class="text-white text-sm font-medium truncate">${escapeHtml(workspace.name)}</span>
        <span class="${config.color} text-xs flex items-center gap-1">
          ${config.icon} ${spinnerHtml}
        </span>
      </div>
      ${buttonsHtml}
    </div>
  `;
}

function renderSidebar(workspaces) {
  const listEl = document.getElementById('workspace-list');
  listEl.innerHTML = workspaces.map(ws => renderSidebarItem(ws, ws.id === selectedWorkspaceId)).join('');
}

function renderPagination(pagination) {
  const paginationEl = document.getElementById('pagination');

  if (pagination.total_pages <= 1) {
    paginationEl.classList.add('hidden');
    return;
  }

  paginationEl.classList.remove('hidden');

  let html = '<div class="flex items-center justify-between">';

  // Previous button
  html += `
    <button onclick="goToPage(${pagination.page - 1})"
            ${!pagination.has_prev ? 'disabled' : ''}
            class="px-2 py-1 text-vscode-text hover:text-white disabled:opacity-50 disabled:cursor-not-allowed">
      &lt;
    </button>
  `;

  // Page info
  html += `<span class="text-vscode-text">Page ${pagination.page} of ${pagination.total_pages} (${pagination.total} total)</span>`;

  // Next button
  html += `
    <button onclick="goToPage(${pagination.page + 1})"
            ${!pagination.has_next ? 'disabled' : ''}
            class="px-2 py-1 text-vscode-text hover:text-white disabled:opacity-50 disabled:cursor-not-allowed">
      &gt;
    </button>
  `;

  html += '</div>';
  paginationEl.innerHTML = html;
}

// =============================================================================
// Detail Panel Rendering
// =============================================================================

function renderDetail(workspace) {
  if (!workspace) {
    document.getElementById('workspace-detail').classList.add('hidden');
    document.getElementById('no-selection').classList.remove('hidden');
    return;
  }

  document.getElementById('no-selection').classList.add('hidden');
  document.getElementById('empty-state').classList.add('hidden');
  document.getElementById('workspace-detail').classList.remove('hidden');

  const config = STATUS_CONFIG[workspace.status] || STATUS_CONFIG.ERROR;

  document.getElementById('detail-name').textContent = workspace.name;
  document.getElementById('detail-description').textContent = workspace.description || 'No description';
  document.getElementById('detail-memo').textContent = workspace.memo || 'No memo';
  document.getElementById('detail-created').textContent = formatDate(workspace.created_at);
  document.getElementById('detail-updated').textContent = formatDate(workspace.updated_at);

  const statusEl = document.getElementById('detail-status');
  statusEl.textContent = config.label;
  statusEl.className = `px-2 py-1 rounded text-xs font-medium text-white ${config.bgColor}`;
}

// =============================================================================
// Main Load Function
// =============================================================================

async function loadWorkspaces(page = 1, preserveSelection = true) {
  const listEl = document.getElementById('workspace-list');
  const emptyEl = document.getElementById('empty-state');
  const noSelectionEl = document.getElementById('no-selection');
  const loadingEl = document.getElementById('sidebar-loading');

  try {
    const data = await fetchWorkspaces(page);
    const workspaces = data.items;
    const pagination = data.pagination;

    currentPage = pagination.page;

    // Cache workspaces
    workspacesCache = {};
    workspaces.forEach(ws => { workspacesCache[ws.id] = ws; });

    loadingEl.classList.add('hidden');
    listEl.classList.remove('hidden');

    if (workspaces.length === 0 && pagination.total === 0) {
      listEl.innerHTML = '';
      emptyEl.classList.remove('hidden');
      noSelectionEl.classList.add('hidden');
      document.getElementById('workspace-detail').classList.add('hidden');
      selectedWorkspaceId = null;
    } else {
      emptyEl.classList.add('hidden');

      // Auto-select first workspace if none selected
      if (!preserveSelection || !selectedWorkspaceId || !workspacesCache[selectedWorkspaceId]) {
        if (workspaces.length > 0) {
          selectedWorkspaceId = workspaces[0].id;
        } else {
          selectedWorkspaceId = null;
        }
      }

      renderSidebar(workspaces);
      renderPagination(pagination);

      // Render detail
      if (selectedWorkspaceId && workspacesCache[selectedWorkspaceId]) {
        renderDetail(workspacesCache[selectedWorkspaceId]);
      } else {
        renderDetail(null);
      }
    }
  } catch (error) {
    loadingEl.classList.add('hidden');
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

// =============================================================================
// Workspace Selection
// =============================================================================

function selectWorkspace(id) {
  selectedWorkspaceId = id;

  // Re-render sidebar to update selection
  const workspaces = Object.values(workspacesCache);
  renderSidebar(workspaces);

  // Render detail
  if (workspacesCache[id]) {
    renderDetail(workspacesCache[id]);
  }
}

// =============================================================================
// Workspace Actions
// =============================================================================

function openWorkspace(id) {
  const workspace = workspacesCache[id];
  if (workspace && workspace.url) {
    window.open(workspace.url, '_blank');
  }
}

async function handleStart(id) {
  try {
    await startWorkspace(id);
    showToast('Workspace starting...', 'info');
    await loadWorkspaces(currentPage);
  } catch (error) {
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

async function handleStop(id) {
  try {
    await stopWorkspace(id);
    showToast('Workspace stopping...', 'info');
    await loadWorkspaces(currentPage);
  } catch (error) {
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

async function handleDelete(id, name) {
  if (!confirm(`Are you sure you want to delete "${name}"?`)) return;

  try {
    await deleteWorkspace(id);
    showToast('Workspace deleted', 'success');

    // Clear selection if deleted
    if (selectedWorkspaceId === id) {
      selectedWorkspaceId = null;
    }

    await loadWorkspaces(currentPage, false);
  } catch (error) {
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

// =============================================================================
// Pagination
// =============================================================================

function goToPage(page) {
  if (page < 1) return;
  loadWorkspaces(page, false);
}

// =============================================================================
// Modal Functions
// =============================================================================

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
  const memo = document.getElementById('workspace-memo').value.trim();

  try {
    const workspace = await createWorkspace(name, description, memo);
    showToast('Workspace created', 'success');
    closeCreateModal();
    selectedWorkspaceId = workspace.id;
    await loadWorkspaces(1, true);
  } catch (error) {
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

function openEditModal() {
  if (!selectedWorkspaceId || !workspacesCache[selectedWorkspaceId]) return;

  const workspace = workspacesCache[selectedWorkspaceId];

  document.getElementById('edit-workspace-id').value = workspace.id;
  document.getElementById('edit-name').value = workspace.name;
  document.getElementById('edit-description').value = workspace.description || '';
  document.getElementById('edit-memo').value = workspace.memo || '';

  document.getElementById('edit-modal').classList.remove('hidden');
  document.getElementById('edit-name').focus();
}

function closeEditModal() {
  document.getElementById('edit-modal').classList.add('hidden');
  document.getElementById('edit-form').reset();
}

async function handleEditSubmit(e) {
  e.preventDefault();

  const id = document.getElementById('edit-workspace-id').value;
  const name = document.getElementById('edit-name').value.trim();
  const description = document.getElementById('edit-description').value.trim();
  const memo = document.getElementById('edit-memo').value.trim();

  try {
    await updateWorkspace(id, { name, description, memo });
    showToast('Workspace updated', 'success');
    closeEditModal();
    await loadWorkspaces(currentPage);
  } catch (error) {
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

// =============================================================================
// Polling
// =============================================================================

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => loadWorkspaces(currentPage), POLL_INTERVAL);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

// =============================================================================
// Initialize
// =============================================================================

document.addEventListener('DOMContentLoaded', async () => {
  // Check session first
  const isLoggedIn = await checkSession();
  if (!isLoggedIn) {
    redirectToLogin();
    return;
  }

  // Load initial data
  await loadWorkspaces(1, false);

  // Start polling
  startPolling();

  // Logout button
  document.getElementById('logout-btn').addEventListener('click', logout);

  // Create modal event listeners
  document.getElementById('new-workspace-btn').addEventListener('click', openCreateModal);
  document.getElementById('close-modal-btn').addEventListener('click', closeCreateModal);
  document.getElementById('cancel-create-btn').addEventListener('click', closeCreateModal);
  document.getElementById('create-form').addEventListener('submit', handleCreateSubmit);

  // Edit modal event listeners
  document.getElementById('edit-workspace-btn').addEventListener('click', openEditModal);
  document.getElementById('close-edit-modal-btn').addEventListener('click', closeEditModal);
  document.getElementById('cancel-edit-btn').addEventListener('click', closeEditModal);
  document.getElementById('edit-form').addEventListener('submit', handleEditSubmit);

  // Close modals on backdrop click
  document.getElementById('create-modal').addEventListener('click', (e) => {
    if (e.target.id === 'create-modal') closeCreateModal();
  });
  document.getElementById('edit-modal').addEventListener('click', (e) => {
    if (e.target.id === 'edit-modal') closeEditModal();
  });

  // Close modals on Escape key
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeCreateModal();
      closeEditModal();
    }
  });

  // Pause polling when tab is hidden
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopPolling();
    } else {
      loadWorkspaces(currentPage);
      startPolling();
    }
  });
});
