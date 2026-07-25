// Zustand 认证状态管理 - 合并 HelpDesk AuthContext + BackgroundService auth.js
import { create } from 'zustand';
import { createRequest, setToken as setApiToken, clearToken as clearApiToken } from '@/api/client';
import API_CONFIG from '@/config/api';

const STORAGE_KEYS = {
  AUTH_TOKEN: 'auth_token',
  REFRESH_TOKEN: 'refresh_token',
  TOKEN_EXPIRES_AT: 'token_expires_at',
  USERNAME: 'username',
};

export interface AuthState {
  isLoggedIn: boolean;
  username: string;
  name: string;
  avatarResourceId: number | null;
  token: string | null;
  isLoading: boolean;
  isAdmin: boolean;
  roles: Record<string, string[]> | null;

  login: (authData: { access_token: string; refresh_token: string; expires_in: number }, user: string) => void;
  logout: () => void;
  refreshAuthToken: () => Promise<boolean>;
  fetchUserDetails: (user: string, authToken: string) => Promise<boolean>;
  checkLoginStatus: () => void;
  setProfile: (data: { name?: string; avatarResourceId?: number | null }) => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  isLoggedIn: false,
  username: '',
  name: '',
  avatarResourceId: null,
  token: null,
  isLoading: true,
  isAdmin: false,
  roles: null,

  login: (authData, user) => {
    const expiresAt = Date.now() + authData.expires_in * 1000;
    setApiToken(authData.access_token);
    console.log('[AuthStore] login: 设置username=', user, ', name暂为空');
    set({
      token: authData.access_token,
      username: user,
      isLoggedIn: true,
      isLoading: false,
    });
    localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, authData.access_token);
    localStorage.setItem(STORAGE_KEYS.REFRESH_TOKEN, authData.refresh_token);
    localStorage.setItem(STORAGE_KEYS.TOKEN_EXPIRES_AT, String(expiresAt));
    localStorage.setItem(STORAGE_KEYS.USERNAME, user);
  },

  logout: () => {
    clearApiToken();
    set({
      token: null,
      username: '',
      name: '',
      avatarResourceId: null,
      isLoggedIn: false,
      isLoading: false,
      isAdmin: false,
      roles: null,
    });
    Object.values(STORAGE_KEYS).forEach((key) => localStorage.removeItem(key));
  },

  refreshAuthToken: async () => {
    const storedRefreshToken = localStorage.getItem(STORAGE_KEYS.REFRESH_TOKEN);
    if (!storedRefreshToken) {
      get().logout();
      return false;
    }
    try {
      const response = await fetch(`${API_CONFIG.AUTH.BASE_URL}/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: storedRefreshToken }),
      });
      if (!response.ok) throw new Error('Token refresh failed');
      const data = await response.json();
      if (data.access_token) {
        const expiresAt = Date.now() + data.expires_in * 1000;
        setApiToken(data.access_token);
        set({ token: data.access_token });
        localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, data.access_token);
        localStorage.setItem(STORAGE_KEYS.REFRESH_TOKEN, data.refresh_token);
        localStorage.setItem(STORAGE_KEYS.TOKEN_EXPIRES_AT, String(expiresAt));
        return true;
      }
      throw new Error('No access token in refresh response');
    } catch {
      get().logout();
      return false;
    }
  },

  fetchUserDetails: async (user, authToken) => {
    const request = createRequest(API_CONFIG.ADMIN.BASE_URL, '用户中心');
    try {
      setApiToken(authToken);
      console.log('[AuthStore] fetchUserDetails: 开始获取用户详情, user=', user);
      const userData = await request<{ roles?: { project_backend?: string[] }, name?: string, avatar_resource_id?: number | null }>(
        `/users/${user}/detail`
      );
      console.log('[AuthStore] fetchUserDetails: 获取成功, name="', userData.name, '", roles=', JSON.stringify(userData.roles));
      const projectRoles = userData.roles?.project_backend || [];
      const hasAdminRole = projectRoles.includes('admin');
      set({
        roles: (userData.roles as Record<string, string[]>) || null,
        isAdmin: hasAdminRole,
        name: userData.name || '',
        avatarResourceId: userData.avatar_resource_id ?? null,
      });
      return hasAdminRole;
    } catch (e) {
      console.error('[AuthStore] fetchUserDetails: 获取失败', e);
      set({ isAdmin: false });
      return false;
    }
  },

  setProfile: ({ name, avatarResourceId }) => {
    set((state) => ({
      name: name !== undefined ? name : state.name,
      avatarResourceId: avatarResourceId !== undefined ? avatarResourceId : state.avatarResourceId,
    }));
  },

  checkLoginStatus: () => {
    try {
      const savedToken = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
      const savedUsername = localStorage.getItem(STORAGE_KEYS.USERNAME);
      console.log('[AuthStore] checkLoginStatus: token=', !!savedToken, ', username="', savedUsername, '"');
      if (savedToken && savedUsername) {
        setApiToken(savedToken);
        console.log('[AuthStore] checkLoginStatus: 恢复登录状态, username=', savedUsername, ', name暂为空(需要后续fetchUserDetails)');
        set({
          token: savedToken,
          username: savedUsername,
          isLoggedIn: true,
          isLoading: false,
        });
        return;
      }
    } catch { /* SSR safe */ }
    set({ isLoading: false });
  },
}));
