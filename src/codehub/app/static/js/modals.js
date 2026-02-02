/**
 * CodeHub Modals Module
 * Modal dialog management (create, delete, shortcuts)
 */

import { state } from './state.js';
import { createWorkspace, deleteWorkspace, fetchWorkspaces } from './api.js';
import { showToast } from './utils.js';

// =============================================================================
// Generic Modal Utilities
// =============================================================================

/**
 * Show a modal by ID
 */
function showModal(modalId) {
  document.getElementById(modalId).classList.remove('hidden');
}

/**
 * Hide a modal by ID
 */
function hideModal(modalId) {
  document.getElementById(modalId).classList.add('hidden');
}

// =============================================================================
// Create Modal
// =============================================================================

export async function openCreateModal() {
  showModal('create-modal');
  document.getElementById('workspace-name').focus();
  await populateSourceWorkspaceDropdown();
}

async function populateSourceWorkspaceDropdown() {
  const select = document.getElementById('source-workspace');
  select.innerHTML = '<option value="">Start fresh (empty workspace)</option>';

  try {
    const data = await fetchWorkspaces(0);
    const archivedWorkspaces = data.items.filter(
      ws => ws.phase === 'ARCHIVED' && ws.archive_key
    );

    for (const ws of archivedWorkspaces) {
      const option = document.createElement('option');
      option.value = ws.id;
      option.textContent = ws.name;
      select.appendChild(option);
    }
  } catch (error) {
    console.error('Failed to load archived workspaces:', error);
  }
}

/**
 * Close the create workspace modal
 */
export function closeCreateModal() {
  hideModal('create-modal');
  document.getElementById('create-form').reset();
}

export async function handleCreateSubmit(e, loadWorkspacesCallback) {
  e.preventDefault();

  const name = document.getElementById('workspace-name').value.trim();
  const description = document.getElementById('workspace-description').value.trim();
  const memo = document.getElementById('workspace-memo').value.trim();
  const sourceWorkspaceId = document.getElementById('source-workspace').value || null;

  try {
    const workspace = await createWorkspace(name, description, memo, sourceWorkspaceId);
    const message = sourceWorkspaceId ? 'Workspace created (restoring...)' : 'Workspace created';
    showToast(message, 'success');
    closeCreateModal();
    state.selectedWorkspaceId = workspace.id;
    await loadWorkspacesCallback(0);
  } catch (error) {
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

// =============================================================================
// Delete Modal
// =============================================================================

/**
 * Open the delete confirmation modal
 */
export function openDeleteModal(id, name) {
  document.getElementById('delete-workspace-id').value = id;
  document.getElementById('delete-workspace-name').textContent = name;
  document.getElementById('delete-confirm-name').textContent = name;
  document.getElementById('delete-confirm-input').value = '';
  document.getElementById('confirm-delete-btn').disabled = true;

  showModal('delete-modal');
  document.getElementById('delete-confirm-input').focus();
}

/**
 * Close the delete confirmation modal
 */
export function closeDeleteModal() {
  hideModal('delete-modal');
  document.getElementById('delete-confirm-input').value = '';
}

/**
 * Handle delete confirmation input
 */
export function handleDeleteConfirmInput() {
  const input = document.getElementById('delete-confirm-input').value;
  const expected = document.getElementById('delete-confirm-name').textContent;
  const confirmBtn = document.getElementById('confirm-delete-btn');

  confirmBtn.disabled = input !== expected;
}

/**
 * Handle confirmed delete
 */
export async function handleConfirmDelete(loadWorkspacesCallback) {
  const id = document.getElementById('delete-workspace-id').value;

  try {
    await deleteWorkspace(id);
    showToast('Workspace deleted', 'success');
    closeDeleteModal();
    state.selectedWorkspaceId = null;
    await loadWorkspacesCallback(0);
  } catch (error) {
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

/**
 * Open the keyboard shortcuts modal
 */
export function openShortcutsModal() {
  showModal('shortcuts-modal');
}

/**
 * Close the keyboard shortcuts modal
 */
export function closeShortcutsModal() {
  hideModal('shortcuts-modal');
}

/**
 * Close all open modals
 */
export function closeAllModals() {
  closeCreateModal();
  closeDeleteModal();
  closeShortcutsModal();
}

/**
 * Check if any modal is currently open
 */
export function isModalOpen() {
  return !document.getElementById('create-modal').classList.contains('hidden') ||
         !document.getElementById('delete-modal').classList.contains('hidden') ||
         !document.getElementById('shortcuts-modal').classList.contains('hidden');
}
