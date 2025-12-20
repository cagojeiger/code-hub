/**
 * CodeHub Modals Module
 * Modal dialog management (create, delete, shortcuts)
 */

import { state } from './state.js';
import { createWorkspace, deleteWorkspace } from './api.js';
import { showToast } from './utils.js';

/**
 * Open the create workspace modal
 */
export function openCreateModal() {
  document.getElementById('create-modal').classList.remove('hidden');
  document.getElementById('workspace-name').focus();
}

/**
 * Close the create workspace modal
 */
export function closeCreateModal() {
  document.getElementById('create-modal').classList.add('hidden');
  document.getElementById('create-form').reset();
}

/**
 * Handle create form submission
 */
export async function handleCreateSubmit(e, loadWorkspacesCallback) {
  e.preventDefault();

  const name = document.getElementById('workspace-name').value.trim();
  const description = document.getElementById('workspace-description').value.trim();
  const memo = document.getElementById('workspace-memo').value.trim();

  try {
    const workspace = await createWorkspace(name, description, memo);
    showToast('Workspace created', 'success');
    closeCreateModal();
    state.selectedWorkspaceId = workspace.id;
    await loadWorkspacesCallback(1);
  } catch (error) {
    if (error.message !== 'Session expired') {
      showToast(error.message, 'error');
    }
  }
}

/**
 * Open the delete confirmation modal
 */
export function openDeleteModal(id, name) {
  document.getElementById('delete-workspace-id').value = id;
  document.getElementById('delete-workspace-name').textContent = name;
  document.getElementById('delete-confirm-name').textContent = name;
  document.getElementById('delete-confirm-input').value = '';
  document.getElementById('confirm-delete-btn').disabled = true;

  document.getElementById('delete-modal').classList.remove('hidden');
  document.getElementById('delete-confirm-input').focus();
}

/**
 * Close the delete confirmation modal
 */
export function closeDeleteModal() {
  document.getElementById('delete-modal').classList.add('hidden');
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
export async function handleConfirmDelete() {
  const id = document.getElementById('delete-workspace-id').value;

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

/**
 * Open the keyboard shortcuts modal
 */
export function openShortcutsModal() {
  document.getElementById('shortcuts-modal').classList.remove('hidden');
}

/**
 * Close the keyboard shortcuts modal
 */
export function closeShortcutsModal() {
  document.getElementById('shortcuts-modal').classList.add('hidden');
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
