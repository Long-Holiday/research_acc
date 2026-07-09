/**
 * Authentication Module
 * Handles password verification and session management for the application
 * by communicating with the backend APIs.
 *
 * @module Auth
 */

const Auth = {
    // Cache for isPasswordEnabled check result
    _passwordEnabled: undefined,

    /**
     * Helper function to call API with Auth token injection & 401 handling.
     *
     * @param {string|Request} url - The URL or Request object to fetch
     * @param {Object} [options={}] - Fetch options
     * @returns {Promise<Response>} The fetch Response
     */
    async fetchWithAuth(url, options = {}) {
        const token = localStorage.getItem('arxiv_auth_token');
        options.headers = options.headers || {};
        
        if (token) {
            if (options.headers instanceof Headers) {
                options.headers.set('Authorization', `Bearer ${token}`);
            } else if (typeof options.headers.set === 'function') {
                options.headers.set('Authorization', `Bearer ${token}`);
            } else {
                options.headers['Authorization'] = `Bearer ${token}`;
            }
        }
        
        const response = await fetch(url, options);
        if (response.status === 401) {
            localStorage.removeItem('arxiv_auth_token');
            localStorage.removeItem('arxiv_auth_expire');
            const currentPage = window.location.pathname.split('/').pop() || 'index.html';
            if (currentPage !== 'login.html') {
                window.location.href = `login.html?redirect=${currentPage}`;
            }
        }
        return response;
    },

    /**
     * Authenticate user with password using the backend API.
     *
     * @param {string} password - User input password
     * @param {boolean} [remember=true] - Whether to remember login (currently token expire is set by backend)
     * @returns {Promise<boolean>} True if authentication successful
     */
    async login(password, remember = true) {
        try {
            const response = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password })
            });
            if (!response.ok) {
                return false;
            }
            const data = await response.json();
            localStorage.setItem('arxiv_auth_token', data.token);
            localStorage.setItem('arxiv_auth_expire', data.expire.toString());
            return true;
        } catch (e) {
            console.error('Login request failed', e);
            return false;
        }
    },

    /**
     * Check if user is authenticated locally based on stored token and expiration.
     *
     * @returns {boolean} True if valid session exists
     */
    isAuthenticated() {
        const token = localStorage.getItem('arxiv_auth_token');
        const expireTime = localStorage.getItem('arxiv_auth_expire');

        if (!token || !expireTime) {
            return false;
        }

        const now = Date.now();
        if (now > parseInt(expireTime) && parseInt(expireTime) !== 0) {
            this.logout();
            return false;
        }

        return true;
    },

    /**
     * Logout user and redirect to login page.
     */
    logout() {
        localStorage.removeItem('arxiv_auth_token');
        localStorage.removeItem('arxiv_auth_expire');
        window.location.href = 'login.html';
    },

    /**
     * Check if password protection is enabled on the backend.
     * Caches the result to avoid repeated network requests.
     *
     * @returns {Promise<boolean>} True if password is required
     */
    async isPasswordEnabled() {
        if (this._passwordEnabled !== undefined) {
            return this._passwordEnabled;
        }
        try {
            const response = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: "" })
            });
            this._passwordEnabled = response.status !== 200; // If empty password succeeds, then password is not required
            return this._passwordEnabled;
        } catch(e) {
            return true;
        }
    },

    /**
     * Require authentication (call on protected pages).
     * Redirects to login page if not authenticated.
     */
    async requireAuth() {
        const enabled = await this.isPasswordEnabled();
        if (!enabled) {
            return;
        }
        if (!this.isAuthenticated()) {
            const currentPage = window.location.pathname.split('/').pop() || 'index.html';
            window.location.href = `login.html?redirect=${currentPage}`;
        }
    },

    /**
     * Get remaining session time in milliseconds.
     *
     * @returns {number} Milliseconds until session expires
     */
    getSessionTimeLeft() {
        const expireTime = localStorage.getItem('arxiv_auth_expire');
        if (!expireTime) return 0;
        return Math.max(0, parseInt(expireTime) - Date.now());
    },

    /**
     * Format session time for display.
     *
     * @returns {string} Human-readable time left
     */
    getSessionTimeLeftFormatted() {
        const ms = this.getSessionTimeLeft();
        const days = Math.floor(ms / (24 * 60 * 60 * 1000));
        const hours = Math.floor((ms % (24 * 60 * 60 * 1000)) / (60 * 60 * 1000));

        if (days > 0) {
            return `${days} day${days > 1 ? 's' : ''} ${hours} hour${hours > 1 ? 's' : ''}`;
        } else if (hours > 0) {
            const minutes = Math.floor((ms % (60 * 60 * 1000)) / (60 * 1000));
            return `${hours} hour${hours > 1 ? 's' : ''} ${minutes} min`;
        } else {
            const minutes = Math.floor(ms / (60 * 1000));
            return `${minutes} minute${minutes > 1 ? 's' : ''}`;
        }
    },

    /**
     * Get session expiration date.
     *
     * @returns {Date|null} Expiration date or null if not authenticated
     */
    getSessionExpireDate() {
        const expireTime = localStorage.getItem('arxiv_auth_expire');
        if (!expireTime) return null;
        return new Date(parseInt(expireTime));
    }
};

// Expose fetchWithAuth globally as well for convenience
window.fetchWithAuth = Auth.fetchWithAuth.bind(Auth);
