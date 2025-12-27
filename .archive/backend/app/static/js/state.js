/**
 * CodeHub State Management
 * Centralized application state
 */

export const API = '/api/v1';
export const PER_PAGE = 20;
export const POLL_INTERVAL = 5000;

// Centralized state management
export const state = {
  currentPage: 1,
  selectedWorkspaceId: null,
  selectedCardIndex: -1,
  workspaces: [],
  cache: {},
  eventSource: null,
  pollTimer: null,
  loadVersion: 0,  // Tracks load version to prevent race conditions
};

// Status configuration
export const STATUS_CONFIG = {
  CREATED: { color: 'text-vscode-text', bgColor: 'bg-gray-600', icon: '○', label: 'Created', canStart: true, canDelete: true },
  PROVISIONING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Starting...', isTransition: true },
  RUNNING: { color: 'text-vscode-success', bgColor: 'bg-green-600', icon: '●', label: 'Running', canStop: true, canOpen: true },
  STOPPING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Stopping...', isTransition: true },
  STOPPED: { color: 'text-vscode-text', bgColor: 'bg-gray-600', icon: '○', label: 'Stopped', canStart: true, canDelete: true },
  DELETING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Deleting...', isTransition: true },
  ERROR: { color: 'text-vscode-error', bgColor: 'bg-red-600', icon: '✕', label: 'Error', canStart: true, canStop: true, canDelete: true },
};

export const STATUS_ORDER = ['RUNNING', 'PROVISIONING', 'STOPPING', 'ERROR', 'STOPPED', 'CREATED', 'DELETING'];

/**
 * Get the currently selected workspace from cache
 */
export function getSelectedWorkspace() {
  if (!state.selectedWorkspaceId) return null;
  return state.cache[state.selectedWorkspaceId] || null;
}
