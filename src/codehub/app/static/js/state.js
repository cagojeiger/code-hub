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

// Transition state tracking for consistent progress display
// Structure: { [workspaceId]: { startPhase, desiredState } }
export const transitionState = {};

// Total steps per transition type
export const TRANSITION_STEPS = {
  'PENDING_RUNNING': 4,    // CREATE_EMPTY → PROVISIONING → STARTING → RUNNING
  'ARCHIVED_RUNNING': 3,   // RESTORING → STARTING → RUNNING
  'STANDBY_RUNNING': 2,    // STARTING → RUNNING
  'RUNNING_STANDBY': 2,    // STOPPING → STANDBY
  'STANDBY_ARCHIVED': 2,   // ARCHIVING → ARCHIVED
  'RUNNING_ARCHIVED': 3,   // STOPPING → ARCHIVING → ARCHIVED
};

// Operation → step mapping (varies by transition)
export const STEP_MAP = {
  'PENDING_RUNNING': { CREATE_EMPTY_ARCHIVE: 1, PROVISIONING: 2, STARTING: 3 },
  'ARCHIVED_RUNNING': { RESTORING: 1, STARTING: 2 },
  'STANDBY_RUNNING': { STARTING: 1 },
  'RUNNING_STANDBY': { STOPPING: 1 },
  'STANDBY_ARCHIVED': { ARCHIVING: 1 },
  'RUNNING_ARCHIVED': { STOPPING: 1, ARCHIVING: 2 },
};

// Inferred operation when WC hasn't triggered yet (operation=NONE but desired_state!=phase)
const INFERRED_OPERATION = {
  'STANDBY_ARCHIVED': 'ARCHIVING',
  'ARCHIVED_RUNNING': 'RESTORING',
  'STANDBY_RUNNING': 'STARTING',
  'RUNNING_STANDBY': 'STOPPING',
  'RUNNING_ARCHIVED': 'STOPPING',
  'PENDING_RUNNING': 'CREATE_EMPTY_ARCHIVE',
};

/**
 * Get progress info for a workspace transition
 * @param {string} startPhase - Phase when transition started
 * @param {string} desiredState - Target desired state
 * @param {string} operation - Current operation
 * @returns {object} { step, totalSteps, percent }
 */
export function getProgressInfo(startPhase, desiredState, operation) {
  const key = `${startPhase}_${desiredState}`;
  const totalSteps = TRANSITION_STEPS[key] || 2;
  const stepMap = STEP_MAP[key] || {};
  const step = stepMap[operation] || 1;
  return { step, totalSteps, percent: Math.round((step / totalSteps) * 100) };
}

// M2 Phase + Operation → Display Status mapping
export const STATUS_CONFIG = {
  // Stable states
  PENDING: { color: 'text-vscode-text', bgColor: 'bg-gray-600', icon: '○', label: 'Pending', canStart: true, canDelete: true },
  ARCHIVED: { color: 'text-vscode-text', bgColor: 'bg-gray-600', icon: '◇', label: 'Archived', canOpen: true, canDelete: true },
  STANDBY: { color: 'text-vscode-text', bgColor: 'bg-blue-600', icon: '◆', label: 'Standby', canOpen: true, canArchive: true, canDelete: true },
  RUNNING: { color: 'text-vscode-success', bgColor: 'bg-green-600', icon: '●', label: 'Running', canOpen: true, canPause: true },
  ERROR: { color: 'text-vscode-error', bgColor: 'bg-red-600', icon: '✕', label: 'Error', canRetry: true, canDelete: true },
  DELETING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Deleting...', isTransition: true },
  DELETED: { color: 'text-gray-500', bgColor: 'bg-gray-800', icon: '○', label: 'Deleted' },

  // Transitional states (operation != NONE)
  PROVISIONING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Provisioning...', isTransition: true },
  RESTORING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Restoring...', isTransition: true },
  STARTING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Starting...', isTransition: true },
  STOPPING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Pausing...', isTransition: true },
  ARCHIVING: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Archiving...', isTransition: true },
  CREATE_EMPTY_ARCHIVE: { color: 'text-vscode-warning', bgColor: 'bg-yellow-600', icon: '◐', label: 'Creating...', isTransition: true },
};

export const STATUS_ORDER = ['RUNNING', 'STARTING', 'STOPPING', 'STANDBY', 'ARCHIVING', 'RESTORING', 'PROVISIONING', 'ERROR', 'ARCHIVED', 'PENDING', 'DELETING', 'DELETED'];

/**
 * Get display status from workspace phase and operation (M2)
 * @param {object} workspace - Workspace object with phase, operation, desired_state
 * @returns {string} Display status key for STATUS_CONFIG
 */
export function getDisplayStatus(workspace) {
  const { phase, operation, desired_state } = workspace;

  // 1. If operation is active (not NONE), show operation as status
  if (operation && operation !== 'NONE') {
    return operation;
  }

  // 2. WC trigger pending: desired_state !== phase → infer operation
  if (desired_state && desired_state !== phase) {
    const key = `${phase}_${desired_state}`;
    const inferred = INFERRED_OPERATION[key];
    if (inferred) return inferred;
  }

  // 3. Stable state
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
