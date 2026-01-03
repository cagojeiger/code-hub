/**
 * CodeHub Detail Panel Module
 * Workspace detail panel rendering and management
 */

import { state, getStatusConfig } from './state.js';
import { updateWorkspace } from './api.js';
import { escapeHtml, showToast } from './utils.js';
import { renderFilteredWorkspaces } from './cards.js';

/**
 * Render the detail panel for a workspace
 */
export function renderDetailPanel(workspace) {
  if (!workspace) {
    closeDetailPanel();
    return;
  }

  const panel = document.getElementById('detail-panel');
  const config = getStatusConfig(workspace);

  document.getElementById('detail-name').textContent = workspace.name;
  document.getElementById('detail-description').textContent = workspace.description || 'No description';

  // Reset memo tab to Preview mode
  const writeTab = document.getElementById('memo-tab-write');
  const previewTab = document.getElementById('memo-tab-preview');
  const writePanel = document.getElementById('memo-write');
  const previewPanel = document.getElementById('memo-preview');
  const saveBtn = document.getElementById('memo-save-btn');

  writeTab.classList.remove('border-vscode-accent', 'text-white');
  writeTab.classList.add('border-transparent', 'text-vscode-text');
  previewTab.classList.add('border-vscode-accent', 'text-white');
  previewTab.classList.remove('border-transparent', 'text-vscode-text');
  writePanel.classList.add('hidden');
  previewPanel.classList.remove('hidden');
  saveBtn.classList.add('hidden');
  document.getElementById('memo-textarea').value = workspace.memo || '';

  // Render memo as markdown (sanitized to prevent XSS)
  const memoEl = document.getElementById('detail-memo');
  if (workspace.memo) {
    memoEl.innerHTML = DOMPurify.sanitize(marked.parse(workspace.memo));
  } else {
    memoEl.textContent = 'No memo';
  }

  const statusEl = document.getElementById('detail-status');
  statusEl.textContent = config.label;
  statusEl.className = `px-2 py-1 rounded text-xs font-medium text-white ${config.bgColor}`;

  // Render action buttons
  let actionsHtml = '';

  if (config.canOpen) {
    actionsHtml += `
      <button data-action="open" data-id="${workspace.id}"
              class="px-4 py-2 bg-vscode-success hover:bg-green-600 text-white rounded transition-colors">
        Open IDE
      </button>`;
  }

  if (config.canStart) {
    actionsHtml += `
      <button data-action="start" data-id="${workspace.id}"
              class="px-4 py-2 bg-vscode-accent hover:bg-blue-600 text-white rounded transition-colors">
        Start
      </button>`;
  }

  if (config.canStop) {
    actionsHtml += `
      <button data-action="stop" data-id="${workspace.id}"
              class="px-4 py-2 bg-vscode-hover border border-vscode-border text-white rounded transition-colors">
        Stop
      </button>`;
  }

  if (config.canDelete) {
    actionsHtml += `
      <button data-action="delete" data-id="${workspace.id}" data-name="${escapeHtml(workspace.name)}"
              class="px-4 py-2 bg-vscode-hover border border-vscode-border hover:border-vscode-error hover:text-vscode-error rounded transition-colors">
        Delete
      </button>`;
  }

  document.getElementById('detail-actions').innerHTML = actionsHtml;

  panel.classList.remove('hidden');
}

/**
 * Close the detail panel
 */
export function closeDetailPanel() {
  document.getElementById('detail-panel').classList.add('hidden');
  state.selectedWorkspaceId = null;
  state.selectedCardIndex = -1;
  renderFilteredWorkspaces();
}

/**
 * Switch between Write and Preview tabs for memo
 */
export function switchMemoTab(tab) {
  const writeTab = document.getElementById('memo-tab-write');
  const previewTab = document.getElementById('memo-tab-preview');
  const writePanel = document.getElementById('memo-write');
  const previewPanel = document.getElementById('memo-preview');
  const saveBtn = document.getElementById('memo-save-btn');
  const textarea = document.getElementById('memo-textarea');

  if (tab === 'write') {
    // Switch to Write tab
    writeTab.classList.add('border-vscode-accent', 'text-white');
    writeTab.classList.remove('border-transparent', 'text-vscode-text');
    previewTab.classList.remove('border-vscode-accent', 'text-white');
    previewTab.classList.add('border-transparent', 'text-vscode-text');
    writePanel.classList.remove('hidden');
    previewPanel.classList.add('hidden');
    saveBtn.classList.remove('hidden');
    // Note: textarea value is pre-loaded in renderDetailPanel
    textarea.focus();
  } else {
    // Switch to Preview tab
    previewTab.classList.add('border-vscode-accent', 'text-white');
    previewTab.classList.remove('border-transparent', 'text-vscode-text');
    writeTab.classList.remove('border-vscode-accent', 'text-white');
    writeTab.classList.add('border-transparent', 'text-vscode-text');
    previewPanel.classList.remove('hidden');
    writePanel.classList.add('hidden');
    saveBtn.classList.add('hidden');

    // Render current textarea content as markdown preview (sanitized)
    const content = textarea.value || '';
    const memoEl = document.getElementById('detail-memo');
    if (content.trim()) {
      memoEl.innerHTML = DOMPurify.sanitize(marked.parse(content));
    } else {
      memoEl.textContent = 'No memo';
    }
  }
}

/**
 * Save memo content
 */
export async function saveMemo() {
  const textarea = document.getElementById('memo-textarea');
  const newValue = textarea.value.trim() || null;

  if (!state.selectedWorkspaceId) return;

  try {
    await updateWorkspace(state.selectedWorkspaceId, { memo: newValue });
    showToast('Memo saved', 'success');

    // Update preview immediately (sanitized)
    const memoEl = document.getElementById('detail-memo');
    if (newValue) {
      memoEl.innerHTML = DOMPurify.sanitize(marked.parse(newValue));
    } else {
      memoEl.textContent = 'No memo';
    }
  } catch (error) {
    showToast('Failed to save memo', 'error');
  }
}

/**
 * Enable inline editing for a field
 */
export function enableInlineEdit(element, field, loadWorkspacesCallback) {
  if (!state.selectedWorkspaceId) return;
  if (element.querySelector('input, textarea')) return; // Already editing

  const workspace = state.cache[state.selectedWorkspaceId];
  if (!workspace) return;

  // Get the actual value from workspace object
  const actualValue = workspace[field] || '';

  const isMultiline = field === 'memo';
  const input = document.createElement(isMultiline ? 'textarea' : 'input');

  input.value = actualValue;
  input.className = isMultiline
    ? 'w-full bg-vscode-bg border border-vscode-accent rounded p-2 text-white focus:outline-none resize-none min-h-[100px]'
    : 'w-full bg-vscode-bg border border-vscode-accent rounded px-2 py-1 text-white focus:outline-none';

  if (isMultiline) {
    input.rows = 5;
  }

  const originalContent = element.innerHTML;
  const originalClass = element.className;
  element.innerHTML = '';
  element.className = element.className.replace('cursor-pointer', '').replace('hover:bg-vscode-hover', '').replace('hover:border-vscode-accent', '');
  element.appendChild(input);
  input.focus();
  if (!isMultiline) {
    input.select();
  }

  let isSaving = false;

  const cancelEdit = () => {
    if (isSaving) return;
    element.innerHTML = originalContent;
    element.className = originalClass;
  };

  const saveEdit = async () => {
    isSaving = true;
    const newValue = input.value.trim();
    const updateData = { [field]: newValue || null };

    try {
      await updateWorkspace(state.selectedWorkspaceId, updateData);
      showToast('Updated', 'success');
      await loadWorkspacesCallback();
      // Re-render detail panel with updated data
      if (state.cache[state.selectedWorkspaceId]) {
        renderDetailPanel(state.cache[state.selectedWorkspaceId]);
      }
    } catch (error) {
      isSaving = false;
      if (error.message !== 'Session expired') {
        showToast(error.message, 'error');
      }
      cancelEdit();
    }
  };

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      saveEdit();
    }
    if (e.key === 'Escape') {
      cancelEdit();
    }
  });

  input.addEventListener('blur', () => {
    setTimeout(() => {
      if (document.activeElement !== input) {
        cancelEdit();
      }
    }, 100);
  });
}
