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

export function buildWechatAuthUrl(state: string): string {
  const redirectUri = `${window.location.origin}${WECHAT_CONFIG.redirectPath}`;
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
  const date = new Date(dateString);
  if (isNaN(date.getTime())) return '';
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
