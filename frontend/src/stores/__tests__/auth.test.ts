import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useAuthStore } from '../auth';

describe('Auth Store', () => {
  beforeEach(() => {
    // Reset store to initial state
    useAuthStore.setState({
      isLoggedIn: false,
      username: '',
      token: null,
      isLoading: true,
      isAdmin: false,
      roles: null,
    });
    localStorage.clear();
    vi.clearAllMocks();
  });

  describe('checkLoginStatus', () => {
    it('should set isLoading to false when no saved token', () => {
      useAuthStore.getState().checkLoginStatus();
      const state = useAuthStore.getState();
      expect(state.isLoading).toBe(false);
      expect(state.isLoggedIn).toBe(false);
    });

    it('should restore login state from localStorage', () => {
      localStorage.setItem('auth_token', 'saved-token');
      localStorage.setItem('username', 'testuser');

      useAuthStore.getState().checkLoginStatus();

      const state = useAuthStore.getState();
      expect(state.isLoggedIn).toBe(true);
      expect(state.username).toBe('testuser');
      expect(state.token).toBe('saved-token');
      expect(state.isLoading).toBe(false);
    });
  });

  describe('login', () => {
    it('should set login state and save to localStorage', () => {
      const authData = {
        access_token: 'access-123',
        refresh_token: 'refresh-456',
        expires_in: 3600,
      };

      useAuthStore.getState().login(authData, 'testuser');

      const state = useAuthStore.getState();
      expect(state.isLoggedIn).toBe(true);
      expect(state.username).toBe('testuser');
      expect(state.token).toBe('access-123');
      expect(state.isLoading).toBe(false);

      expect(localStorage.getItem('auth_token')).toBe('access-123');
      expect(localStorage.getItem('refresh_token')).toBe('refresh-456');
      expect(localStorage.getItem('username')).toBe('testuser');
      expect(localStorage.getItem('token_expires_at')).toBeTruthy();
    });

    it('should calculate correct expires_at', () => {
      const before = Date.now();
      const authData = {
        access_token: 'access-123',
        refresh_token: 'refresh-456',
        expires_in: 1800,
      };

      useAuthStore.getState().login(authData, 'testuser');

      const expiresAt = Number(localStorage.getItem('token_expires_at'));
      expect(expiresAt).toBeGreaterThan(before);
      expect(expiresAt).toBeLessThanOrEqual(before + 1800 * 1000 + 100); // tolerance
    });
  });

  describe('logout', () => {
    it('should clear all auth state and localStorage', () => {
      // First login
      useAuthStore.getState().login(
        { access_token: 't1', refresh_token: 'r1', expires_in: 3600 },
        'user'
      );

      // Then logout
      useAuthStore.getState().logout();

      const state = useAuthStore.getState();
      expect(state.isLoggedIn).toBe(false);
      expect(state.username).toBe('');
      expect(state.token).toBeNull();
      expect(state.isAdmin).toBe(false);
      expect(state.roles).toBeNull();

      expect(localStorage.getItem('auth_token')).toBeNull();
      expect(localStorage.getItem('refresh_token')).toBeNull();
      expect(localStorage.getItem('username')).toBeNull();
      expect(localStorage.getItem('token_expires_at')).toBeNull();
    });
  });

  describe('refreshAuthToken', () => {
    it('should logout when no refresh token stored', async () => {
      const result = await useAuthStore.getState().refreshAuthToken();
      expect(result).toBe(false);
      expect(useAuthStore.getState().isLoggedIn).toBe(false);
    });

    it('should refresh token successfully', async () => {
      localStorage.setItem('refresh_token', 'old-refresh');

      const mockResponse = {
        access_token: 'new-access',
        refresh_token: 'new-refresh',
        expires_in: 7200,
      };

      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: true,
        json: async () => mockResponse,
      } as Response);

      const result = await useAuthStore.getState().refreshAuthToken();

      expect(result).toBe(true);
      expect(useAuthStore.getState().token).toBe('new-access');
      expect(localStorage.getItem('auth_token')).toBe('new-access');
      expect(localStorage.getItem('refresh_token')).toBe('new-refresh');
    });

    it('should logout when refresh fails', async () => {
      localStorage.setItem('refresh_token', 'old-refresh');

      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: false,
        json: async () => ({}),
        status: 401,
      } as Response);

      const result = await useAuthStore.getState().refreshAuthToken();

      expect(result).toBe(false);
      expect(useAuthStore.getState().isLoggedIn).toBe(false);
    });

    it('should logout when no access_token in response', async () => {
      localStorage.setItem('refresh_token', 'old-refresh');

      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: true,
        json: async () => ({ refresh_token: 'new-refresh' }),
      } as Response);

      const result = await useAuthStore.getState().refreshAuthToken();
      expect(result).toBe(false);
    });
  });

  describe('fetchUserDetails', () => {
    it('should set isAdmin when user has admin role', async () => {
      const userData = {
        roles: {
          project_backend: ['admin', 'user'],
        },
      };

      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: true,
        json: async () => userData,
      } as Response);

      setApiTokenBehind('auth-token');
      const result = await useAuthStore.getState().fetchUserDetails('admin-user', 'auth-token');

      expect(result).toBe(true);
      expect(useAuthStore.getState().isAdmin).toBe(true);
    });

    it('should set isAdmin false when user has no admin role', async () => {
      const userData = {
        roles: {
          project_backend: ['user'],
        },
      };

      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
        ok: true,
        json: async () => userData,
      } as Response);

      const result = await useAuthStore.getState().fetchUserDetails('normal-user', 'auth-token');

      expect(result).toBe(false);
      expect(useAuthStore.getState().isAdmin).toBe(false);
    });

    it('should set isAdmin false on fetch error', async () => {
      vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('Network error'));

      const result = await useAuthStore.getState().fetchUserDetails('error-user', 'auth-token');

      expect(result).toBe(false);
      expect(useAuthStore.getState().isAdmin).toBe(false);
    });
  });
});

// helper to inject token into the api client module without import loop
function setApiTokenBehind(token: string) {
  // Use initToken internal mechanism
  localStorage.clear();
  localStorage.setItem('auth_token', token);
}
