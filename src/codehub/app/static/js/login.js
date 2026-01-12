/**
 * Login page JavaScript
 */

const loginForm = document.getElementById('login-form');
const loginBtn = document.getElementById('login-btn');
const errorMessage = document.getElementById('error-message');
const usernameInput = document.getElementById('username');
const passwordInput = document.getElementById('password');

/**
 * Show error message
 */
function showError(message) {
  errorMessage.textContent = message;
  errorMessage.classList.remove('hidden');
}

/**
 * Hide error message
 */
function hideError() {
  errorMessage.classList.add('hidden');
}

/**
 * Set loading state
 */
function setLoading(loading) {
  loginBtn.disabled = loading;
  loginBtn.textContent = loading ? 'Signing in...' : 'Sign In';
}

/**
 * Handle login form submission
 */
async function handleLogin(event) {
  event.preventDefault();
  hideError();

  const username = usernameInput.value.trim();
  const password = passwordInput.value;

  if (!username || !password) {
    showError('Please enter both username and password');
    return;
  }

  setLoading(true);

  try {
    const response = await fetch('/api/v1/login', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ username, password }),
    });

    if (response.ok) {
      // Redirect to dashboard on success
      window.location.href = '/';
    } else {
      const data = await response.json();
      const message = data.error?.message || 'Login failed';
      showError(message);
    }
  } catch (error) {
    console.error('Login error:', error);
    showError('Network error. Please try again.');
  } finally {
    setLoading(false);
  }
}

/**
 * Check if already logged in
 */
async function checkSession() {
  try {
    const response = await fetch('/api/v1/session');
    if (response.ok) {
      // Already logged in, redirect to dashboard
      window.location.href = '/';
    }
  } catch (error) {
    // Not logged in, stay on login page
  }
}

// Event listeners
loginForm.addEventListener('submit', handleLogin);

// Check session on page load
checkSession();
