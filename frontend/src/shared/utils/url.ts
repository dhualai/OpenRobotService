// URL 工具函数 - 从 HelpDesk urlUtils.js 移植
import { WECHAT_CONFIG } from '@/config/wechat';
import { formatBackendTime, parseBackendDate } from '@/shared/utils/time';
import { persistAuthTokens, writeStored } from '@/stores/authStorage';

export function getUrlParams(): boolean {
  try {
    const url = new URL(window.location.href);
    return url.searchParams.get('debug') === 'true';
  } catch {
    return false;
  }
}

function storeTokens(token: string, refreshToken: string): void {
  // 写入当前环境命名空间 key（t_/p_），避免 OAuth 回跳 token 串到另一环境
  persistAuthTokens(token, refreshToken, Date.now() + 28 * 60 * 1000);
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
          writeStored('USERNAME', username);
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
 * 统一委托 `@/shared/utils/time` 的 `parseBackendDate`（唯一解析入口，兼容 string/number）。
 *
 * 语义：后端 DB naive DateTime 列统一存 UTC（见 backend/app/core/db.py `_ensure_utc_session`），
 * 无时区 ISO 字符串补 Z 当 UTC 解析、由浏览器按本地时区 +8 转换；带时区后缀原样解析。
 * 不写死 +8，跨时区浏览器同样正确。
 */
export function parseUtcDate(dateString: string): Date | null {
  return parseBackendDate(dateString);
}

/**
 * 格式化 deadline_at 等字段为「YYYY/MM/DD HH:mm」本地时区字符串。
 * 统一委托 `formatBackendTime`（内部走 parseBackendDate）。与 formatDateTime 输出一致。
 *
 * 修复背景（2026-08-25）：原实现直接提取年月日时分显示，假设 naive = 上海时间；
 * c2ebf96 时区根因治理后 DB 存的是 UTC，导致 deadline 少 8 小时（选 10:00 → 存 02:00 UTC → 显示 02:00）。
 */
export function formatRawDateTime(dateString: string): string {
  return formatBackendTime(dateString);
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
