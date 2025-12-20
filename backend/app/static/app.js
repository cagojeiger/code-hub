/**
 * CodeHub Dashboard Application
 * Card Grid layout with SSE real-time updates, search/filter, and keyboard navigation
 */

const API = '/api/v1';
const PER_PAGE = 20;

let currentPage = 1;
let selectedWorkspaceId = null;
let selectedCardIndex = -1;
let workspacesCache = {};
let allWorkspaces = [];
let eventSource = null;

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

const STATUS_ORDER = ['RUNNING', 'PROVISIONING', 'STOPPING', 'ERROR', 'STOPPED', 'CREATED', 'DELETING'];

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
// SSE Connection with Polling Fallback
// =============================================================================

let pollTimer = null;
const POLL_INTERVAL = 5000;

function connectSSE() {
  if (eventSource) {
    eventSource.close();
  }

  updateConnectionStatus('connecting');

  try {
    eventSource = new EventSource(`${API}/events`);

    eventSource.onopen = () => {
      updateConnectionStatus('connected');
      // Stop polling when SSE is connected
      stopPolling();
    };

    eventSource.addEventListener('workspace_updated', (event) => {
      const data = JSON.parse(event.data);
      handleWorkspaceUpdate(data);
    });

    eventSource.addEventListener('workspace_deleted', (event) => {
      const data = JSON.parse(event.data);
      handleWorkspaceDeleted(data.id);
    });

    eventSource.addEventListener('heartbeat', () => {
      // Keep-alive, no action needed
    });

    eventSource.onerror = () => {
      updateConnectionStatus('disconnected');
      // Start polling as fallback
      startPolling();
    };
  } catch (e) {
    // SSE not supported or failed, use polling
    console.warn('SSE not available, falling back to polling');
    updateConnectionStatus('polling');
    startPolling();
  }
}

function startPolling() {
  if (pollTimer) return; // Already polling
  pollTimer = setInterval(() => loadWorkspaces(currentPage), POLL_INTERVAL);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function updateConnectionStatus(status) {
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

function handleWorkspaceUpdate(workspace) {
  // Update cache
  workspacesCache[workspace.id] = workspace;

  // Update allWorkspaces array
  const index = allWorkspaces.findIndex(ws => ws.id === workspace.id);
  if (index >= 0) {
    allWorkspaces[index] = workspace;
  } else {
    allWorkspaces.unshift(workspace);
  }

  // Re-render
  renderFilteredWorkspaces();

  // Update detail panel if this workspace is selected
  if (selectedWorkspaceId === workspace.id) {
    renderDetailPanel(workspace);
  }

  // Show toast for status changes
  const config = STATUS_CONFIG[workspace.status];
  if (config && !config.isTransition) {
    showToast(`${workspace.name}: ${config.label}`, workspace.status === 'ERROR' ? 'error' : 'info');
  }
}

function handleWorkspaceDeleted(id) {
  // Remove from cache
  delete workspacesCache[id];

  // Remove from allWorkspaces
  allWorkspaces = allWorkspaces.filter(ws => ws.id !== id);

  // Re-render
  renderFilteredWorkspaces();

  // Close detail panel if this workspace was selected
  if (selectedWorkspaceId === id) {
    closeDetailPanel();
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

  toast.className = `${bgColor} text-white px-4 py-2 rounded shadow-lg transform transition-all duration-300 fade-in`;
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
// Skeleton Loading
// =============================================================================

function renderSkeletonCards(count = 6) {
  const skeletonHtml = Array(count).fill(0).map(() => `
    <div class="bg-vscode-sidebar border border-vscode-border rounded-lg p-4">
      <div class="flex items-center justify-between mb-3">
        <div class="skeleton h-5 w-32 rounded"></div>
        <div class="skeleton h-5 w-16 rounded"></div>
      </div>
      <div class="skeleton h-4 w-full rounded mb-2"></div>
      <div class="skeleton h-4 w-2/3 rounded mb-4"></div>
      <div class="flex gap-2">
        <div class="skeleton h-8 w-16 rounded"></div>
        <div class="skeleton h-8 w-16 rounded"></div>
      </div>
    </div>
  `).join('');

  document.getElementById('loading-skeleton').innerHTML = skeletonHtml;
}

function hideSkeletonLoading() {
  document.getElementById('loading-skeleton').classList.add('hidden');
}

// =============================================================================
// Card Grid Rendering
// =============================================================================

function renderWorkspaceCard(workspace, index) {
  const config = STATUS_CONFIG[workspace.status] || STATUS_CONFIG.ERROR;
  const isSelected = workspace.id === selectedWorkspaceId;

  const spinnerHtml = config.isTransition
    ? '<span class="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full spinner ml-1"></span>'
    : '';

  let buttonsHtml = '<div class="flex flex-wrap gap-2 mt-3">';

  if (config.canOpen) {
    buttonsHtml += `
      <button onclick="event.stopPropagation(); openWorkspace('${workspace.id}')"
              class="px-3 py-1.5 bg-vscode-success hover:bg-green-600 text-white text-sm rounded transition-colors">
        Open
      </button>`;
  }

  if (config.canStart) {
    buttonsHtml += `
      <button onclick="event.stopPropagation(); handleStart('${workspace.id}')"
              class="px-3 py-1.5 bg-vscode-accent hover:bg-blue-600 text-white text-sm rounded transition-colors">
        Start
      </button>`;
  }

  if (config.canStop) {
    buttonsHtml += `
      <button onclick="event.stopPropagation(); handleStop('${workspace.id}')"
              class="px-3 py-1.5 bg-vscode-hover border border-vscode-border text-white text-sm rounded transition-colors hover:border-vscode-text">
        Stop
      </button>`;
  }

  if (config.canDelete) {
    buttonsHtml += `
      <button onclick="event.stopPropagation(); openDeleteModal('${workspace.id}', '${escapeHtml(workspace.name)}')"
              class="px-3 py-1.5 bg-vscode-hover border border-vscode-border hover:border-vscode-error hover:text-vscode-error text-sm rounded transition-colors">
        Delete
      </button>`;
  }

  buttonsHtml += '</div>';

  return `
    <div onclick="selectWorkspace('${workspace.id}', ${index})"
         data-workspace-id="${workspace.id}"
         data-index="${index}"
         tabindex="0"
         class="workspace-card bg-vscode-sidebar border border-vscode-border rounded-lg p-4 cursor-pointer focus-ring ${isSelected ? 'selected' : ''}">
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-white font-medium truncate flex-1">${escapeHtml(workspace.name)}</h3>
        <span class="px-2 py-1 rounded text-xs font-medium text-white ${config.bgColor} flex items-center shrink-0 ml-2">
          ${config.icon} ${config.label}${spinnerHtml}
        </span>
      </div>
      <p class="text-vscode-text text-sm truncate mb-1">${escapeHtml(workspace.description) || 'No description'}</p>
      <p class="text-gray-500 text-xs">${formatDate(workspace.updated_at)}</p>
      ${buttonsHtml}
    </div>
  `;
}

function renderWorkspaceGrid(workspaces) {
  const gridEl = document.getElementById('workspace-grid');
  const emptyEl = document.getElementById('empty-state');
  const noResultsEl = document.getElementById('no-results-state');

  hideSkeletonLoading();

  if (allWorkspaces.length === 0) {
    gridEl.classList.add('hidden');
    noResultsEl.classList.add('hidden');
    emptyEl.classList.remove('hidden');
    return;
  }

  if (workspaces.length === 0) {
    gridEl.classList.add('hidden');
    emptyEl.classList.add('hidden');
    noResultsEl.classList.remove('hidden');
    return;
  }

  emptyEl.classList.add('hidden');
  noResultsEl.classList.add('hidden');
  gridEl.classList.remove('hidden');

  gridEl.innerHTML = workspaces.map((ws, index) => renderWorkspaceCard(ws, index)).join('');
}

// =============================================================================
// Search, Filter, Sort
// =============================================================================

function getFilteredWorkspaces() {
  const searchQuery = document.getElementById('search-input').value.toLowerCase().trim();
  const statusFilter = document.getElementById('status-filter').value;
  const sortOption = document.getElementById('sort-select').value;

  let filtered = [...allWorkspaces];

  // Search
  if (searchQuery) {
    filtered = filtered.filter(ws =>
      ws.name.toLowerCase().includes(searchQuery) ||
      (ws.description && ws.description.toLowerCase().includes(searchQuery)) ||
      (ws.memo && ws.memo.toLowerCase().includes(searchQuery))
    );
  }

  // Filter by status
  if (statusFilter !== 'all') {
    switch (statusFilter) {
      case 'running':
        filtered = filtered.filter(ws => ws.status === 'RUNNING');
        break;
      case 'stopped':
        filtered = filtered.filter(ws => ['STOPPED', 'CREATED'].includes(ws.status));
        break;
      case 'error':
        filtered = filtered.filter(ws => ws.status === 'ERROR');
        break;
      case 'transitioning':
        filtered = filtered.filter(ws => ['PROVISIONING', 'STOPPING', 'DELETING'].includes(ws.status));
        break;
    }
  }

  // Sort
  switch (sortOption) {
    case 'name':
      filtered.sort((a, b) => a.name.localeCompare(b.name));
      break;
    case 'status':
      filtered.sort((a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status));
      break;
    case 'recent':
    default:
      filtered.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
      break;
  }

  return filtered;
}

function renderFilteredWorkspaces() {
  const filtered = getFilteredWorkspaces();
  renderWorkspaceGrid(filtered);
}

// =============================================================================
// Detail Panel
// =============================================================================

function renderDetailPanel(workspace) {
  if (!workspace) {
    closeDetailPanel();
    return;
  }

  const panel = document.getElementById('detail-panel');
  const config = STATUS_CONFIG[workspace.status] || STATUS_CONFIG.ERROR;

  document.getElementById('detail-name').textContent = workspace.name;
  document.getElementById('detail-description').textContent = workspace.description || 'No description';
  document.getElementById('detail-memo').textContent = workspace.memo || 'No memo';
  document.getElementById('detail-created').textContent = formatDate(workspace.created_at);
  document.getElementById('detail-updated').textContent = formatDate(workspace.updated_at);

  const urlEl = document.getElementById('detail-url');
  urlEl.href = workspace.url;
  urlEl.textContent = workspace.url;

  const statusEl = document.getElementById('detail-status');
  statusEl.textContent = config.label;
  statusEl.className = `px-2 py-1 rounded text-xs font-medium text-white ${config.bgColor}`;

  // Render action buttons
  let actionsHtml = '';

  if (config.canOpen) {
    actionsHtml += `
      <button onclick="openWorkspace('${workspace.id}')"
              class="px-4 py-2 bg-vscode-success hover:bg-green-600 text-white rounded transition-colors">
        Open IDE
      </button>`;
  }

  if (config.canStart) {
    actionsHtml += `
      <button onclick="handleStart('${workspace.id}')"
              class="px-4 py-2 bg-vscode-accent hover:bg-blue-600 text-white rounded transition-colors">
        Start
      </button>`;
  }

  if (config.canStop) {
    actionsHtml += `
      <button onclick="handleStop('${workspace.id}')"
              class="px-4 py-2 bg-vscode-hover border border-vscode-border text-white rounded transition-colors">
        Stop
      </button>`;
  }

  if (config.canDelete) {
    actionsHtml += `
      <button onclick="openDeleteModal('${workspace.id}', '${escapeHtml(workspace.name)}')"
              class="px-4 py-2 bg-vscode-hover border border-vscode-border hover:border-vscode-error hover:text-vscode-error rounded transition-colors">
        Delete
      </button>`;
  }

  document.getElementById('detail-actions').innerHTML = actionsHtml;

  panel.classList.remove('hidden');
}

function closeDetailPanel() {
  document.getElementById('detail-panel').classList.add('hidden');
  selectedWorkspaceId = null;
  selectedCardIndex = -1;
  renderFilteredWorkspaces();
}

// =============================================================================
// Workspace Selection & Actions
// =============================================================================

function selectWorkspace(id, index = -1) {
  selectedWorkspaceId = id;
  selectedCardIndex = index >= 0 ? index : getFilteredWorkspaces().findIndex(ws => ws.id === id);

  renderFilteredWorkspaces();

  if (workspacesCache[id]) {
    renderDetailPanel(workspacesCache[id]);
  }
}

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
  } catch (error) {
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

async function handleDelete(id) {
  try {
    await deleteWorkspace(id);
    showToast('Workspace deleted', 'success');
    closeDeleteModal();
  } catch (error) {
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

// =============================================================================
// Pagination
// =============================================================================

function renderPagination(pagination) {
  const paginationEl = document.getElementById('pagination');

  if (pagination.total_pages <= 1) {
    paginationEl.classList.add('hidden');
    return;
  }

  paginationEl.classList.remove('hidden');

  let html = '<div class="flex items-center gap-4">';

  html += `
    <button onclick="goToPage(${pagination.page - 1})"
            ${!pagination.has_prev ? 'disabled' : ''}
            class="px-3 py-1 bg-vscode-sidebar border border-vscode-border rounded text-vscode-text hover:text-white disabled:opacity-50 disabled:cursor-not-allowed">
      Previous
    </button>
  `;

  html += `<span class="text-vscode-text">Page ${pagination.page} of ${pagination.total_pages}</span>`;

  html += `
    <button onclick="goToPage(${pagination.page + 1})"
            ${!pagination.has_next ? 'disabled' : ''}
            class="px-3 py-1 bg-vscode-sidebar border border-vscode-border rounded text-vscode-text hover:text-white disabled:opacity-50 disabled:cursor-not-allowed">
      Next
    </button>
  `;

  html += '</div>';
  paginationEl.innerHTML = html;
}

async function goToPage(page) {
  if (page < 1) return;
  currentPage = page;
  await loadWorkspaces(page);
}

// =============================================================================
// Main Load Function
// =============================================================================

async function loadWorkspaces(page = 1) {
  try {
    const data = await fetchWorkspaces(page);
    const workspaces = data.items;
    const pagination = data.pagination;

    currentPage = pagination.page;

    // Cache workspaces
    workspacesCache = {};
    allWorkspaces = [];
    workspaces.forEach(ws => {
      workspacesCache[ws.id] = ws;
      allWorkspaces.push(ws);
    });

    renderFilteredWorkspaces();
    renderPagination(pagination);

  } catch (error) {
    hideSkeletonLoading();
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
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
    await loadWorkspaces(1);
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

// Delete Modal
function openDeleteModal(id, name) {
  document.getElementById('delete-workspace-id').value = id;
  document.getElementById('delete-workspace-name').textContent = name;
  document.getElementById('delete-confirm-name').textContent = name;
  document.getElementById('delete-confirm-input').value = '';
  document.getElementById('confirm-delete-btn').disabled = true;

  document.getElementById('delete-modal').classList.remove('hidden');
  document.getElementById('delete-confirm-input').focus();
}

function closeDeleteModal() {
  document.getElementById('delete-modal').classList.add('hidden');
  document.getElementById('delete-confirm-input').value = '';
}

function handleDeleteConfirmInput() {
  const input = document.getElementById('delete-confirm-input').value;
  const expected = document.getElementById('delete-confirm-name').textContent;
  const confirmBtn = document.getElementById('confirm-delete-btn');

  confirmBtn.disabled = input !== expected;
}

async function handleConfirmDelete() {
  const id = document.getElementById('delete-workspace-id').value;
  await handleDelete(id);
}

// Shortcuts Modal
function openShortcutsModal() {
  document.getElementById('shortcuts-modal').classList.remove('hidden');
}

function closeShortcutsModal() {
  document.getElementById('shortcuts-modal').classList.add('hidden');
}

function closeAllModals() {
  closeCreateModal();
  closeEditModal();
  closeDeleteModal();
  closeShortcutsModal();
}

function isModalOpen() {
  return !document.getElementById('create-modal').classList.contains('hidden') ||
         !document.getElementById('edit-modal').classList.contains('hidden') ||
         !document.getElementById('delete-modal').classList.contains('hidden') ||
         !document.getElementById('shortcuts-modal').classList.contains('hidden');
}

// =============================================================================
// Keyboard Navigation
// =============================================================================

function handleKeyboardNavigation(e) {
  // Ignore if typing in an input
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
    if (e.key === 'Escape') {
      e.target.blur();
      closeAllModals();
    }
    return;
  }

  // Modal is open
  if (isModalOpen()) {
    if (e.key === 'Escape') {
      closeAllModals();
    }
    return;
  }

  const filtered = getFilteredWorkspaces();

  switch (e.key) {
    case '/':
      e.preventDefault();
      document.getElementById('search-input').focus();
      break;

    case 'n':
    case 'N':
      e.preventDefault();
      openCreateModal();
      break;

    case '?':
      e.preventDefault();
      openShortcutsModal();
      break;

    case 'Escape':
      if (!document.getElementById('detail-panel').classList.contains('hidden')) {
        closeDetailPanel();
      }
      break;

    case 'ArrowLeft':
    case 'ArrowUp':
      e.preventDefault();
      if (filtered.length > 0) {
        selectedCardIndex = selectedCardIndex <= 0 ? filtered.length - 1 : selectedCardIndex - 1;
        selectWorkspace(filtered[selectedCardIndex].id, selectedCardIndex);
        scrollToCard(selectedCardIndex);
      }
      break;

    case 'ArrowRight':
    case 'ArrowDown':
      e.preventDefault();
      if (filtered.length > 0) {
        selectedCardIndex = selectedCardIndex >= filtered.length - 1 ? 0 : selectedCardIndex + 1;
        selectWorkspace(filtered[selectedCardIndex].id, selectedCardIndex);
        scrollToCard(selectedCardIndex);
      }
      break;

    case 'Enter':
      if (selectedWorkspaceId) {
        const ws = workspacesCache[selectedWorkspaceId];
        if (ws && STATUS_CONFIG[ws.status]?.canOpen) {
          openWorkspace(selectedWorkspaceId);
        }
      }
      break;

    case 's':
    case 'S':
      if (selectedWorkspaceId) {
        const ws = workspacesCache[selectedWorkspaceId];
        if (ws && STATUS_CONFIG[ws.status]?.canStart) {
          handleStart(selectedWorkspaceId);
        }
      }
      break;

    case 'x':
    case 'X':
      if (selectedWorkspaceId) {
        const ws = workspacesCache[selectedWorkspaceId];
        if (ws && STATUS_CONFIG[ws.status]?.canStop) {
          handleStop(selectedWorkspaceId);
        }
      }
      break;
  }
}

function scrollToCard(index) {
  const cards = document.querySelectorAll('.workspace-card');
  if (cards[index]) {
    cards[index].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    cards[index].focus();
  }
}

// =============================================================================
// Initialize
// =============================================================================

document.addEventListener('DOMContentLoaded', async () => {
  // Show skeleton loading
  renderSkeletonCards();

  // Check session first
  const isLoggedIn = await checkSession();
  if (!isLoggedIn) {
    redirectToLogin();
    return;
  }

  // Load initial data
  await loadWorkspaces(1);

  // Connect SSE for real-time updates
  connectSSE();

  // Event listeners
  document.getElementById('logout-btn').addEventListener('click', logout);
  document.getElementById('help-btn').addEventListener('click', openShortcutsModal);

  // Search and filter
  document.getElementById('search-input').addEventListener('input', renderFilteredWorkspaces);
  document.getElementById('status-filter').addEventListener('change', renderFilteredWorkspaces);
  document.getElementById('sort-select').addEventListener('change', renderFilteredWorkspaces);

  // Create modal
  document.getElementById('new-workspace-btn').addEventListener('click', openCreateModal);
  document.getElementById('close-modal-btn').addEventListener('click', closeCreateModal);
  document.getElementById('cancel-create-btn').addEventListener('click', closeCreateModal);
  document.getElementById('create-form').addEventListener('submit', handleCreateSubmit);

  // Edit modal
  document.getElementById('edit-workspace-btn').addEventListener('click', openEditModal);
  document.getElementById('close-edit-modal-btn').addEventListener('click', closeEditModal);
  document.getElementById('cancel-edit-btn').addEventListener('click', closeEditModal);
  document.getElementById('edit-form').addEventListener('submit', handleEditSubmit);

  // Delete modal
  document.getElementById('close-delete-modal-btn').addEventListener('click', closeDeleteModal);
  document.getElementById('cancel-delete-btn').addEventListener('click', closeDeleteModal);
  document.getElementById('delete-confirm-input').addEventListener('input', handleDeleteConfirmInput);
  document.getElementById('confirm-delete-btn').addEventListener('click', handleConfirmDelete);

  // Shortcuts modal
  document.getElementById('close-shortcuts-modal-btn').addEventListener('click', closeShortcutsModal);

  // Detail panel
  document.getElementById('close-panel-btn').addEventListener('click', closeDetailPanel);

  // Close modals on backdrop click
  ['create-modal', 'edit-modal', 'delete-modal', 'shortcuts-modal'].forEach(modalId => {
    document.getElementById(modalId).addEventListener('click', (e) => {
      if (e.target.id === modalId) {
        closeAllModals();
      }
    });
  });

  // Keyboard navigation
  document.addEventListener('keydown', handleKeyboardNavigation);

  // Pause SSE/polling when tab is hidden, reconnect when visible
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopPolling();
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
    } else {
      loadWorkspaces(currentPage);
      connectSSE();
    }
  });
});
