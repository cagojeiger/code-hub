/**
 * CodeHub Utility Functions
 * Common helper functions used across the application
 */

/**
 * Show a toast notification
 */
export function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');

  const bgColor = type === 'error' ? 'bg-vscode-error' :
                  type === 'success' ? 'bg-vscode-success' : 'bg-vscode-accent';

  toast.className = `${bgColor} text-white px-4 py-2 rounded shadow-lg transform transition-all duration-300 animate-fade-in`;
  toast.textContent = message;

  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('opacity-0', 'translate-x-full');
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

/**
 * Escape HTML entities to prevent XSS
 */
export function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/**
 * Format date as relative time (e.g., "5m ago", "2h ago")
 */
export function formatDate(dateString) {
  if (!dateString) return '-';
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;

  return date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Format date as compact relative time (no "ago" suffix)
 */
export function formatShortDate(dateString) {
  if (!dateString) return '-';
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'now';
  if (diffMins < 60) return `${diffMins}m`;
  if (diffHours < 24) return `${diffHours}h`;
  if (diffDays < 30) return `${diffDays}d`;

  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/**
 * Update footer statistics display
 */
export function updateFooterStats(workspaces) {
  const total = workspaces.length;
  const running = workspaces.filter(w => w.phase === 'RUNNING' && w.operation === 'NONE').length;
  const stopped = workspaces.filter(w =>
    ['ARCHIVED', 'STANDBY', 'PENDING'].includes(w.phase) && w.operation === 'NONE'
  ).length;
  const error = workspaces.filter(w => w.phase === 'ERROR').length;

  document.getElementById('stat-total').textContent = total;
  document.getElementById('stat-running').textContent = running;
  document.getElementById('stat-stopped').textContent = stopped;
  document.getElementById('stat-error').textContent = error;
}

/**
 * Update footer date/time display
 */
export function updateFooterDate() {
  const now = new Date();
  const dateStr = now.toLocaleDateString('ko-KR', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  });
  const timeStr = now.toLocaleTimeString('ko-KR', {
    hour: '2-digit',
    minute: '2-digit',
  });
  document.getElementById('footer-date').textContent = `${dateStr} ${timeStr}`;
}
