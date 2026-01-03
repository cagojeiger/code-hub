/**
 * CodeHub State Management (M2)
 * Centralized application state
 */

export const API = '/api/v1';
export const LIMIT = 50;
export const POLL_INTERVAL = 5000;

// Centralized state management
export const state = {
  offset: 0,
  selectedWorkspaceId: null,
  selectedCardIndex: -1,
  workspaces: [],
  cache: {},
  eventSource: null,
  pollTimer: null,
  loadVersion: 0,
};

// M2 Phase + Operation → Display Status mapping
export const STATUS_CONFIG = {
  // Stable states
  PENDING: { color: 'text-vscode-text', bgColor: 'bg-gray-600', icon: '○', label: 'Pending', canStart: true, canDelete: true },
  ARCHIVED: { color: 'text-vscode-text', bgColor: 'bg-gray-600', icon: '◇', label: 'Archived', canStart: true, canDelete: true },
  STANDBY: { color: 'text-vscode-text', bgColor: 'bg-blue-600', icon: '◆', label: 'Standby', canStart: true, canStop: true, canDelete: true },
  RUNNING: { color: 'text-vscode-success', bgColor: 'bg-green-600', icon: '●', label: 'Running', canStop: true, canOpen: true },
  ERROR: { color: 'text-vscode-error', bgColor: 'bg-red-600', icon: '✕', label: 'Error', canStart: true, canStop: true, canDelete: true },
  DELETING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Deleting...', isTransition: true },
  DELETED: { color: 'text-gray-500', bgColor: 'bg-gray-800', icon: '○', label: 'Deleted' },

  // Transitional states (operation != NONE)
  PROVISIONING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Provisioning...', isTransition: true },
  RESTORING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Restoring...', isTransition: true },
  STARTING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Starting...', isTransition: true },
  STOPPING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Stopping...', isTransition: true },
  ARCHIVING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Archiving...', isTransition: true },
  CREATE_EMPTY_ARCHIVE: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Creating...', isTransition: true },
};

export const STATUS_ORDER = ['RUNNING', 'STARTING', 'STOPPING', 'STANDBY', 'ARCHIVING', 'RESTORING', 'PROVISIONING', 'ERROR', 'ARCHIVED', 'PENDING', 'DELETING', 'DELETED'];

/**
 * Get display status from workspace phase and operation (M2)
 * @param {object} workspace - Workspace object with phase and operation
 * @returns {string} Display status key for STATUS_CONFIG
 */
export function getDisplayStatus(workspace) {
  const { phase, operation } = workspace;

  // If operation is active (not NONE), show operation as status
  if (operation && operation !== 'NONE') {
    return operation;
  }

  // Otherwise show phase
  return phase || 'PENDING';
}

/**
 * Get status config for a workspace
 * @param {object} workspace - Workspace object
 * @returns {object} Status config object
 */
export function getStatusConfig(workspace) {
  const status = getDisplayStatus(workspace);
  return STATUS_CONFIG[status] || STATUS_CONFIG.ERROR;
}

/**
 * Get the currently selected workspace from cache
 */
export function getSelectedWorkspace() {
  if (!state.selectedWorkspaceId) return null;
  return state.cache[state.selectedWorkspaceId] || null;
}
