/**
 * CodeHub Cards Module (M2)
 * Workspace card rendering and grid management
 */

import { state, STATUS_ORDER, getStatusConfig, getDisplayStatus } from './state.js';
import { escapeHtml, formatShortDate, formatDate } from './utils.js';

/**
 * Render skeleton loading cards
 */
export function renderSkeletonCards(count = 6) {
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

/**
 * Hide skeleton loading
 */
export function hideSkeletonLoading() {
  document.getElementById('loading-skeleton').classList.add('hidden');
}

/**
 * Render a single workspace card (M2)
 */
export function renderWorkspaceCard(workspace, index) {
  const config = getStatusConfig(workspace);
  const isSelected = workspace.id === state.selectedWorkspaceId;

  const spinnerHtml = config.isTransition
    ? '<span class="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full spinner ml-1"></span>'
    : '';

  let buttonsHtml = '<div class="flex flex-wrap gap-2 mt-3">';

  if (config.canOpen) {
    buttonsHtml += `
      <button data-action="open" data-id="${workspace.id}"
              class="px-3 py-1.5 bg-vscode-success hover:bg-green-600 text-white text-sm rounded transition-colors">
        Open
      </button>`;
  }

  if (config.canStart) {
    buttonsHtml += `
      <button data-action="start" data-id="${workspace.id}"
              class="px-3 py-1.5 bg-vscode-accent hover:bg-blue-600 text-white text-sm rounded transition-colors">
        Start
      </button>`;
  }

  if (config.canPause) {
    buttonsHtml += `
      <button data-action="pause" data-id="${workspace.id}"
              class="px-3 py-1.5 bg-vscode-hover border border-vscode-border text-white text-sm rounded transition-colors hover:border-vscode-text">
        Pause
      </button>`;
  }

  if (config.canArchive) {
    buttonsHtml += `
      <button data-action="archive" data-id="${workspace.id}"
              class="px-3 py-1.5 bg-vscode-hover border border-vscode-border text-white text-sm rounded transition-colors hover:border-vscode-warning hover:text-vscode-warning">
        Archive
      </button>`;
  }

  if (config.canDelete) {
    buttonsHtml += `
      <button data-action="delete" data-id="${workspace.id}" data-name="${escapeHtml(workspace.name)}"
              class="px-3 py-1.5 bg-vscode-hover border border-vscode-border hover:border-vscode-error hover:text-vscode-error text-sm rounded transition-colors">
        Delete
      </button>`;
  }

  buttonsHtml += '</div>';

  return `
    <div data-workspace-id="${workspace.id}"
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
      <div class="text-xs text-gray-500 flex gap-3">
        <span title="Created: ${formatDate(workspace.created_at)}">+ ${formatShortDate(workspace.created_at)}</span>
        <span title="Updated: ${formatDate(workspace.updated_at)}">~ ${formatShortDate(workspace.updated_at)}</span>
        ${workspace.last_access_at ? `<span title="Last active: ${formatDate(workspace.last_access_at)}">⚡ ${formatShortDate(workspace.last_access_at)}</span>` : ''}
      </div>
      ${buttonsHtml}
    </div>
  `;
}

/**
 * Get filtered and sorted workspaces based on current UI state (M2)
 */
export function getFilteredWorkspaces() {
  const searchQuery = document.getElementById('search-input').value.toLowerCase().trim();
  const statusFilter = document.getElementById('status-filter').value;
  const sortOption = document.getElementById('sort-select').value;

  let filtered = [...state.workspaces];

  // Search
  if (searchQuery) {
    filtered = filtered.filter(ws =>
      ws.name.toLowerCase().includes(searchQuery) ||
      (ws.description && ws.description.toLowerCase().includes(searchQuery)) ||
      (ws.memo && ws.memo.toLowerCase().includes(searchQuery))
    );
  }

  // Filter by status (M2: uses phase + operation)
  if (statusFilter !== 'all') {
    switch (statusFilter) {
      case 'running':
        filtered = filtered.filter(ws => ws.phase === 'RUNNING' && ws.operation === 'NONE');
        break;
      case 'standby':
        filtered = filtered.filter(ws => ws.phase === 'STANDBY' && ws.operation === 'NONE');
        break;
      case 'archived':
        filtered = filtered.filter(ws => ws.phase === 'ARCHIVED' && ws.operation === 'NONE');
        break;
      case 'pending':
        filtered = filtered.filter(ws => ws.phase === 'PENDING' && ws.operation === 'NONE');
        break;
      case 'error':
        filtered = filtered.filter(ws => ws.phase === 'ERROR');
        break;
      case 'transitioning':
        filtered = filtered.filter(ws => ws.operation !== 'NONE');
        break;
    }
  }

  // Sort (M2: uses getDisplayStatus for status order)
  switch (sortOption) {
    case 'name':
      filtered.sort((a, b) => a.name.localeCompare(b.name));
      break;
    case 'recent':
      filtered.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
      break;
    case 'status':
    default:
      filtered.sort((a, b) => STATUS_ORDER.indexOf(getDisplayStatus(a)) - STATUS_ORDER.indexOf(getDisplayStatus(b)));
      break;
  }

  return filtered;
}

/**
 * Render the workspace grid
 */
export function renderWorkspaceGrid(workspaces) {
  const gridEl = document.getElementById('workspace-grid');
  const emptyEl = document.getElementById('empty-state');
  const noResultsEl = document.getElementById('no-results-state');

  hideSkeletonLoading();

  if (state.workspaces.length === 0) {
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

/**
 * Render filtered workspaces (convenience function)
 */
export function renderFilteredWorkspaces() {
  const filtered = getFilteredWorkspaces();
  renderWorkspaceGrid(filtered);
}

/**
 * Scroll to a specific card by index
 */
export function scrollToCard(index) {
  const cards = document.querySelectorAll('.workspace-card');
  if (cards[index]) {
    cards[index].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    cards[index].focus();
  }
}
