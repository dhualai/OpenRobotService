// URL 工具函数 - 从 HelpDesk urlUtils.js 移植
import { WECHAT_CONFIG } from '@/config/wechat';

const STORAGE_KEYS = {
  AUTH_TOKEN: 'auth_token',
  REFRESH_TOKEN: 'refresh_token',
  TOKEN_EXPIRES_AT: 'token_expires_at',
  USERNAME: 'username',
};

export function getUrlParams(): boolean {
  try {
    const url = new URL(window.location.href);
    return url.searchParams.get('debug') === 'true';
  } catch {
    return false;
  }
}

function storeTokens(token: string, refreshToken: string): void {
  localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, token);
  localStorage.setItem(STORAGE_KEYS.REFRESH_TOKEN, refreshToken);
  const expirationTime = Date.now() + 28 * 60 * 1000;
  localStorage.setItem(STORAGE_KEYS.TOKEN_EXPIRES_AT, String(expirationTime));
}

export function checkUrlTokens(): string | null {
  try {
    const searchParams = new URLSearchParams(window.location.search);
    const token = searchParams.get('token');
    const refreshToken = searchParams.get('refresh_token');

    if (token) {
      storeTokens(token, refreshToken || '');
      try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        const username = payload.sub || '';
        if (username) {
          localStorage.setItem(STORAGE_KEYS.USERNAME, username);
        }
      } catch { /* JWT parse error */ }
      const cleanUrl = window.location.pathname;
      window.location.href = cleanUrl;
      return token;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * 将完整目标 URL 编码为微信 OAuth `state` 安全字符串（base64url，字符集 A-Za-z0-9-_）。
 * 微信 state 仅接受有限字符且长度 <=128，base64url 全部落在安全集内、无需二次转义。
 * 采用 UTF-8 安全编码，兼容路径中可能出现的非 ASCII 字符。
 */
export function encodeWechatState(fullUrl: string): string {
  const b64 = btoa(unescape(encodeURIComponent(fullUrl)));
  return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * 由 React Router 的路由路径（不含部署前缀，如 `/app/admin/wechat`）构造微信 `state`。
 * 拼上 `origin` 与 Vite 部署前缀（`import.meta.env.BASE_URL`，如 `/p/app`），得到浏览器
 * 可直接访问的**完整地址**再 base64url 编码。后端 `/wechat/callback` 解码后原样回跳，
 * 从根本上避免旧方案（仅传路径、后端用 netloc 重拼）丢失 `/p/app` 前缀导致的 404。
 */
export function buildStateFromPath(routerPath: string): string {
  const base = import.meta.env.BASE_URL.replace(/\/$/, ''); // '/p/app' | '/t/app' | ''
  const fullUrl = `${window.location.origin}${base}${routerPath}`;
  return encodeWechatState(fullUrl);
}

export function buildWechatAuthUrl(state: string): string {
  // 优先用显式配置的完整回调地址；未配置则按 origin + redirectPath 自动推导
  const redirectUri = WECHAT_CONFIG.redirectUri || `${window.location.origin}${WECHAT_CONFIG.redirectPath}`;
  const baseUrl = 'https://open.weixin.qq.com/connect/oauth2/authorize';
  const params = new URLSearchParams({
    appid: WECHAT_CONFIG.appId,
    redirect_uri: redirectUri,
    response_type: 'code',
    scope: WECHAT_CONFIG.oauthScope,
    state: state || 'STATE',
  });
  return `${baseUrl}?${params.toString()}#wechat_redirect`;
}

/**
 * 把后端时间字符串解析为本地时区 Date。
 *
 * 后端在数据库引擎层已强制每个连接的会话时区为 UTC（见 backend/app/core/db.py 的
 * `_ensure_utc_session`），因此 ``func.now()`` 一律返回 UTC 时间，本地/生产一致。
 *
 * - 无时区 ISO 字符串（如 "2026-08-15T07:55:55"）：来自 naive DateTime 列，存储的是 UTC，
 *   补 ``Z`` 标记为 UTC 后由浏览器按本地时区自动 +8 转换。
 * - 已带时区（``Z`` / ``±HH:MM``）的字符串：原样解析（aware datetime 经 pydantic 序列化输出）。
 *
 * 不写死 +8，跨时区浏览器同样正确。
 */
export function parseUtcDate(dateString: string): Date | null {
  if (!dateString) return null;
  let s = String(dateString).trim().replace(' ', 'T');
  const hasTz = /([+-]\d{2}:?\d{2}|Z)$/.test(s);
  if (!hasTz && /T\d{2}:\d{2}/.test(s)) s += 'Z';
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

/**
 * 格式化 deadline_at 等字段：与 parseUtcDate 语义一致——DB 存 naive UTC，
 * 无时区后缀时补 Z 当 UTC 解析，由浏览器按本地时区自动 +8 转换。
 *
 * 修复背景（2026-08-25）：原实现直接提取年月日时分显示，假设 naive = 上海时间。
 * c2ebf96 时区根因治理后，DB 已统一存 UTC（naive DateTime 存 UTC 值），
 * 写入侧（convert_to_shanghai_time：aware→转 UTC 剥时区）存的是 UTC naive，
 * 但显示侧仍按"naive = 上海时间"提取，导致 deadline 少 8 小时（如用户选 10:00
 * → DB 存 02:00 UTC → 显示成 02:00 而非 10:00）。
 */
export function formatRawDateTime(dateString: string): string {
  if (!dateString) return '';
  const s = String(dateString).trim().replace(' ', 'T');
  // 先补 Z 当 UTC 解析，再按本地时区显示
  const hasTz = /([+-]\d{2}:?\d{2}|Z)$/.test(s);
  const d = new Date(hasTz ? s : `${s}Z`);
  if (isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function formatDateTime(dateString: string): string {
  const d = parseUtcDate(dateString);
  if (!d) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 统一日期时间格式：「2026/08/12 09:37」，24h 制 */
export function formatDateTimeShort(dateString: string): string {
  const d = parseUtcDate(dateString);
  if (!d) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 完整日期时间格式（24h 制，精确到秒）：「2026/08/12 09:37:45」 */
export function formatDateTimeFull(dateString: string): string {
  const d = parseUtcDate(dateString);
  if (!d) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function formatTime(dateString: string): string {
  if (!dateString) return '刚刚';
  const date = parseUtcDate(dateString);
  if (!date) return '刚刚';

  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return '刚刚';
  if (diffMins < 60) return `${diffMins}分钟前`;
  if (diffHours < 24) return `${diffHours}小时前`;
  if (diffDays < 7) return `${diffDays}天前`;

  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
