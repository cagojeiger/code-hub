/**
 * CodeHub Main Entry Point
 * Application initialization and event binding
 */

import { state, LIMIT } from './state.js';
import { checkSession, logout, fetchWorkspaces, redirectToLogin } from './api.js';
import { updateFooterStats, updateFooterDate } from './utils.js';
import { renderSkeletonCards, hideSkeletonLoading, renderFilteredWorkspaces } from './cards.js';
import { renderDetailPanel, closeDetailPanel, switchMemoTab, saveMemo, enableInlineEdit } from './detail-panel.js';
import {
  openCreateModal, closeCreateModal, handleCreateSubmit,
  openDeleteModal, closeDeleteModal, handleDeleteConfirmInput, handleConfirmDelete,
  openShortcutsModal, closeShortcutsModal, closeAllModals
} from './modals.js';
import { createKeyboardHandler, openWorkspace, handleStart, handleStop, selectWorkspace } from './keyboard.js';
import { connectSSE, setupVisibilityHandler } from './sse.js';

/**
 * Load workspaces and update UI
 * Uses loadVersion to prevent race conditions with SSE updates
 */
async function loadWorkspaces(offset = 0) {
  const currentVersion = ++state.loadVersion;

  try {
    const data = await fetchWorkspaces(offset);

    // If a newer load started while we were fetching, discard this result
    if (state.loadVersion !== currentVersion) return;

    const workspaces = data.items;
    const total = data.total;

    state.offset = offset;

    // Cache workspaces
    state.cache = {};
    state.workspaces = [];
    workspaces.forEach(ws => {
      state.cache[ws.id] = ws;
      state.workspaces.push(ws);
    });

    renderFilteredWorkspaces();
    renderPagination(offset, total);
    updateFooterStats(state.workspaces);

  } catch (error) {
    // If a newer load started, ignore this error
    if (state.loadVersion !== currentVersion) return;

    hideSkeletonLoading();
    if (error.message !== 'Session expired') {
      import('./utils.js').then(({ showToast }) => {
        showToast(error.message, 'error');
      });
    }
  }
}

/**
 * Render pagination controls (M2: offset-based)
 */
function renderPagination(offset, total) {
  const paginationEl = document.getElementById('pagination');
  const totalPages = Math.ceil(total / LIMIT);
  const currentPage = Math.floor(offset / LIMIT) + 1;

  if (totalPages <= 1) {
    paginationEl.classList.add('hidden');
    return;
  }

  paginationEl.classList.remove('hidden');

  const hasPrev = offset > 0;
  const hasNext = offset + LIMIT < total;

  let html = '<div class="flex items-center gap-4">';

  html += `
    <button data-action="page-prev" ${!hasPrev ? 'disabled' : ''}
            class="px-3 py-1 bg-vscode-sidebar border border-vscode-border rounded text-vscode-text hover:text-white disabled:opacity-50 disabled:cursor-not-allowed">
      Previous
    </button>
  `;

  html += `<span class="text-vscode-text">Page ${currentPage} of ${totalPages}</span>`;

  html += `
    <button data-action="page-next" ${!hasNext ? 'disabled' : ''}
            class="px-3 py-1 bg-vscode-sidebar border border-vscode-border rounded text-vscode-text hover:text-white disabled:opacity-50 disabled:cursor-not-allowed">
      Next
    </button>
  `;

  html += '</div>';
  paginationEl.innerHTML = html;
}

/**
 * Go to next/previous page (M2: offset-based)
 */
async function goToOffset(newOffset) {
  if (newOffset < 0) return;
  await loadWorkspaces(newOffset);
}

/**
 * Setup panel resize functionality
 */
function setupPanelResize() {
  const resizeHandle = document.getElementById('panel-resize-handle');
  const detailPanel = document.getElementById('detail-panel');
  let isResizing = false;

  resizeHandle.addEventListener('mousedown', (e) => {
    isResizing = true;
    resizeHandle.classList.add('active');
    document.body.style.cursor = 'ew-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });

  document.addEventListener('mousemove', (e) => {
    if (!isResizing) return;

    const containerRight = document.querySelector('main').getBoundingClientRect().right;
    const newWidth = containerRight - e.clientX;

    // Respect min/max constraints
    const minWidth = 280;
    const maxWidth = 600;
    const clampedWidth = Math.max(minWidth, Math.min(maxWidth, newWidth));

    detailPanel.style.width = `${clampedWidth}px`;
  });

  document.addEventListener('mouseup', () => {
    if (isResizing) {
      isResizing = false;
      resizeHandle.classList.remove('active');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }
  });
}

/**
 * Setup event delegation for actions
 */
function setupEventDelegation() {
  document.addEventListener('click', (e) => {
    const actionBtn = e.target.closest('[data-action]');
    if (actionBtn) {
      e.stopPropagation();
      const action = actionBtn.dataset.action;
      const id = actionBtn.dataset.id;
      const name = actionBtn.dataset.name;

      switch (action) {
        case 'open':
          openWorkspace(id);
          break;
        case 'start':
          handleStart(id);
          break;
        case 'stop':
          handleStop(id);
          break;
        case 'delete':
          openDeleteModal(id, name);
          break;
        case 'memo-tab':
          switchMemoTab(actionBtn.dataset.tab);
          break;
        case 'memo-save':
          saveMemo();
          break;
        case 'page-prev':
          goToOffset(state.offset - LIMIT);
          break;
        case 'page-next':
          goToOffset(state.offset + LIMIT);
          break;
      }
      return;
    }

    // Card click (select workspace)
    const card = e.target.closest('.workspace-card');
    if (card && !e.target.closest('[data-action]')) {
      const id = card.dataset.workspaceId;
      const index = parseInt(card.dataset.index, 10);
      selectWorkspace(id, index);
    }
  });

  // Close detail panel on click outside
  document.getElementById('workspace-container').addEventListener('click', (e) => {
    const detailPanel = document.getElementById('detail-panel');
    if (detailPanel.classList.contains('hidden')) return;
    if (e.target.closest('.workspace-card')) return;
    if (detailPanel.contains(e.target)) return;
    closeDetailPanel();
  });

  // Close modals on backdrop click
  ['create-modal', 'delete-modal', 'shortcuts-modal'].forEach(modalId => {
    document.getElementById(modalId).addEventListener('click', (e) => {
      if (e.target.id === modalId) {
        closeAllModals();
      }
    });
  });
}

/**
 * Initialize the application
 */
async function init() {
  // Show skeleton loading
  renderSkeletonCards();

  // Check session first
  const isLoggedIn = await checkSession();
  if (!isLoggedIn) {
    redirectToLogin();
    return;
  }

  // Load initial data
  await loadWorkspaces(0);

  // Connect SSE for real-time updates
  connectSSE(loadWorkspaces);

  // Update footer date
  updateFooterDate();
  setInterval(updateFooterDate, 60000);

  // Event listeners
  document.getElementById('logout-btn').addEventListener('click', logout);
  document.getElementById('footer-shortcuts-btn').addEventListener('click', openShortcutsModal);

  // Search and filter
  document.getElementById('search-input').addEventListener('input', renderFilteredWorkspaces);
  document.getElementById('status-filter').addEventListener('change', renderFilteredWorkspaces);
  document.getElementById('sort-select').addEventListener('change', renderFilteredWorkspaces);

  // Create modal
  document.getElementById('new-workspace-btn').addEventListener('click', openCreateModal);
  document.getElementById('close-modal-btn').addEventListener('click', closeCreateModal);
  document.getElementById('cancel-create-btn').addEventListener('click', closeCreateModal);
  document.getElementById('create-form').addEventListener('submit', (e) => handleCreateSubmit(e, loadWorkspaces));

  // Inline editing
  document.getElementById('detail-name').addEventListener('dblclick', (e) => {
    enableInlineEdit(e.currentTarget, 'name', () => loadWorkspaces(state.offset));
  });
  document.getElementById('detail-description').addEventListener('dblclick', (e) => {
    enableInlineEdit(e.currentTarget, 'description', () => loadWorkspaces(state.offset));
  });

  // Delete modal
  document.getElementById('close-delete-modal-btn').addEventListener('click', closeDeleteModal);
  document.getElementById('cancel-delete-btn').addEventListener('click', closeDeleteModal);
  document.getElementById('delete-confirm-input').addEventListener('input', handleDeleteConfirmInput);
  document.getElementById('confirm-delete-btn').addEventListener('click', handleConfirmDelete);

  // Shortcuts modal
  document.getElementById('close-shortcuts-modal-btn').addEventListener('click', closeShortcutsModal);

  // Detail panel
  document.getElementById('close-panel-btn').addEventListener('click', closeDetailPanel);

  // Setup event delegation
  setupEventDelegation();

  // Keyboard navigation
  document.addEventListener('keydown', createKeyboardHandler());

  // Visibility handler for SSE/polling
  setupVisibilityHandler(loadWorkspaces);

  // Panel resize
  setupPanelResize();
}

// Start the application
document.addEventListener('DOMContentLoaded', init);
