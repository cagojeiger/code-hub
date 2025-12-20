/**
 * CodeHub API Module
 * All API communication functions
 */

import { API, PER_PAGE } from './state.js';

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
 * Fetch workspaces with pagination
 */
export async function fetchWorkspaces(page = 1) {
  const response = await fetchWithAuth(`${API}/workspaces?page=${page}&per_page=${PER_PAGE}`);
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
      memo: memo || null,
    }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to create workspace');
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
    throw new Error(error.error?.message || 'Failed to update workspace');
  }
  return response.json();
}

/**
 * Start a workspace
 */
export async function startWorkspace(id) {
  const response = await fetchWithAuth(`${API}/workspaces/${id}:start`, { method: 'POST' });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to start workspace');
  }
  return response.json();
}

/**
 * Stop a workspace
 */
export async function stopWorkspace(id) {
  const response = await fetchWithAuth(`${API}/workspaces/${id}:stop`, { method: 'POST' });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to stop workspace');
  }
  return response.json();
}

/**
 * Delete a workspace
 */
export async function deleteWorkspace(id) {
  const response = await fetchWithAuth(`${API}/workspaces/${id}`, { method: 'DELETE' });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error?.message || 'Failed to delete workspace');
  }
}
