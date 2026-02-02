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

let archivedWorkspacesList = [];

export async function openCreateModal() {
  showModal('create-modal');
  document.getElementById('workspace-name').focus();
  await loadArchivedWorkspaces();
  setupSourceWorkspacePicker();
}

async function loadArchivedWorkspaces() {
  try {
    const data = await fetchWorkspaces(0);
    archivedWorkspacesList = data.items.filter(
      ws => ws.phase === 'ARCHIVED' && ws.archive_key
    );
    renderSourceWorkspaceOptions('');
  } catch (error) {
    console.error('Failed to load archived workspaces:', error);
    archivedWorkspacesList = [];
  }
}

function renderSourceWorkspaceOptions(searchTerm) {
  const container = document.getElementById('source-workspace-options');
  const term = searchTerm.toLowerCase();
  
  const filtered = archivedWorkspacesList.filter(ws => 
    ws.name.toLowerCase().includes(term) || 
    (ws.description && ws.description.toLowerCase().includes(term))
  );

  let html = `
    <div class="source-ws-option px-3 py-2 cursor-pointer hover:bg-vscode-hover border-b border-vscode-border" data-id="" data-name="Start fresh (empty workspace)">
      <div class="text-white text-sm">Start fresh (empty workspace)</div>
      <div class="text-gray-500 text-xs">Create a new empty workspace</div>
    </div>
  `;

  for (const ws of filtered) {
    const desc = ws.description || 'No description';
    const escapedName = ws.name.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const escapedDesc = desc.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    html += `
      <div class="source-ws-option px-3 py-2 cursor-pointer hover:bg-vscode-hover border-b border-vscode-border last:border-b-0" data-id="${ws.id}" data-name="${escapedName}">
        <div class="text-white text-sm">${escapedName}</div>
        <div class="text-gray-500 text-xs truncate">${escapedDesc}</div>
      </div>
    `;
  }

  if (filtered.length === 0 && searchTerm) {
    html += `<div class="px-3 py-4 text-center text-gray-500 text-sm">No archived workspaces found</div>`;
  }

  container.innerHTML = html;

  container.querySelectorAll('.source-ws-option').forEach(opt => {
    opt.addEventListener('click', () => selectSourceWorkspace(opt.dataset.id, opt.dataset.name));
  });
}

function selectSourceWorkspace(id, name) {
  document.getElementById('source-workspace').value = id;
  document.getElementById('source-workspace-label').textContent = name;
  closeSourceWorkspaceDropdown();
}

function toggleSourceWorkspaceDropdown() {
  const dropdown = document.getElementById('source-workspace-dropdown');
  const isHidden = dropdown.classList.contains('hidden');
  if (isHidden) {
    dropdown.classList.remove('hidden');
    document.getElementById('source-workspace-search').value = '';
    document.getElementById('source-workspace-search').focus();
    renderSourceWorkspaceOptions('');
  } else {
    closeSourceWorkspaceDropdown();
  }
}

function closeSourceWorkspaceDropdown() {
  document.getElementById('source-workspace-dropdown').classList.add('hidden');
}

function setupSourceWorkspacePicker() {
  const btn = document.getElementById('source-workspace-btn');
  const search = document.getElementById('source-workspace-search');

  btn.onclick = toggleSourceWorkspaceDropdown;
  
  search.oninput = (e) => renderSourceWorkspaceOptions(e.target.value);
  
  search.onkeydown = (e) => {
    if (e.key === 'Escape') closeSourceWorkspaceDropdown();
  };

  document.addEventListener('click', (e) => {
    const picker = document.getElementById('source-workspace-picker');
    if (picker && !picker.contains(e.target)) {
      closeSourceWorkspaceDropdown();
    }
  }, { once: false });
}

function resetSourceWorkspacePicker() {
  document.getElementById('source-workspace').value = '';
  document.getElementById('source-workspace-label').textContent = 'Start fresh (empty workspace)';
  closeSourceWorkspaceDropdown();
}

export function closeCreateModal() {
  hideModal('create-modal');
  document.getElementById('create-form').reset();
  resetSourceWorkspacePicker();
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
