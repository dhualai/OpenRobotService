// Zustand 认证状态管理 - 合并 HelpDesk AuthContext + BackgroundService auth.js
import { create } from 'zustand';
import { createRequest, setToken as setApiToken, clearToken as clearApiToken, setLoggingOut } from '@/api/client';
import API_CONFIG from '@/config/api';
import {
  clearAllAuth,
  hasScopedAuthSession,
  migrateLegacyAuthAsync,
  persistAuthTokens,
  readStored,
  removeStored,
  writeStored,
} from '@/stores/authStorage';

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
  userId: string;
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
  userId: '',
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
    // 写入「当前环境命名空间」的 localStorage key（t_/p_），避免与另一环境 token 串用
    persistAuthTokens(authData.access_token, authData.refresh_token, expiresAt, user);
    set({
      token: authData.access_token,
      username: user,
      isLoggedIn: true,
      isLoading: false,
    });
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
    // 清空当前环境 + legacy 全部认证/资料（登出即彻底清除，恢复旧版「全清」语义）
    clearAllAuth();
    set({
      token: null,
      username: '',
      userId: '',
      name: '',
      avatarResourceId: null,
      isLoggedIn: false,
      isLoading: false,
      isAdmin: false,
      roles: null,
      permissions: [],
      projectIds: [],
    });
  },

  refreshAuthToken: async () => {
    // 优先读当前环境 scoped key，未迁移前回退 legacy key
    const storedRefreshToken = readStored('REFRESH_TOKEN');
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
        persistAuthTokens(data.access_token, data.refresh_token, expiresAt);
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
      const userData = await request<{ id?: string; roles?: { project_backend?: string[] }, name?: string, avatar_resource_id?: number | null, permissions?: string[], projectPermissions?: Record<string, unknown> }>(
        `/users/${user}/detail`
      );
      const projectRoles = userData.roles?.project_backend || [];
      const hasAdminRole = projectRoles.includes('admin');
      const name = userData.name || '';
      const avatarResourceId = userData.avatar_resource_id ?? null;
      const permissions = userData.permissions || [];
      const userId = userData.id || '';
      // projectPermissions 的 keys 即当前用户关联的项目 ID（与 task.project_id 同源）
      const projectIds = Object.keys(userData.projectPermissions || {});
      set({
        roles: (userData.roles as Record<string, string[]>) || null,
        isAdmin: hasAdminRole,
        name,
        avatarResourceId,
        permissions,
        projectIds,
        userId,
      });
      try {
        if (userId) writeStored('USER_ID', userId);
        else removeStored('USER_ID');
        writeStored('NAME', name);
        if (avatarResourceId === null) {
          removeStored('AVATAR_RESOURCE_ID');
        } else {
          writeStored('AVATAR_RESOURCE_ID', String(avatarResourceId));
        }
        writeStored('PROJECT_IDS', JSON.stringify(projectIds));
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
      if (name !== undefined) writeStored('NAME', name);
      if (avatarResourceId !== undefined) {
        if (avatarResourceId === null) {
          removeStored('AVATAR_RESOURCE_ID');
        } else {
          writeStored('AVATAR_RESOURCE_ID', String(avatarResourceId));
        }
      }
    } catch { /* SSR safe */ }
  },

  checkLoginStatus: () => {
    try {
      // 读当前环境 scoped key；升级前的老用户（无 scoped）回退 legacy key 乐观恢复，
      // 随后异步探测并迁移（见 migrateLegacyAuthAsync），生产存量用户升级后无感。
      const savedToken = readStored('AUTH_TOKEN');
      const savedUsername = readStored('USERNAME');
      if (savedToken && savedUsername) {
        setApiToken(savedToken);
        const savedName = readStored('NAME') || '';
        const savedUserId = readStored('USER_ID') || '';
        const savedAvatarId = readStored('AVATAR_RESOURCE_ID');
        const savedProjectIdsRaw = readStored('PROJECT_IDS');
        let savedProjectIds: string[] = [];
        try {
          savedProjectIds = savedProjectIdsRaw ? JSON.parse(savedProjectIdsRaw) : [];
        } catch { savedProjectIds = []; }
        set({
          token: savedToken,
          username: savedUsername,
          userId: savedUserId,
          isLoggedIn: true,
          isLoading: false,
          name: savedName,
          avatarResourceId: savedAvatarId ? Number(savedAvatarId) : null,
          projectIds: savedProjectIds,
        });
        // 仅当读到的 token 来自 legacy（当前环境尚未建立自己的 key）时触发迁移；
        // /auth/me 探测命中才落盘，串环境的脏 token 不迁移、也不影响另一环境。
        if (!hasScopedAuthSession()) void migrateLegacyAuthAsync();
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
    // admin 通配权限：与后端 require_permission 的「permissions 含 admin 直通」对齐，
    // 否则 admin 用户（permissions=['admin']）在前端看不到「其他」等按权限码控制的入口
    if (permissions.includes('admin')) return true;
    return permissions.some(p => p.startsWith(prefix) || p === `${prefix}:*` || p === '*');
  },
}));
