/**
 * CodeHub Keyboard Navigation Module
 * Keyboard shortcuts and navigation handling
 */

import { state, STATUS_CONFIG } from './state.js';
import { startWorkspace, stopWorkspace } from './api.js';
import { showToast } from './utils.js';
import { getFilteredWorkspaces, renderFilteredWorkspaces, scrollToCard } from './cards.js';
import { closeDetailPanel, saveMemo } from './detail-panel.js';
import { openCreateModal, openShortcutsModal, closeAllModals, isModalOpen } from './modals.js';

/**
 * Open workspace in new tab
 */
export function openWorkspace(id) {
  const workspace = state.cache[id];
  if (workspace && workspace.url) {
    window.open(workspace.url, '_blank');
  }
}

/**
 * Handle workspace start action
 */
export async function handleStart(id) {
  try {
    await startWorkspace(id);
    showToast('Workspace starting...', 'info');
  } catch (error) {
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

/**
 * Handle workspace stop action
 */
export async function handleStop(id) {
  try {
    await stopWorkspace(id);
    showToast('Workspace stopping...', 'info');
  } catch (error) {
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

/**
 * Select a workspace by ID
 */
export function selectWorkspace(id, index = -1) {
  state.selectedWorkspaceId = id;
  state.selectedCardIndex = index >= 0 ? index : getFilteredWorkspaces().findIndex(ws => ws.id === id);

  renderFilteredWorkspaces();

  // Import dynamically to avoid circular dependency
  import('./detail-panel.js').then(({ renderDetailPanel }) => {
    if (state.cache[id]) {
      renderDetailPanel(state.cache[id]);
    }
  });
}

/**
 * Create keyboard navigation handler
 */
export function createKeyboardHandler() {
  return function handleKeyboardNavigation(e) {
    // Handle Ctrl+S for memo save
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      const memoTextarea = document.getElementById('memo-textarea');
      if (e.target === memoTextarea && !document.getElementById('memo-write').classList.contains('hidden')) {
        e.preventDefault();
        saveMemo();
        return;
      }
    }

    // Ignore if typing in an input or textarea
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
      if (e.key === 'Escape' && isModalOpen()) {
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
          state.selectedCardIndex = state.selectedCardIndex <= 0 ? filtered.length - 1 : state.selectedCardIndex - 1;
          selectWorkspace(filtered[state.selectedCardIndex].id, state.selectedCardIndex);
          scrollToCard(state.selectedCardIndex);
        }
        break;

      case 'ArrowRight':
      case 'ArrowDown':
        e.preventDefault();
        if (filtered.length > 0) {
          state.selectedCardIndex = state.selectedCardIndex >= filtered.length - 1 ? 0 : state.selectedCardIndex + 1;
          selectWorkspace(filtered[state.selectedCardIndex].id, state.selectedCardIndex);
          scrollToCard(state.selectedCardIndex);
        }
        break;

      case 'Enter':
        if (state.selectedWorkspaceId) {
          const ws = state.cache[state.selectedWorkspaceId];
          if (ws && STATUS_CONFIG[ws.status]?.canOpen) {
            openWorkspace(state.selectedWorkspaceId);
          }
        }
        break;

      case 's':
      case 'S':
        if (state.selectedWorkspaceId) {
          const ws = state.cache[state.selectedWorkspaceId];
          if (ws && STATUS_CONFIG[ws.status]?.canStart) {
            handleStart(state.selectedWorkspaceId);
          }
        }
        break;

      case 'x':
      case 'X':
        if (state.selectedWorkspaceId) {
          const ws = state.cache[state.selectedWorkspaceId];
          if (ws && STATUS_CONFIG[ws.status]?.canStop) {
            handleStop(state.selectedWorkspaceId);
          }
        }
        break;
    }
  };
}
