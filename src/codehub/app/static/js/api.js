/**
 * CodeHub API Module (M2)
 * All API communication functions
 */

import { API, LIMIT } from './state.js';

/**
 * Redirect to login page
 */
export function redirectToLogin() {
  window.location.href = '/login';
}

/**
 * Fetch wrapper that handles auth errors
 */
export async function fetchWithAuth(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    redirectToLogin();
    throw new Error('Session expired');
  }
  return response;
}

/**
 * Check current session
 */
export async function checkSession() {
  try {
    const response = await fetch(`${API}/session`);
    if (response.ok) {
      const data = await response.json();
      document.getElementById('username-display').textContent = data.username;
      return true;
    }
    return false;
  } catch (error) {
    return false;
  }
}

/**
 * Logout current user
 */
export async function logout() {
  try {
    await fetch(`${API}/logout`, { method: 'POST' });
  } catch (error) {
    console.error('Logout error:', error);
  }
  window.location.href = '/login';
}

/**
 * Fetch workspaces with pagination (M2: uses limit/offset)
 */
export async function fetchWorkspaces(offset = 0) {
  const response = await fetchWithAuth(`${API}/workspaces?limit=${LIMIT}&offset=${offset}`);
  if (!response.ok) throw new Error('Failed to fetch workspaces');
  return response.json();
}

/**
 * Create a new workspace
 */
export async function createWorkspace(name, description, memo) {
  const response = await fetchWithAuth(`${API}/workspaces`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name,
      description: description || null,
    }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to create workspace');
  }
  return response.json();
}

/**
 * Update workspace properties
 */
export async function updateWorkspace(id, data) {
  const response = await fetchWithAuth(`${API}/workspaces/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to update workspace');
  }
  return response.json();
}

/**
 * Start a workspace (M2: set desired_state to RUNNING)
 */
export async function startWorkspace(id) {
  return updateWorkspace(id, { desired_state: 'RUNNING' });
}

/**
 * Pause a workspace (M2: set desired_state to STANDBY)
 * Preserves volume for quick restart
 */
export async function pauseWorkspace(id) {
  return updateWorkspace(id, { desired_state: 'STANDBY' });
}

/**
 * Archive a workspace (M2: set desired_state to ARCHIVED)
 * Moves volume to S3, deletes local volume
 */
export async function archiveWorkspace(id) {
  return updateWorkspace(id, { desired_state: 'ARCHIVED' });
}

/**
 * Delete a workspace (M2: soft delete via API)
 */
export async function deleteWorkspace(id) {
  const response = await fetchWithAuth(`${API}/workspaces/${id}`, { method: 'DELETE' });
  if (!response.ok && response.status !== 204) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to delete workspace');
  }
}

/**
 * Get a single workspace by ID
 */
export async function getWorkspace(id) {
  const response = await fetchWithAuth(`${API}/workspaces/${id}`);
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to get workspace');
  }
  return response.json();
}
