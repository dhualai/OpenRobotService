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
  PROJECT_IDS: 'profile_project_ids',
};

/**
 * 「手动登出」持久标记（sessionStorage）。
 *
 * 用途：登出后禁止微信静默 OAuth 自动登录、把用户固定在 /login。
 * - 模块级 loggingOut 标记只在当前 SPA 会话内存有效，刷新即复位；
 *   且 AuthGuard 的 redirectUnauthenticated 此前未识别它，登出瞬间仍会整页跳微信 OAuth。
 * - 改用 sessionStorage 持久化「已手动登出」意图：
 *   守卫与 Login 页据此跳过自动微信登录，停留在登录页由用户手动重新登录。
 * - sessionStorage 随微信 webview 关闭/新开会话复位：新会话首次访问仍可自动登录，
 *   仅在「同一会话内登出后」生效，符合「登出后固定在登录页」诉求。
 */
const MANUAL_LOGOUT_KEY = 'manual_logout';

/** 是否处于「手动登出」状态（同一会话内登出后、未重新登录前为 true） */
export function isManualLogout(): boolean {
  try {
    return sessionStorage.getItem(MANUAL_LOGOUT_KEY) === '1';
  } catch {
    return false;
  }
}

/** 拥有此权限的用户可查看全部项目和工单数据，不受「仅看自己关联项目」限制 */
export const PERMISSION_VIEW_ALL = 'backend:project:all';

export interface AuthState {
  isLoggedIn: boolean;
  username: string;
  name: string;
  avatarResourceId: number | null;
  token: string | null;
  isLoading: boolean;
  isAdmin: boolean;
  roles: Record<string, string[]> | null;
  permissions: string[];
  // 当前用户关联的项目 ID（取自 projectPermissions 的 keys），用于按项目过滤任务
  projectIds: string[];

  login: (authData: { access_token: string; refresh_token: string; expires_in: number }, user: string) => void;
  logout: () => void;
  refreshAuthToken: () => Promise<boolean>;
  fetchUserDetails: (user: string, authToken: string) => Promise<boolean>;
  checkLoginStatus: () => void;
  setProfile: (data: { name?: string; avatarResourceId?: number | null }) => void;
  hasPermission: (prefix: string) => boolean;
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
  permissions: [],
  projectIds: [],

  login: (authData, user) => {
    setLoggingOut(false);
    // 重新登录清除「手动登出」标记，恢复自动登录能力
    try { sessionStorage.removeItem(MANUAL_LOGOUT_KEY); } catch { /* SSR safe */ }
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
    // 标记「手动登出」：同一会话内禁止微信静默 OAuth 自动登录，固定在 /login
    try { sessionStorage.setItem(MANUAL_LOGOUT_KEY, '1'); } catch { /* SSR safe */ }
    set({
      token: null,
      username: '',
      name: '',
      avatarResourceId: null,
      isLoggedIn: false,
      isLoading: false,
      isAdmin: false,
      roles: null,
      permissions: [],
      projectIds: [],
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
      const userData = await request<{ roles?: { project_backend?: string[] }, name?: string, avatar_resource_id?: number | null, permissions?: string[], projectPermissions?: Record<string, unknown> }>(
        `/users/${user}/detail`
      );
      const projectRoles = userData.roles?.project_backend || [];
      const hasAdminRole = projectRoles.includes('admin');
      const name = userData.name || '';
      const avatarResourceId = userData.avatar_resource_id ?? null;
      const permissions = userData.permissions || [];
      // projectPermissions 的 keys 即当前用户关联的项目 ID（与 task.project_id 同源）
      const projectIds = Object.keys(userData.projectPermissions || {});
      set({
        roles: (userData.roles as Record<string, string[]>) || null,
        isAdmin: hasAdminRole,
        name,
        avatarResourceId,
        permissions,
        projectIds,
      });
      try {
        localStorage.setItem(STORAGE_KEYS.NAME, name);
        if (avatarResourceId === null) {
          localStorage.removeItem(STORAGE_KEYS.AVATAR_RESOURCE_ID);
        } else {
          localStorage.setItem(STORAGE_KEYS.AVATAR_RESOURCE_ID, String(avatarResourceId));
        }
        localStorage.setItem(STORAGE_KEYS.PROJECT_IDS, JSON.stringify(projectIds));
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
        const savedProjectIdsRaw = localStorage.getItem(STORAGE_KEYS.PROJECT_IDS);
        let savedProjectIds: string[] = [];
        try {
          savedProjectIds = savedProjectIdsRaw ? JSON.parse(savedProjectIdsRaw) : [];
        } catch { savedProjectIds = []; }
        set({
          token: savedToken,
          username: savedUsername,
          isLoggedIn: true,
          isLoading: false,
          name: savedName,
          avatarResourceId: savedAvatarId ? Number(savedAvatarId) : null,
          projectIds: savedProjectIds,
        });
        // 本地缓存先行展示，避免闪回微信ID；随后静默刷新最新的姓名/头像
        get().fetchUserDetails(savedUsername, savedToken);
        return;
      }
    } catch { /* SSR safe */ }
    set({ isLoading: false });
  },

  hasPermission: (prefix: string) => {
    const { permissions } = get();
    if (!permissions || permissions.length === 0) return false;
    return permissions.some(p => p.startsWith(prefix) || p === `${prefix}:*` || p === '*');
  },
}));
