/**
 * Proxy status page utilities
 * Uses polling to check workspace state
 * Progress calculated in FE (same logic as state.js)
 */

// Total steps per transition type
const TRANSITION_STEPS = {
  'PENDING_RUNNING': 4,
  'ARCHIVED_RUNNING': 3,
  'STANDBY_RUNNING': 2,
  'RUNNING_STANDBY': 2,
  'STANDBY_ARCHIVED': 2,
  'RUNNING_ARCHIVED': 3,
};

// Operation → step mapping
const STEP_MAP = {
  'PENDING_RUNNING': { CREATE_EMPTY_ARCHIVE: 1, PROVISIONING: 2, STARTING: 3 },
  'ARCHIVED_RUNNING': { RESTORING: 1, STARTING: 2 },
  'STANDBY_RUNNING': { STARTING: 1 },
  'RUNNING_STANDBY': { STOPPING: 1 },
  'STANDBY_ARCHIVED': { ARCHIVING: 1 },
  'RUNNING_ARCHIVED': { STOPPING: 1, ARCHIVING: 2 },
};

// Operation labels
const OPERATION_LABELS = {
  CREATE_EMPTY_ARCHIVE: 'Creating...',
  PROVISIONING: 'Provisioning...',
  RESTORING: 'Restoring...',
  STARTING: 'Starting...',
  STOPPING: 'Pausing...',
  ARCHIVING: 'Archiving...',
};

/**
 * Calculate progress for proxy page
 * @param {string} startPhase - Phase when transition started
 * @param {string} desiredState - Target desired state
 * @param {string} operation - Current operation
 * @returns {object|null} { step, total_steps, label, percent }
 */
function calculateProgress(startPhase, desiredState, operation) {
  if (!operation || operation === 'NONE') return null;

  const key = `${startPhase}_${desiredState}`;
  const totalSteps = TRANSITION_STEPS[key] || 2;
  const stepMap = STEP_MAP[key] || {};
  const step = stepMap[operation] || 1;
  const percent = Math.round((step / totalSteps) * 100);
  const label = OPERATION_LABELS[operation] || 'Processing...';

  return { step, total_steps: totalSteps, label, percent };
}

const ProxyPage = {
  // Track transition start phase for consistent progress calculation
  _transitionState: null,

  /**
   * Poll workspace status and redirect when RUNNING
   * @param {string} workspaceId - Workspace ID to monitor
   * @param {string} statusElementId - Element ID to update status text
   * @param {number} intervalMs - Polling interval in milliseconds (default: 500)
   */
  pollStatus(workspaceId, statusElementId, intervalMs = 500) {
    const statusEl = document.getElementById(statusElementId);
    const progressBarEl = document.getElementById('progress-bar');
    const progressStepEl = document.getElementById('progress-step');
    if (!statusEl) return;

    statusEl.textContent = 'Preparing...';

    const updateProgress = (progress, desiredState) => {
      if (!progress) return;

      const { step, total_steps, label, percent } = progress;

      // Update status text with label
      statusEl.textContent = label;

      // Update progress bar if element exists
      if (progressBarEl) {
        progressBarEl.style.width = `${percent}%`;
      }

      // Update step indicator if element exists (with target state)
      if (progressStepEl) {
        const targetLabel = desiredState ? ` → ${desiredState}` : '';
        progressStepEl.textContent = `${step}/${total_steps}${targetLabel}`;
      }
    };

    const poll = async () => {
      try {
        const res = await fetch(`/api/v1/workspaces/${workspaceId}`, {
          credentials: 'include'
        });

        if (!res.ok) {
          statusEl.textContent = 'Error checking status. Retrying...';
          setTimeout(poll, intervalMs);
          return;
        }

        const ws = await res.json();

        if (ws.phase === 'RUNNING') {
          statusEl.textContent = 'Workspace ready! Redirecting...';
          if (progressBarEl) progressBarEl.style.width = '100%';
          if (progressStepEl) {
            const key = `${this._transitionState?.startPhase || ws.phase}_${ws.desired_state}`;
            const totalSteps = TRANSITION_STEPS[key] || 2;
            progressStepEl.textContent = `${totalSteps}/${totalSteps}`;
          }
          setTimeout(() => {
            window.location.href = `/w/${workspaceId}/`;
          }, 500);
        } else if (ws.phase === 'ERROR') {
          statusEl.textContent = `Error: ${ws.error_reason || 'Unknown error'}`;
        } else {
          // Track transition start phase
          if (!this._transitionState || this._transitionState.desiredState !== ws.desired_state) {
            this._transitionState = { startPhase: ws.phase, desiredState: ws.desired_state };
          }

          // Calculate progress in FE
          const progress = calculateProgress(
            this._transitionState.startPhase,
            ws.desired_state,
            ws.operation
          );

          if (progress) {
            updateProgress(progress, ws.desired_state);
          } else {
            statusEl.textContent = 'Preparing...';
          }
          setTimeout(poll, intervalMs);
        }
      } catch (err) {
        console.error('Poll error:', err);
        statusEl.textContent = 'Connection error. Retrying...';
        setTimeout(poll, intervalMs);
      }
    };

    // Start polling
    poll();
  }
};
