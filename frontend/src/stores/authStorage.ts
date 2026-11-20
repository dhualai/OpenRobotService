// ======================================================================
// 认证/用户资料的 localStorage 环境命名空间隔离（authStorage）
// ----------------------------------------------------------------------
// 背景：测试（/t/app）与生产（/p/app）共用同一套 SPA、同一 origin（usp.ep-zl.com），
// localStorage 按 path 共享；旧代码 token 全部使用无前缀 key，导致用户在测试登录后
// 覆盖生产 token（或反向）——串过去的 token 打到对方环境后端返回 401，refresh 又失败，
// 形成「来回踢」。报错现象即：/t/api 上的 detail 401 + check-subscription 400。
//
// 治理：token 等敏感 key 追加环境前缀（t_/p_），各环境读写互不干扰；
// dev（base=/）前缀为空，key 与旧版完全一致，本地开发与现有单测零感知。
//
// 存量用户平滑（尽量无感）：当前环境 scoped key 缺失时回退读无前缀 legacy key，
// 并通过 GET /auth/me 探测该 legacy token 是否由当前环境签发：
//   - 是 → 整组静默迁移到 scoped key 并删除 legacy（生产正常用户升级后无感）；
//   - 否 → legacy 是「串环境的脏 token」，保留不删（避免破坏另一环境登录态），
//         本环境按「未登录」走既有引导登录流程（微信静默 OAuth / 账密页）。
// ======================================================================

import API_CONFIG, { ENV_PREFIX } from '@/config/api';

/** 环境命名空间：由 Vite base 推导（dev '/'; 构建 '/t/app/'、'/p/app/'），
 *  与 config/api.ts 的 API_ROOT 前缀同源。NS = '' | 't' | 'p' */
const NS = ENV_PREFIX.replace(/[^a-z0-9]/gi, '').toLowerCase();
/** key 前缀：''(dev) | 't_' | 'p_' */
const P = NS ? `${NS}_` : '';

/** 当前环境作用域下的认证/资料 key */
export const AUTH_STORAGE_KEYS = {
  AUTH_TOKEN: `${P}auth_token`,
  REFRESH_TOKEN: `${P}refresh_token`,
  TOKEN_EXPIRES_AT: `${P}token_expires_at`,
  USERNAME: `${P}username`,
  USER_ID: `${P}user_id`,
  NAME: `${P}profile_name`,
  AVATAR_RESOURCE_ID: `${P}profile_avatar_resource_id`,
  PROJECT_IDS: `${P}profile_project_ids`,
} as const;

/** 升级前的无前缀旧 key（跨环境仅一份）——只用于回退读取与一次性迁移 */
const LEGACY_KEYS = {
  AUTH_TOKEN: 'auth_token',
  REFRESH_TOKEN: 'refresh_token',
  TOKEN_EXPIRES_AT: 'token_expires_at',
  USERNAME: 'username',
  USER_ID: 'user_id',
  NAME: 'profile_name',
  AVATAR_RESOURCE_ID: 'profile_avatar_resource_id',
  PROJECT_IDS: 'profile_project_ids',
} as const;

type AuthKeyName = keyof typeof LEGACY_KEYS;

/** scoped key → legacy key 映射（用于回退读取与迁移复制） */
const KEY_PAIRS = (Object.keys(AUTH_STORAGE_KEYS) as AuthKeyName[]).map(
  (k) => [AUTH_STORAGE_KEYS[k], LEGACY_KEYS[k]] as const,
);

/** 当前环境是否已建立自己的认证数据（dev 下与 legacy 同名，存在即 true） */
export function hasScopedAuthSession(): boolean {
  try {
    return localStorage.getItem(AUTH_STORAGE_KEYS.AUTH_TOKEN) !== null;
  } catch {
    return false;
  }
}

/** 读取某项：优先当前环境 scoped key，缺失时回退 legacy key */
export function readStored(key: AuthKeyName): string | null {
  try {
    return localStorage.getItem(AUTH_STORAGE_KEYS[key]) ?? localStorage.getItem(LEGACY_KEYS[key]);
  } catch {
    return null;
  }
}

/** 写入某项：始终写当前环境 scoped key（不删 legacy，legacy 由迁移/登出流程统一清理） */
export function writeStored(key: AuthKeyName, value: string): void {
  try {
    localStorage.setItem(AUTH_STORAGE_KEYS[key], value);
  } catch {
    /* SSR safe */
  }
}

/** 删除某项：scoped 与 legacy 一并删除（登出/凭证失效场景） */
export function removeStored(key: AuthKeyName): void {
  try {
    localStorage.removeItem(AUTH_STORAGE_KEYS[key]);
    localStorage.removeItem(LEGACY_KEYS[key]);
  } catch {
    /* SSR safe */
  }
}

/** 清空全部认证/资料数据（scoped + legacy）——登出时调用 */
export function clearAllAuth(): void {
  try {
    for (const key of Object.values(AUTH_STORAGE_KEYS)) localStorage.removeItem(key);
    for (const key of Object.values(LEGACY_KEYS)) localStorage.removeItem(key);
  } catch {
    /* SSR safe */
  }
}

/** 持久化登录令牌（access/refresh/过期时间/可选 username）到当前环境 scoped key */
export function persistAuthTokens(
  accessToken: string,
  refreshToken: string,
  expiresAt: number,
  username?: string,
): void {
  writeStored('AUTH_TOKEN', accessToken);
  writeStored('REFRESH_TOKEN', refreshToken);
  writeStored('TOKEN_EXPIRES_AT', String(expiresAt));
  if (username !== undefined) writeStored('USERNAME', username);
}

/**
 * 一次性把 legacy（无前缀旧 key）迁移到当前环境 scoped key。
 * 仅在「当前环境无 scoped token、但存在 legacy token」时执行一次；
 * 用 GET /auth/me 探测 legacy token 归属，命中（2xx）才复制并删除 legacy。
 * 返回是否完成迁移；失败（串环境/过期/网络异常）返回 false 且不动 legacy。
 */
export async function migrateLegacyAuthAsync(): Promise<boolean> {
  try {
    if (hasScopedAuthSession()) return true; // dev（key 同名）或已迁移
    const legacyAccess = localStorage.getItem(LEGACY_KEYS.AUTH_TOKEN);
    if (!legacyAccess) return false;

    // 探测：legacy token 是否由当前环境后端签发（无效 → 401）
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    let ok = false;
    try {
      const res = await fetch(`${API_CONFIG.AUTH.BASE_URL}/me`, {
        headers: { Authorization: `Bearer ${legacyAccess}` },
        signal: controller.signal,
      });
      ok = res.ok;
    } finally {
      clearTimeout(timer);
    }
    if (!ok) return false;

    // 属于当前环境 → 整组复制到 scoped 后清理 legacy
    for (const [scopedKey, legacyKey] of KEY_PAIRS) {
      const value = localStorage.getItem(legacyKey);
      if (value !== null) localStorage.setItem(scopedKey, value);
    }
    for (const legacyKey of Object.values(LEGACY_KEYS)) localStorage.removeItem(legacyKey);
    return true;
  } catch {
    return false;
  }
}
