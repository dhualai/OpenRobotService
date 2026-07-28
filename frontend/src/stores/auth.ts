// Zustand 认证状态管理 - 合并 HelpDesk AuthContext + BackgroundService auth.js
import { create } from 'zustand';
import { createRequest, setToken as setApiToken, clearToken as clearApiToken, setLoggingOut } from '@/api/client';
import API_CONFIG from '@/config/api';

const STORAGE_KEYS = {
  AUTH_TOKEN: 'auth_token',
  REFRESH_TOKEN: 'refresh_token',
  TOKEN_EXPIRES_AT: 'token_expires_at',
  USERNAME: 'username',
  NAME: 'profile_name',
  AVATAR_RESOURCE_ID: 'profile_avatar_resource_id',
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
    setLoggingOut(false);
    const expiresAt = Date.now() + authData.expires_in * 1000;
    setApiToken(authData.access_token);
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
    // 登录后异步回填姓名/头像/角色，使 Navbar 头像在刷新/重登后持续展示
    void get().fetchUserDetails(user, authData.access_token);
  },

  logout: () => {
    // 先置登出标记，再清 token：在途请求 401 刷新失败时不抢跳转，
    // 由调用方 navigate('/login?reason=logout') 把页面停在登录页。
    setLoggingOut(true);
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
      const userData = await request<{ roles?: { project_backend?: string[] }, name?: string, avatar_resource_id?: number | null }>(
        `/users/${user}/detail`
      );
      const projectRoles = userData.roles?.project_backend || [];
      const hasAdminRole = projectRoles.includes('admin');
      const name = userData.name || '';
      const avatarResourceId = userData.avatar_resource_id ?? null;
      set({
        roles: (userData.roles as Record<string, string[]>) || null,
        isAdmin: hasAdminRole,
        name,
        avatarResourceId,
      });
      try {
        localStorage.setItem(STORAGE_KEYS.NAME, name);
        if (avatarResourceId === null) {
          localStorage.removeItem(STORAGE_KEYS.AVATAR_RESOURCE_ID);
        } else {
          localStorage.setItem(STORAGE_KEYS.AVATAR_RESOURCE_ID, String(avatarResourceId));
        }
      } catch { /* SSR safe */ }
      return hasAdminRole;
    } catch {
      set({ isAdmin: false });
      return false;
    }
  },

  setProfile: ({ name, avatarResourceId }) => {
    set((state) => ({
      name: name !== undefined ? name : state.name,
      avatarResourceId: avatarResourceId !== undefined ? avatarResourceId : state.avatarResourceId,
    }));
    try {
      if (name !== undefined) localStorage.setItem(STORAGE_KEYS.NAME, name);
      if (avatarResourceId !== undefined) {
        if (avatarResourceId === null) {
          localStorage.removeItem(STORAGE_KEYS.AVATAR_RESOURCE_ID);
        } else {
          localStorage.setItem(STORAGE_KEYS.AVATAR_RESOURCE_ID, String(avatarResourceId));
        }
      }
    } catch { /* SSR safe */ }
  },

  checkLoginStatus: () => {
    try {
      const savedToken = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
      const savedUsername = localStorage.getItem(STORAGE_KEYS.USERNAME);
      if (savedToken && savedUsername) {
        setApiToken(savedToken);
        const savedName = localStorage.getItem(STORAGE_KEYS.NAME) || '';
        const savedAvatarId = localStorage.getItem(STORAGE_KEYS.AVATAR_RESOURCE_ID);
        set({
          token: savedToken,
          username: savedUsername,
          isLoggedIn: true,
          isLoading: false,
          name: savedName,
          avatarResourceId: savedAvatarId ? Number(savedAvatarId) : null,
        });
        // 本地缓存先行展示，避免闪回微信ID；随后静默刷新最新的姓名/头像
        get().fetchUserDetails(savedUsername, savedToken);
        return;
      }
    } catch { /* SSR safe */ }
    set({ isLoading: false });
  },
}));
