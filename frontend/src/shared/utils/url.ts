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

export function formatDateTime(dateString: string): string {
  if (!dateString) return '';
  const d = new Date(dateString);
  if (isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 统一日期时间格式：「2026/08/12 09:37」，24h 制 */
export function formatDateTimeShort(dateString: string): string {
  if (!dateString) return '';
  const d = new Date(dateString);
  if (isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 最晚解决时间选择器值：有值用原值，无值默认当天 9:00（格式 YYYY-MM-DD HH:00） */
export function deadlinePickerValue(iso?: string): string {
  const d = iso ? new Date(iso) : new Date();
  if (iso && isNaN(d.getTime())) return '';
  if (!iso) d.setHours(9, 0, 0, 0);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:00`;
}

export function formatTime(dateString: string): string {
  if (!dateString) return '刚刚';
  const date = new Date(dateString);
  if (isNaN(date.getTime())) return '刚刚';

  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return '刚刚';
  if (diffMins < 60) return `${diffMins}分钟前`;
  if (diffHours < 24) return `${diffHours}小时前`;
  if (diffDays < 7) return `${diffDays}天前`;

  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}
