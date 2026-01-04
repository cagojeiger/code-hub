/**
 * CodeHub Keyboard Navigation Module
 * Keyboard shortcuts and navigation handling
 */

import { state, getStatusConfig } from './state.js';
import { startWorkspace, pauseWorkspace, archiveWorkspace } from './api.js';
import { showToast } from './utils.js';
import { getFilteredWorkspaces, renderFilteredWorkspaces, scrollToCard } from './cards.js';
import { closeDetailPanel, saveMemo } from './detail-panel.js';
import { openCreateModal, openShortcutsModal, closeAllModals, isModalOpen } from './modals.js';

/**
 * Open workspace in new tab
 */
export function openWorkspace(id) {
  if (!id) return;
  window.open(`/w/${id}/`, '_blank');
}

/**
 * Generic workspace action handler to reduce code duplication
 */
async function handleWorkspaceAction(id, apiCall, successMsg) {
  try {
    await apiCall(id);
    showToast(successMsg, 'info');
  } catch (error) {
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

/**
 * Handle workspace start action
 */
export const handleStart = (id) => handleWorkspaceAction(id, startWorkspace, 'Workspace starting...');

/**
 * Handle workspace pause action (RUNNING → STANDBY)
 */
export const handlePause = (id) => handleWorkspaceAction(id, pauseWorkspace, 'Workspace pausing...');

/**
 * Handle workspace archive action (STANDBY → ARCHIVED)
 */
export const handleArchive = (id) => handleWorkspaceAction(id, archiveWorkspace, 'Workspace archiving...');

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
          if (ws && getStatusConfig(ws).canOpen) {
            openWorkspace(state.selectedWorkspaceId);
          }
        }
        break;

      case 's':
      case 'S':
        if (state.selectedWorkspaceId) {
          const ws = state.cache[state.selectedWorkspaceId];
          if (ws && getStatusConfig(ws).canStart) {
            handleStart(state.selectedWorkspaceId);
          }
        }
        break;

      case 'p':
      case 'P':
        if (state.selectedWorkspaceId) {
          const ws = state.cache[state.selectedWorkspaceId];
          if (ws && getStatusConfig(ws).canPause) {
            handlePause(state.selectedWorkspaceId);
          }
        }
        break;

      case 'a':
      case 'A':
        if (state.selectedWorkspaceId) {
          const ws = state.cache[state.selectedWorkspaceId];
          if (ws && getStatusConfig(ws).canArchive) {
            handleArchive(state.selectedWorkspaceId);
          }
        }
        break;
    }
  };
}
